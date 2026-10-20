import contextlib
import importlib.metadata as _im
import logging
import os
import tempfile
import threading
import time
import types
import wave

import numpy as np
import sounddevice as sd
from PySide6 import QtCore

NEMOTRON_MODEL_KEYS = ("nemotron-3.5-streaming", "nvidia/nemotron-3.5-asr-streaming-0.6b")
NEMOTRON_HF_REPO = "nvidia/nemotron-3.5-asr-streaming-0.6b"

_TRANSFORMERS_SHIMMED = False


def _ensure_transformers_nemotron_ready():
    """
    Ensure PyTorch 2.5.x compatibility shims are in place before importing
    Transformers >= 5.13 `AutoModelForRNNT` and `AutoProcessor` for Nemotron 3.5 ASR.
    Must run BEFORE `faster_whisper` / `ctranslate2` imports `transformers`.
    """
    global _TRANSFORMERS_SHIMMED
    if _TRANSFORMERS_SHIMMED:
        return
    _TRANSFORMERS_SHIMMED = True

    orig_ver = _im.version

    def _patched_ver(name):
        v = orig_ver(name)
        if name == "torch" and v.startswith("2.5."):
            return "2.6.0+cu121"
        return v

    _im.version = _patched_ver

    try:
        import torch

        if not hasattr(torch, "accelerator"):
            torch.accelerator = types.SimpleNamespace(
                is_available=lambda: torch.cuda.is_available(),
                current_accelerator=lambda *a, **k: torch.device("cuda") if torch.cuda.is_available() else None,
                device_count=lambda: torch.cuda.device_count() if torch.cuda.is_available() else 0,
                current_device_index=lambda: torch.cuda.current_device() if torch.cuda.is_available() else 0,
                set_device_index=lambda i: torch.cuda.set_device(i) if torch.cuda.is_available() else None,
                empty_cache=lambda: torch.cuda.empty_cache() if torch.cuda.is_available() else None,
                synchronize=lambda *a, **k: torch.cuda.synchronize() if torch.cuda.is_available() else None,
            )

        import torch.distributed.fsdp as _fsdp
        for _attr in ("CPUOffloadPolicy", "MixedPrecisionPolicy", "FSDPModule", "fully_shard"):
            if not hasattr(_fsdp, _attr):
                setattr(_fsdp, _attr, object)

        import torch._dynamo._trace_wrapped_higher_order_op as _twh
        if not hasattr(_twh, "TransformGetItemToIndex"):
            _twh.TransformGetItemToIndex = contextlib.nullcontext

        import transformers.masking_utils as _mu
        _mu._vmap_expansion_sdpa = lambda mask_fn: (
            lambda b, h, q, kv: mask_fn(*_mu._non_vmap_expansion_sdpa(b, h, q, kv)).expand(
                b.shape[0], h.shape[0], q.shape[0], kv.shape[0]
            )
        )
    except Exception as e:
        logging.debug(f"Transformers shim notice: {e}")


# Apply shims before importing faster_whisper (which imports ctranslate2 -> transformers)
_ensure_transformers_nemotron_ready()
from faster_whisper import WhisperModel  # noqa: E402


class AudioTranscriber(QtCore.QObject):
    """
    Handles microphone recording (via sounddevice), real-time live streaming preview,
    and audio transcription supporting two engines:
      1. NVIDIA Nemotron 3.5 ASR Streaming 0.6B (`nvidia/nemotron-3.5-asr-streaming-0.6b`) — Primary / Default
      2. Faster-Whisper (`large-v3`, `medium`, `small`, `base`, `tiny`) — Selectable in Settings
    Emits Qt signals so the UI can show real-time audio levels, vocal FFT spectrum,
    live streaming partial transcription while speaking, and final transcription.
    """
    recording_started = QtCore.Signal()
    recording_stopped = QtCore.Signal(str)          # emits path to recorded wav
    audio_level_signal = QtCore.Signal(float)       # 0.0 to 1.0 RMS amplitude
    audio_spectrum_signal = QtCore.Signal(dict)     # {rms, bass, mid, treble, pitch, pitch_hz, bands}
    partial_transcript_signal = QtCore.Signal(str)  # live streaming partial transcript while speaking
    status_changed = QtCore.Signal(str)             # human-readable status in PT-BR
    transcription_finished = QtCore.Signal(str)     # raw transcribed text
    transcription_error = QtCore.Signal(str)        # error message

    def __init__(
        self,
        parent=None,
        default_model_size="nemotron-3.5-streaming",
        device_index=None,
        whisper_device="auto",
        live_streaming_enabled=True,
    ):
        super().__init__(parent)
        self.model_size = default_model_size or "nemotron-3.5-streaming"
        self.whisper_device = whisper_device or "auto"
        self.device_index = device_index
        self.live_streaming_enabled = bool(live_streaming_enabled)

        # Faster-Whisper state
        self.model = None
        # Nemotron 3.5 ASR state
        self.nemotron_model = None
        self.nemotron_processor = None

        self._loaded_signature = None  # (model_size, whisper_device)
        self._model_lock = threading.Lock()
        self._is_loading_model = False

        # Recording state
        self.is_recording = False
        self.sample_rate = 16000
        self.channels = 1
        self._frames = []
        self._stream = None
        self._record_start_time = 0.0
        self._fft_ring = np.zeros(2048, dtype=np.float32)
        self._smooth_pitch = 0.35
        self._live_partial_text = ""

    def is_nemotron_selected(self) -> bool:
        return (self.model_size or "").strip().lower() in NEMOTRON_MODEL_KEYS

    @staticmethod
    def list_input_devices():
        """
        Return a deduplicated list of available input microphone devices as:
        [(device_index, display_name), ...], plus (None, "Padrão do Sistema").
        Prefers Windows DirectSound or MME for broad sample-rate compatibility.
        """
        result = [(None, "🎙️ Padrão do Sistema")]
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            seen_names = set()

            preferred_order = []
            for idx, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) <= 0:
                    continue
                h_idx = dev.get('hostapi', 0)
                h_name = hostapis[h_idx]['name'] if h_idx < len(hostapis) else ''
                if 'DirectSound' in h_name:
                    prio = 0
                elif 'MME' in h_name:
                    prio = 1
                elif 'WASAPI' in h_name:
                    prio = 2
                else:
                    prio = 3
                preferred_order.append((prio, idx, dev, h_name))

            preferred_order.sort(key=lambda x: (x[0], x[1]))

            for prio, idx, dev, h_name in preferred_order:
                raw_name = (dev.get('name') or f"Device {idx}").strip()
                if any(skip in raw_name.lower() for skip in ('mapeador de som', 'sound mapper', 'driver de captura', 'primary sound capture')):
                    continue
                clean_key = raw_name.lower()[:28]
                if clean_key in seen_names:
                    continue
                seen_names.add(clean_key)
                result.append((idx, f"🎙️ {raw_name}"))
        except Exception as e:
            logging.error(f"Error querying audio input devices: {e}")
        return result

    def set_device(self, device_index):
        """Set the active microphone device index (None = system default)."""
        self.device_index = device_index
        logging.info(f"Microphone device set to index: {device_index}")

    def configure_whisper(self, model_size: str, whisper_device: str = "auto", reload_async: bool = True):
        """
        Update the ASR model (Nemotron 3.5 Streaming or Faster-Whisper) and device/compute mode.
        If changed, resets the cached model and optionally preloads the new one in background.
        """
        new_size = (model_size or "nemotron-3.5-streaming").strip()
        new_dev = (whisper_device or "auto").strip()
        changed = (new_size != self.model_size) or (new_dev != self.whisper_device)
        self.model_size = new_size
        self.whisper_device = new_dev

        if changed:
            logging.info(f"ASR configuration updated: model={self.model_size}, device_mode={self.whisper_device}")
            with self._model_lock:
                self.model = None
                self.nemotron_model = None
                self.nemotron_processor = None
                self._loaded_signature = None
            if reload_async:
                self.preload_model_async()

    def get_active_badge_text(self) -> str:
        """Return a short human-readable description of the configured ASR model & device."""
        dev_map = {
            "auto": "Auto GPU/CPU",
            "cuda_float16": "CUDA float16",
            "cuda_int8": "CUDA int8",
            "cpu_int8": "CPU int8",
        }
        dev_label = dev_map.get(self.whisper_device, "CUDA float16")
        if self.is_nemotron_selected():
            return f"Nemotron 3.5 Streaming ({dev_label})"
        return f"Whisper {self.model_size} ({dev_label})"

    def preload_model_async(self, model_size=None, whisper_device=None):
        """Preload the selected ASR model in a background thread so transcription is instant."""
        if model_size:
            self.model_size = model_size
        if whisper_device:
            self.whisper_device = whisper_device
        threading.Thread(target=self._ensure_model_loaded, daemon=True).start()

    def _resolve_device_and_compute(self):
        """Return (device, compute_type) tuple based on `self.whisper_device`."""
        mode = (self.whisper_device or "auto").lower()
        if mode == "cuda_float16":
            return "cuda", "float16"
        elif mode == "cuda_int8":
            return "cuda", "int8_float16"
        elif mode == "cpu_int8":
            return "cpu", "int8"
        else:
            return "auto", "auto"

    def _ensure_model_loaded(self):
        with self._model_lock:
            target_sig = (self.model_size, self.whisper_device)
            if self._loaded_signature == target_sig and (self.nemotron_model is not None or self.model is not None):
                return self.nemotron_model if self.is_nemotron_selected() and self.nemotron_model is not None else self.model

            self._is_loading_model = True
            req_dev, req_compute = self._resolve_device_and_compute()

            try:
                if self.is_nemotron_selected():
                    try:
                        _ensure_transformers_nemotron_ready()
                        import torch
                        from transformers import AutoModelForRNNT, AutoProcessor

                        use_cuda = (req_dev != "cpu") and torch.cuda.is_available()
                        target_device = "cuda" if use_cuda else "cpu"
                        target_dtype = torch.float16 if use_cuda else torch.float32

                        self.status_changed.emit(f"Carregando Nemotron 3.5 Streaming ({target_device.upper()})...")
                        logging.info(f"Loading NVIDIA Nemotron 3.5 ASR Streaming '{NEMOTRON_HF_REPO}' on {target_device} ({target_dtype})...")

                        try:
                            self.nemotron_processor = AutoProcessor.from_pretrained(
                                NEMOTRON_HF_REPO, local_files_only=True
                            )
                            self.nemotron_model = AutoModelForRNNT.from_pretrained(
                                NEMOTRON_HF_REPO, dtype=target_dtype, local_files_only=True
                            ).to(target_device).eval()
                        except Exception:
                            self.nemotron_processor = AutoProcessor.from_pretrained(NEMOTRON_HF_REPO)
                            self.nemotron_model = AutoModelForRNNT.from_pretrained(
                                NEMOTRON_HF_REPO, dtype=target_dtype
                            ).to(target_device).eval()

                        self.model = None
                        self._loaded_signature = target_sig
                        logging.info(f"NVIDIA Nemotron 3.5 ASR Streaming loaded on {target_device} ({target_dtype}).")
                        return self.nemotron_model
                    except Exception as nem_err:
                        logging.warning(
                            f"Failed to load Nemotron 3.5 ASR ({nem_err}); falling back to Faster-Whisper large-v3...",
                            exc_info=True
                        )
                        self.nemotron_model = None
                        self.nemotron_processor = None

                # Faster-Whisper loader (either explicitly selected or fallback)
                fw_size = "large-v3" if self.is_nemotron_selected() else self.model_size
                if req_dev == "cpu":
                    self.status_changed.emit(f"Carregando Whisper ({fw_size}) na CPU...")
                    logging.info(f"Loading faster-whisper '{fw_size}' on CPU (int8)...")
                    self.model = WhisperModel(
                        fw_size,
                        device="cpu",
                        compute_type="int8"
                    )
                    self._loaded_signature = target_sig
                    logging.info(f"faster-whisper '{fw_size}' loaded on CPU int8.")
                else:
                    compute_to_use = "float16" if req_compute == "auto" else req_compute
                    self.status_changed.emit(f"Carregando Whisper ({fw_size}) na GPU...")
                    logging.info(f"Loading faster-whisper '{fw_size}' on CUDA ({compute_to_use})...")
                    try:
                        self.model = WhisperModel(
                            fw_size,
                            device="cuda",
                            compute_type=compute_to_use
                        )
                        self._loaded_signature = target_sig
                        logging.info(f"faster-whisper '{fw_size}' loaded on CUDA {compute_to_use}.")
                    except Exception as cuda_err:
                        logging.warning(f"CUDA ({compute_to_use}) failed ({cuda_err}), falling back to CPU int8...")
                        self.status_changed.emit(f"GPU indisponível, carregando Whisper ({fw_size}) na CPU...")
                        fallback_size = fw_size if fw_size in ("tiny", "base", "small") else "base"
                        self.model = WhisperModel(
                            fallback_size,
                            device="cpu",
                            compute_type="int8"
                        )
                        self._loaded_signature = target_sig
                        logging.info(f"faster-whisper '{fallback_size}' loaded on CPU int8 fallback.")
            finally:
                self._is_loading_model = False

            return self.nemotron_model if self.nemotron_model is not None else self.model

    # ─── AUDIO DECODING & RESAMPLING HELPERS ─────────────────────────────────

    @staticmethod
    def _resample_to_16k(audio_float32: np.ndarray, orig_sr: int) -> np.ndarray:
        if orig_sr == 16000 or len(audio_float32) == 0:
            return audio_float32
        duration = len(audio_float32) / float(orig_sr)
        target_len = max(1, int(round(duration * 16000)))
        x_old = np.linspace(0.0, 1.0, num=len(audio_float32), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        return np.interp(x_new, x_old, audio_float32).astype(np.float32)

    @classmethod
    def _load_audio_file_16k_mono(cls, file_path: str) -> np.ndarray:
        """
        Decode any audio file (.wav, .ogg, .mp3, .m4a, .opus, .flac) into a 1D
        16000 Hz mono float32 numpy array normalized to [-1.0, 1.0].
        Uses PyAV (`av`) first for broad format support, with `wave` fallback for PCM WAV.
        """
        try:
            import av
            container = av.open(file_path)
            resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=16000)
            chunks = []
            for frame in container.decode(audio=0):
                resampled_frames = resampler.resample(frame)
                if not isinstance(resampled_frames, list):
                    resampled_frames = [resampled_frames] if resampled_frames is not None else []
                for rf in resampled_frames:
                    arr = rf.to_ndarray()
                    if arr.ndim > 1:
                        arr = arr[0]
                    chunks.append(arr.astype(np.float32))
            # Flush resampler
            try:
                flushed = resampler.resample(None)
                if not isinstance(flushed, list):
                    flushed = [flushed] if flushed is not None else []
                for rf in flushed:
                    arr = rf.to_ndarray()
                    if arr.ndim > 1:
                        arr = arr[0]
                    chunks.append(arr.astype(np.float32))
            except Exception:
                pass
            container.close()
            if chunks:
                return np.concatenate(chunks, axis=0)
        except Exception as av_err:
            logging.debug(f"PyAV decode notice ({av_err}), trying wave fallback...")

        with wave.open(file_path, "rb") as wf:
            sr = wf.getframerate()
            n_ch = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if n_ch > 1:
                pcm = pcm.reshape(-1, n_ch).mean(axis=1)
            return cls._resample_to_16k(pcm, sr)

    def _transcribe_nemotron_16k(self, audio_16k: np.ndarray, language: str = "pt") -> str:
        """
        Transcribe a 16kHz mono float32 audio array using NVIDIA Nemotron 3.5 ASR Streaming.
        Supports both short utterances and long recordings (via 24-second chunking).
        """
        import torch

        proc = self.nemotron_processor
        model = self.nemotron_model
        if proc is None or model is None:
            return ""

        lang_code = "pt-BR" if (not language or language.lower().startswith("pt")) else language
        if lang_code not in getattr(proc, "prompt_dictionary", {}):
            lang_code = "auto"

        # Process in segments of up to 24 seconds (384,000 samples) to keep GPU memory & attention fast
        chunk_samples = 16000 * 24
        total_samples = len(audio_16k)
        if total_samples < 1600:  # < 0.1s
            return ""

        texts = []
        for start in range(0, total_samples, chunk_samples):
            segment = audio_16k[start:start + chunk_samples]
            if len(segment) < 1600:
                continue
            # Pad very short segments (<0.4s) to 0.4s so subsampling convs have enough frames
            if len(segment) < 6400:
                segment = np.pad(segment, (0, 6400 - len(segment)), mode="constant")

            inputs = proc(
                segment,
                sampling_rate=16000,
                language=lang_code,
                return_tensors="pt"
            )
            inputs = inputs.to(model.device, dtype=model.dtype)
            with torch.inference_mode():
                output = model.generate(**inputs, return_dict_in_generate=True)
            decoded_list = proc.decode(output.sequences, skip_special_tokens=True)
            seg_text = (decoded_list[0] if isinstance(decoded_list, list) and decoded_list else str(decoded_list)).strip()
            if seg_text:
                texts.append(seg_text)

        return " ".join(texts).strip()

    # ─── REAL-TIME LIVE STREAMING PREVIEW WHILE SPEAKING ─────────────────────

    def _start_live_streaming_worker(self):
        """
        Background worker that runs while `self.is_recording` is True and emits
        `partial_transcript_signal` every ~0.55s as the user speaks.
        Works with both NVIDIA Nemotron 3.5 ASR Streaming (primary) and Faster-Whisper!
        """
        def _stream_loop():
            last_frame_count = 0
            while self.is_recording:
                time.sleep(0.52)
                if not self.is_recording or not self.live_streaming_enabled:
                    continue

                frames_snapshot = list(self._frames)
                n_frames = len(frames_snapshot)
                if n_frames == 0 or n_frames == last_frame_count:
                    continue

                # Avoid blocking if model is still loading on startup
                if self._is_loading_model:
                    continue

                try:
                    raw_int16 = np.concatenate(frames_snapshot, axis=0)[:, 0]
                    sr = int(self.sample_rate or 16000)
                    duration_s = len(raw_int16) / float(sr)
                    if duration_s < 0.45:
                        continue

                    # Keep a rolling window of the last 14 seconds for ultra-low-latency live preview
                    max_window_samples = sr * 14
                    if len(raw_int16) > max_window_samples:
                        raw_int16 = raw_int16[-max_window_samples:]

                    audio_f32 = raw_int16.astype(np.float32) / 32768.0
                    # Skip if audio is pure silence
                    if float(np.sqrt(np.mean(audio_f32 ** 2))) < 0.004:
                        continue

                    audio_16k = self._resample_to_16k(audio_f32, sr)
                    last_frame_count = n_frames

                    # Try non-blocking lock acquisition for live preview
                    if not self._model_lock.acquire(blocking=False):
                        continue
                    try:
                        partial_txt = ""
                        if self.is_nemotron_selected() and self.nemotron_model is not None:
                            partial_txt = self._transcribe_nemotron_16k(audio_16k, language="pt-BR")
                        elif self.model is not None:
                            segments, _ = self.model.transcribe(
                                audio_16k,
                                beam_size=1,
                                best_of=1,
                                language="pt",
                                vad_filter=True,
                                condition_on_previous_text=False,
                            )
                            partial_txt = " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
                    finally:
                        self._model_lock.release()

                    if partial_txt and self.is_recording and partial_txt != self._live_partial_text:
                        self._live_partial_text = partial_txt
                        self.partial_transcript_signal.emit(partial_txt)
                except Exception as stream_err:
                    logging.debug(f"Live streaming preview notice: {stream_err}")

        threading.Thread(target=_stream_loop, daemon=True).start()

    # ─── MICROPHONE RECORDING ────────────────────────────────────────────────

    def start_recording(self):
        """Start capturing microphone audio via sounddevice and launch live streaming preview."""
        if self.is_recording:
            return

        self._frames = []
        self._live_partial_text = ""
        self.is_recording = True
        self._record_start_time = time.time()
        self._fft_ring = np.zeros(2048, dtype=np.float32)

        def audio_callback(indata, frames, time_info, status):
            if status:
                logging.debug(f"sounddevice status: {status}")
            if not self.is_recording:
                return
            self._frames.append(indata.copy())
            samples = indata[:, 0].astype(np.float32)
            rms = float(np.sqrt(np.mean(samples ** 2)))
            norm_level = min(1.0, rms / 2200.0)
            self.audio_level_signal.emit(norm_level)

            n_in = len(samples)
            if n_in >= 2048:
                self._fft_ring[:] = samples[-2048:]
            else:
                self._fft_ring[:-n_in] = self._fft_ring[n_in:]
                self._fft_ring[-n_in:] = samples

            try:
                sr = float(self.sample_rate or 16000)
                windowed = self._fft_ring * np.hanning(2048)
                spec = np.abs(np.fft.rfft(windowed)) / 2048.0
                freqs = np.fft.rfftfreq(2048, d=1.0 / sr)

                bass_mask = (freqs >= 75.0) & (freqs < 250.0)
                mid_mask = (freqs >= 250.0) & (freqs < 1000.0)
                treble_mask = (freqs >= 1000.0) & (freqs <= 4200.0)

                bass_raw = float(np.sqrt(np.mean(spec[bass_mask] ** 2))) if np.any(bass_mask) else 0.0
                mid_raw = float(np.sqrt(np.mean(spec[mid_mask] ** 2))) if np.any(mid_mask) else 0.0
                treble_raw = float(np.sqrt(np.mean(spec[treble_mask] ** 2))) if np.any(treble_mask) else 0.0

                norm_bass = min(1.0, bass_raw / 42.0)
                norm_mid = min(1.0, mid_raw / 28.0)
                norm_treble = min(1.0, treble_raw / 12.0)

                band_edges = [80, 130, 200, 310, 480, 750, 1200, 2100, 3800]
                bands = []
                scales = [45.0, 42.0, 36.0, 30.0, 24.0, 18.0, 13.0, 9.0]
                for bi in range(8):
                    m = (freqs >= band_edges[bi]) & (freqs < band_edges[bi + 1])
                    val = float(np.sqrt(np.mean(spec[m] ** 2))) if np.any(m) else 0.0
                    bands.append(min(1.0, val / scales[bi]))

                voice_mask = (freqs >= 80.0) & (freqs <= 1100.0)
                dom_hz = 160.0
                if norm_level > 0.03 and np.any(voice_mask):
                    v_freqs = freqs[voice_mask]
                    v_spec = spec[voice_mask]
                    peak_idx = int(np.argmax(v_spec))
                    peak_hz = float(v_freqs[peak_idx])
                    weights = v_spec ** 1.5 + 1e-6
                    centroid_hz = float(np.exp(np.sum(np.log(v_freqs) * weights) / np.sum(weights)))
                    dom_hz = 0.55 * peak_hz + 0.45 * centroid_hz
                    raw_pitch = (np.log2(max(85.0, min(600.0, dom_hz))) - np.log2(85.0)) / (np.log2(520.0) - np.log2(85.0))
                    raw_pitch = float(max(0.0, min(1.0, raw_pitch)))
                    self._smooth_pitch = 0.65 * self._smooth_pitch + 0.35 * raw_pitch

                self.audio_spectrum_signal.emit({
                    "rms": norm_level,
                    "bass": norm_bass,
                    "mid": norm_mid,
                    "treble": norm_treble,
                    "pitch": float(self._smooth_pitch),
                    "pitch_hz": float(dom_hz),
                    "bands": bands,
                })
            except Exception as fft_err:
                logging.debug(f"FFT calc notice: {fft_err}")

        try:
            target_sr = 16000
            try:
                sd.check_input_settings(device=self.device_index, channels=self.channels, dtype="int16", samplerate=target_sr)
            except Exception:
                dev_info = sd.query_devices(self.device_index, 'input')
                target_sr = int(dev_info.get('default_samplerate', 44100))

            self.sample_rate = target_sr
            self._stream = sd.InputStream(
                device=self.device_index,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=audio_callback,
                blocksize=1024
            )
            self._stream.start()
            self.recording_started.emit()
            self.status_changed.emit("Ouvindo... (Espaço para concluir)")
            if self.live_streaming_enabled:
                self._start_live_streaming_worker()
            logging.info(f"Microphone recording started (device={self.device_index}, sr={self.sample_rate}).")
        except Exception as e:
            self.is_recording = False
            logging.error(f"Failed to start microphone recording: {e}", exc_info=True)
            self.transcription_error.emit(f"Erro ao acessar o microfone: {e}")

    def cancel_recording(self):
        """Silently stop and discard any ongoing recording without emitting errors."""
        if not self.is_recording:
            return
        self.is_recording = False
        self._live_partial_text = ""
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception as e:
            logging.debug(f"Error closing stream on cancel: {e}")
        self._frames = []

    def stop_recording(self) -> str | None:
        """Stop capturing microphone audio and write a temporary WAV file."""
        if not self.is_recording:
            return None

        self.is_recording = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception as e:
            logging.error(f"Error closing audio stream: {e}")

        if not self._frames:
            self.transcription_error.emit("Nenhum áudio capturado.")
            return None

        try:
            audio_data = np.concatenate(self._frames, axis=0)
            duration = len(audio_data) / float(self.sample_rate)
            if duration < 0.25:
                self.transcription_error.emit("Gravação muito curta.")
                return None

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="wt_mic_")
            os.close(tmp_fd)

            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)  # int16 = 2 bytes
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())

            logging.info(f"Saved microphone recording ({duration:.1f}s) to {tmp_path}")
            self.recording_stopped.emit(tmp_path)
            return tmp_path
        except Exception as e:
            logging.error(f"Failed to save recorded audio: {e}", exc_info=True)
            self.transcription_error.emit(f"Erro ao salvar gravação: {e}")
            return None

    # ─── FILE & FINAL TRANSCRIPTION (NEMOTRON 3.5 STREAMING / FASTER-WHISPER) ───

    def transcribe_file_async(self, file_path: str, delete_after: bool = False, language: str = "pt"):
        """Run Nemotron 3.5 ASR Streaming or Faster-Whisper transcription in a background thread."""
        def _worker():
            try:
                self._ensure_model_loaded()
                if self.is_nemotron_selected() and self.nemotron_model is not None:
                    self.status_changed.emit("Transcrevendo com Nemotron 3.5 Streaming...")
                    logging.info(f"Transcribing audio file with Nemotron 3.5 ASR: {file_path}")
                    audio_16k = self._load_audio_file_16k_mono(file_path)
                    with self._model_lock:
                        raw_text = self._transcribe_nemotron_16k(audio_16k, language=language)
                    # Fallback to live partial text if final pass was empty on a very short clip
                    if not raw_text and self._live_partial_text:
                        raw_text = self._live_partial_text.strip()
                    logging.info(f"Nemotron 3.5 transcription complete (len={len(raw_text)}): {raw_text[:80]}...")
                else:
                    self.status_changed.emit(f"Transcrevendo áudio com Whisper ({self.model_size})...")
                    logging.info(f"Transcribing audio file with Faster-Whisper ({self.model_size}): {file_path}")
                    with self._model_lock:
                        segments, info = self.model.transcribe(
                            file_path,
                            beam_size=5,
                            language=language if language else None,
                            vad_filter=True,
                            vad_parameters=dict(min_silence_duration_ms=400)
                        )
                        text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
                        raw_text = " ".join(text_parts).strip()
                    logging.info(f"Faster-Whisper transcription complete (lang={info.language}, len={len(raw_text)}): {raw_text[:80]}...")

                self._live_partial_text = ""
                if not raw_text:
                    self.transcription_error.emit("Não foi possível detectar fala no áudio.")
                    return

                self.transcription_finished.emit(raw_text)
            except Exception as e:
                logging.error(f"Transcription error: {e}", exc_info=True)
                self.transcription_error.emit(f"Erro na transcrição: {e}")
            finally:
                if delete_after and file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

        threading.Thread(target=_worker, daemon=True).start()

    def transcribe_file_sync(self, file_path: str, language: str = "pt") -> str:
        """
        Synchronously transcribe an audio file (e.g., WhatsApp .ogg) on a caller's worker thread
        without emitting microphone popup signals. Returns the raw transcribed text.
        """
        self._ensure_model_loaded()
        if self.is_nemotron_selected() and self.nemotron_model is not None:
            logging.info(f"Transcribing audio file (sync, Nemotron 3.5 ASR): {file_path}")
            audio_16k = self._load_audio_file_16k_mono(file_path)
            with self._model_lock:
                return self._transcribe_nemotron_16k(audio_16k, language=language)

        logging.info(f"Transcribing audio file (sync, Faster-Whisper {self.model_size}): {file_path}")
        with self._model_lock:
            segments, _ = self.model.transcribe(
                file_path,
                beam_size=5,
                language=language if language else None,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400)
            )
            text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
            return " ".join(text_parts).strip()
