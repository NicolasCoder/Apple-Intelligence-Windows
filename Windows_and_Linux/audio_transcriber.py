import logging
import os
import tempfile
import threading
import time
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from PySide6 import QtCore


class AudioTranscriber(QtCore.QObject):
    """
    Handles microphone recording (via sounddevice) and audio transcription
    (via faster-whisper on CUDA or CPU, with configurable model size and precision).
    Emits Qt signals so the UI can show real-time audio levels and status updates.
    """
    recording_started = QtCore.Signal()
    recording_stopped = QtCore.Signal(str)          # emits path to recorded wav
    audio_level_signal = QtCore.Signal(float)       # 0.0 to 1.0 RMS amplitude
    audio_spectrum_signal = QtCore.Signal(dict)     # {rms, bass, mid, treble, pitch, pitch_hz, bands}
    status_changed = QtCore.Signal(str)             # human-readable status in PT-BR
    transcription_finished = QtCore.Signal(str)     # raw transcribed text
    transcription_error = QtCore.Signal(str)        # error message

    def __init__(
        self,
        parent=None,
        default_model_size="large-v3",
        device_index=None,
        whisper_device="auto"
    ):
        super().__init__(parent)
        self.model_size = default_model_size or "large-v3"
        self.whisper_device = whisper_device or "auto"
        self.device_index = device_index
        self.model = None
        self._loaded_signature = None  # (model_size, actual_device, actual_compute_type)
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

            # Prefer DirectSound (1) or MME (0) first as they support automatic
            # OS sample-rate conversion to 16000 Hz on Windows.
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
                # Skip generic Microsoft mapper duplicates
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
        Update the Whisper model size and device/compute mode.
        If changed, resets the cached model and optionally preloads the new one in background.
        """
        new_size = (model_size or "large-v3").strip()
        new_dev = (whisper_device or "auto").strip()
        changed = (new_size != self.model_size) or (new_dev != self.whisper_device)
        self.model_size = new_size
        self.whisper_device = new_dev

        if changed:
            logging.info(f"Whisper configuration updated: model={self.model_size}, device_mode={self.whisper_device}")
            with self._model_lock:
                self.model = None
                self._loaded_signature = None
            if reload_async:
                self.preload_model_async()

    def get_active_badge_text(self) -> str:
        """Return a short human-readable description of the configured Whisper model & device."""
        dev_map = {
            "auto": "Auto GPU/CPU",
            "cuda_float16": "CUDA float16",
            "cuda_int8": "CUDA int8",
            "cpu_int8": "CPU int8",
        }
        dev_label = dev_map.get(self.whisper_device, "CUDA float16")
        return f"Whisper {self.model_size} ({dev_label})"

    def preload_model_async(self, model_size=None, whisper_device=None):
        """Preload the Whisper model in a background thread so transcription is instant."""
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
            # "auto": try CUDA float16 first, fallback to CPU int8
            return "auto", "auto"

    def _ensure_model_loaded(self):
        with self._model_lock:
            target_sig = (self.model_size, self.whisper_device)
            if self.model is not None and self._loaded_signature == target_sig:
                return self.model

            self._is_loading_model = True
            req_dev, req_compute = self._resolve_device_and_compute()

            try:
                if req_dev == "cpu":
                    self.status_changed.emit(f"Carregando Whisper ({self.model_size}) na CPU...")
                    logging.info(f"Loading faster-whisper '{self.model_size}' on CPU (int8)...")
                    self.model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8"
                    )
                    self._loaded_signature = target_sig
                    logging.info(f"faster-whisper '{self.model_size}' loaded on CPU int8.")
                else:
                    compute_to_use = "float16" if req_compute == "auto" else req_compute
                    self.status_changed.emit(f"Carregando Whisper ({self.model_size}) na GPU...")
                    logging.info(f"Loading faster-whisper '{self.model_size}' on CUDA ({compute_to_use})...")
                    try:
                        self.model = WhisperModel(
                            self.model_size,
                            device="cuda",
                            compute_type=compute_to_use
                        )
                        self._loaded_signature = target_sig
                        logging.info(f"faster-whisper '{self.model_size}' loaded on CUDA {compute_to_use}.")
                    except Exception as cuda_err:
                        logging.warning(f"CUDA ({compute_to_use}) failed ({cuda_err}), falling back to CPU int8...")
                        self.status_changed.emit(f"GPU indisponível, carregando Whisper ({self.model_size}) na CPU...")
                        fallback_size = self.model_size if self.model_size in ("tiny", "base", "small") else "base"
                        self.model = WhisperModel(
                            fallback_size,
                            device="cpu",
                            compute_type="int8"
                        )
                        self._loaded_signature = target_sig
                        logging.info(f"faster-whisper '{fallback_size}' loaded on CPU int8 fallback.")
            finally:
                self._is_loading_model = False

            return self.model

    def start_recording(self):
        """Start capturing microphone audio via sounddevice."""
        if self.is_recording:
            return

        self._frames = []
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
            # Compute normalized RMS level for waveform animation (more sensitive for normal speech)
            rms = float(np.sqrt(np.mean(samples ** 2)))
            norm_level = min(1.0, rms / 2200.0)
            self.audio_level_signal.emit(norm_level)

            # Update rolling 2048-sample buffer for high-resolution vocal FFT
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

                # Extract Grave (75-250 Hz), Médio (250-1000 Hz), Agudo (1000-4200 Hz)
                bass_mask = (freqs >= 75.0) & (freqs < 250.0)
                mid_mask = (freqs >= 250.0) & (freqs < 1000.0)
                treble_mask = (freqs >= 1000.0) & (freqs <= 4200.0)

                bass_raw = float(np.sqrt(np.mean(spec[bass_mask] ** 2))) if np.any(bass_mask) else 0.0
                mid_raw = float(np.sqrt(np.mean(spec[mid_mask] ** 2))) if np.any(mid_mask) else 0.0
                treble_raw = float(np.sqrt(np.mean(spec[treble_mask] ** 2))) if np.any(treble_mask) else 0.0

                norm_bass = min(1.0, bass_raw / 42.0)
                norm_mid = min(1.0, mid_raw / 28.0)
                norm_treble = min(1.0, treble_raw / 12.0)

                # 8 logarithmic vocal bands from 80 Hz to 3800 Hz
                band_edges = [80, 130, 200, 310, 480, 750, 1200, 2100, 3800]
                bands = []
                scales = [45.0, 42.0, 36.0, 30.0, 24.0, 18.0, 13.0, 9.0]
                for bi in range(8):
                    m = (freqs >= band_edges[bi]) & (freqs < band_edges[bi + 1])
                    val = float(np.sqrt(np.mean(spec[m] ** 2))) if np.any(m) else 0.0
                    bands.append(min(1.0, val / scales[bi]))

                # Estimate vocal pitch (0.0 = grave ~85Hz, 1.0 = agudo ~650Hz+)
                voice_mask = (freqs >= 80.0) & (freqs <= 1100.0)
                dom_hz = 160.0
                if norm_level > 0.03 and np.any(voice_mask):
                    v_freqs = freqs[voice_mask]
                    v_spec = spec[voice_mask]
                    # Harmonic Product Spectrum (downsample 2x & 3x) to lock onto fundamental f0 note
                    peak_idx = int(np.argmax(v_spec))
                    peak_hz = float(v_freqs[peak_idx])
                    # Spectral centroid in log-frequency space
                    weights = v_spec ** 1.5 + 1e-6
                    centroid_hz = float(np.exp(np.sum(np.log(v_freqs) * weights) / np.sum(weights)))
                    dom_hz = 0.55 * peak_hz + 0.45 * centroid_hz
                    # Map 85 Hz .. 520 Hz logarithmically to 0.0 .. 1.0
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
            # Determine supported sample rate (prefer 16000, fallback to device default)
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

    def transcribe_file_async(self, file_path: str, delete_after: bool = False, language: str = "pt"):
        """Run faster-whisper transcription in a background thread."""
        def _worker():
            try:
                model = self._ensure_model_loaded()
                self.status_changed.emit(f"Transcrevendo áudio com Whisper ({self.model_size})...")
                logging.info(f"Transcribing audio file: {file_path} (model={self.model_size})")

                segments, info = model.transcribe(
                    file_path,
                    beam_size=5,
                    language=language if language else None,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=400)
                )

                text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
                raw_text = " ".join(text_parts).strip()

                logging.info(f"Transcription complete (lang={info.language}, len={len(raw_text)}): {raw_text[:80]}...")
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
        model = self._ensure_model_loaded()
        logging.info(f"Transcribing audio file (sync): {file_path} (model={self.model_size})")
        segments, info = model.transcribe(
            file_path,
            beam_size=5,
            language=language if language else None,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400)
        )
        text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
        return " ".join(text_parts).strip()

