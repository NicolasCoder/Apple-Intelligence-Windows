import math
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ui.UIUtils import colorMode

# Pitch-reactive color palettes (RGB float32):
# 1) GRAVE (Low pitch / Bass): Deep Indigo -> Royal Violet -> Crimson Magenta
_PAL_BASS = np.array([
    (88, 86, 214),    # Deep Indigo
    (138, 68, 242),   # Royal Violet
    (215, 50, 120),   # Deep Crimson Rose
    (94, 92, 230),    # Apple Indigo
    (88, 86, 214),
], dtype=np.float32)

# 2) MÉDIO (Mid vocal register): Apple Intelligence Purple -> Blue -> Coral Pink -> Warm Amber
_PAL_MID = np.array([
    (168, 130, 255),  # Lavender Purple
    (94, 175, 255),   # Siri Blue
    (255, 105, 180),  # Vibrant Pink
    (255, 165, 100),  # Warm Peach/Orange
    (168, 130, 255),
], dtype=np.float32)

# 3) AGUDO (High pitch / Treble): Electric Cyan -> Aqua -> Neon Rose -> Solar Gold
_PAL_TREBLE = np.array([
    (100, 210, 255),  # Electric Cyan
    (90, 245, 220),   # Mint Aqua
    (255, 130, 210),  # Bright Rose
    (255, 214, 60),   # Solar Gold
    (100, 210, 255),
], dtype=np.float32)


def _sample_stops(stops: np.ndarray, n: int = 256) -> np.ndarray:
    pal = np.zeros((n, 3), dtype=np.float32)
    seg = len(stops) - 1
    for i in range(n):
        t = i / n * seg
        k = int(t)
        f = t - k
        f = f * f * (3.0 - 2.0 * f)
        pal[i] = stops[k] * (1.0 - f) + stops[k + 1] * f
    return pal


_LUT_BASS = _sample_stops(_PAL_BASS)
_LUT_MID = _sample_stops(_PAL_MID)
_LUT_TREBLE = _sample_stops(_PAL_TREBLE)


class ThinkingOrb(QtWidgets.QWidget):
    """
    Pitch & Frequency-Reactive 3D Dotted Sphere inspired by 'thinking-orbs' (Jakub Antalik)
    and Apple Intelligence / Siri.

    Features:
      - Authentic latitude/longitude ring geometry from `thinking-orbs` (`frameWave` & `frameGlobe` & `frameBraid`)
      - Real-time FFT Spectral & Pitch Reactivity (`push_spectrum`):
          * GRAVE (Low pitch / Bass 75-250Hz): Heavy, deep radial expansion (l=2 harmonic swell),
            larger dots, slower/heavier axial sway, Deep Indigo/Violet/Crimson palette.
          * MÉDIO (Mid voice 250-1000Hz): Smooth multi-lobed harmonic waves (l=3,4) + 8-band
            equalizer mapped across latitude rings, Apple Intelligence Iridescent palette.
          * AGUDO (High pitch / Treble 1000-4200Hz): Fast, crystalline high-frequency ripples
            (l=6,8 + longitudinal star-waves), Electric Cyan/Aqua/Gold palette.
      - Working / Transcribing states:
          * Transcribing: `thinking-orbs` Globe Scan Meridian sweeping across the sphere
          * Refining (AI): `thinking-orbs` 3-strand Weaving Braid + Orbit particles
    """

    def __init__(self, parent=None, size=118, dots=320):
        super().__init__(parent)
        self.setFixedHeight(size)
        self.setMinimumWidth(size)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Build structured latitude rings (like thinking-orbs frameWave / frameGlobe)
        # so vocal frequency bands map cleanly to rings from south to north pole
        lat_rings = 15
        lon_density = 38
        xs, ys, zs, lats, lons, ring_ids = [], [], [], [], [], []
        for ri in range(lat_rings + 1):
            lat = -math.pi / 2.0 + (ri / lat_rings) * math.pi
            cos_lat = math.cos(lat)
            sin_lat = math.sin(lat)
            lon_count = max(1, round(abs(cos_lat) * lon_density))
            for lj in range(lon_count):
                lon = (lj / lon_count) * 2.0 * math.pi
                xs.append(cos_lat * math.cos(lon))
                ys.append(sin_lat)
                zs.append(cos_lat * math.sin(lon))
                lats.append(lat)
                lons.append(lon)
                ring_ids.append(ri / lat_rings)  # 0.0 .. 1.0

        self._x = np.array(xs, dtype=np.float32)
        self._y = np.array(ys, dtype=np.float32)
        self._z = np.array(zs, dtype=np.float32)
        self._lat = np.array(lats, dtype=np.float32)
        self._lon = np.array(lons, dtype=np.float32)
        self._ring_u = np.array(ring_ids, dtype=np.float32)
        self._hue = ((self._lon % (2.0 * math.pi)) / (2.0 * math.pi)).astype(np.float32)

        # Logical state
        self._mode = "idle"  # "idle" | "listening" | "working"

        # Smoothed audio & spectral parameters
        self._level_target = 0.0
        self._level = 0.0
        self._bass_t = 0.0
        self._bass = 0.0
        self._mid_t = 0.0
        self._mid = 0.0
        self._treble_t = 0.0
        self._treble = 0.0
        self._pitch_t = 0.38   # 0.0 = grave, 0.5 = médio, 1.0 = agudo
        self._pitch = 0.38
        self._bands_t = np.zeros(8, dtype=np.float32)
        self._bands = np.zeros(8, dtype=np.float32)

        # Animation kinematics
        self._spin = 0.32
        self._spin_t = 0.32
        self._scan = 0.0
        self._scan_t = 0.0
        self._angle = 0.0
        self._wave_phase = 0.0
        self._clock = 0.0
        self._last = time.perf_counter()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)  # ~60 FPS
        self._timer.timeout.connect(self._tick)

    # ── Public API ───────────────────────────────────────────────────────
    def set_state(self, recording=False, processing=False):
        if recording:
            self._mode = "listening"
            self._spin_t = 0.42
            self._scan_t = 0.0
        elif processing:
            self._mode = "working"
            self._spin_t = 1.35
            self._scan_t = 1.0
            self._level_target = 0.0
            self._bass_t = self._mid_t = self._treble_t = 0.0
            self._bands_t[:] = 0.0
        else:
            self._mode = "idle"
            self._spin_t = 0.28
            self._scan_t = 0.0
            self._level_target = 0.0
            self._bass_t = self._mid_t = self._treble_t = 0.0
            self._bands_t[:] = 0.0
        self._ensure_running()

    def push_level(self, rms_level: float):
        if self._mode != "listening":
            return
        self._level_target = max(self._level_target * 0.5, min(1.0, rms_level * 1.9))

    def push_spectrum(self, spec: dict):
        """
        Receive real-time FFT spectral analysis from AudioTranscriber:
        {rms, bass, mid, treble, pitch, pitch_hz, bands: [8 floats]}
        """
        if self._mode != "listening" or not spec:
            return
        rms = float(spec.get("rms", 0.0))
        self._level_target = max(self._level_target * 0.45, min(1.0, rms * 1.9))
        self._bass_t = max(self._bass_t * 0.45, min(1.0, float(spec.get("bass", 0.0)) * 1.6))
        self._mid_t = max(self._mid_t * 0.45, min(1.0, float(spec.get("mid", 0.0)) * 1.6))
        self._treble_t = max(self._treble_t * 0.45, min(1.0, float(spec.get("treble", 0.0)) * 1.7))
        if rms > 0.025:
            self._pitch_t = float(spec.get("pitch", self._pitch_t))
        bands = spec.get("bands")
        if bands and len(bands) == 8:
            b_arr = np.clip(np.array(bands, dtype=np.float32) * 1.65, 0.0, 1.0)
            self._bands_t = np.maximum(self._bands_t * 0.4, b_arr)

    # ── Lifecycle ────────────────────────────────────────────────────────
    def _ensure_running(self):
        if self.isVisible() and not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        self._ensure_running()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    # ── Animation Step ───────────────────────────────────────────────────
    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, max(0.001, now - self._last))
        self._last = now
        self._clock += dt

        k_slow = 1.0 - math.exp(-dt * 6.0)
        k_fast = 1.0 - math.exp(-dt * 18.0)
        k_pitch = 1.0 - math.exp(-dt * 10.0)

        self._spin += (self._spin_t - self._spin) * k_slow
        self._scan += (self._scan_t - self._scan) * k_slow
        self._level += (self._level_target - self._level) * k_fast
        self._bass += (self._bass_t - self._bass) * k_fast
        self._mid += (self._mid_t - self._mid) * k_fast
        self._treble += (self._treble_t - self._treble) * k_fast
        self._pitch += (self._pitch_t - self._pitch) * k_pitch
        self._bands += (self._bands_t - self._bands) * k_fast

        # Natural decay when silent
        decay = math.exp(-dt * 5.5)
        self._level_target *= decay
        self._bass_t *= decay
        self._mid_t *= decay
        self._treble_t *= decay
        self._bands_t *= decay

        # Dynamic rotation & wave speed modulated by pitch (agudo spins/ripples faster, grave is deep & heavy)
        pitch_speed_mul = 0.75 + 0.95 * self._pitch
        active_spin = self._spin * (1.0 + 0.55 * self._level * pitch_speed_mul)
        self._angle += active_spin * dt
        self._wave_phase += dt * (2.6 + 6.5 * self._pitch + 3.2 * self._level)
        self.update()

    def _blend_palette_lut(self) -> np.ndarray:
        """
        Interpolate between _LUT_BASS (pitch=0.0), _LUT_MID (pitch=0.45), and _LUT_TREBLE (pitch=1.0).
        """
        p = max(0.0, min(1.0, self._pitch))
        if p < 0.45:
            w = p / 0.45
            return _LUT_BASS * (1.0 - w) + _LUT_MID * w
        else:
            w = (p - 0.45) / 0.55
            return _LUT_MID * (1.0 - w) + _LUT_TREBLE * w

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        t = self._clock

        # Grave (bass) makes the entire orb swell deeply; Agudo (treble) keeps radius tighter with crisp ripples
        bass_swell = 0.16 * self._bass
        breath = 1.0 + 0.03 * math.sin(t * 1.7) + bass_swell
        R = min(w, h) / 2.0 * 0.76 * breath

        lut = self._blend_palette_lut()

        # Soft ambient Siri/Apple Intelligence aura behind the sphere
        aura_color = lut[int((t * 25) % 256)]
        glow_alpha = int(min(135, 32 + 75 * self._level + 35 * self._bass + 28 * self._scan))
        grad = QtGui.QRadialGradient(cx, cy, R * 1.5)
        grad.setColorAt(0.0, QtGui.QColor(int(aura_color[0]), int(aura_color[1]), int(aura_color[2]), glow_alpha))
        grad.setColorAt(0.55, QtGui.QColor(int(aura_color[0]), int(aura_color[1]), int(aura_color[2]), glow_alpha // 3))
        grad.setColorAt(1.0, QtGui.QColor(int(aura_color[0]), int(aura_color[1]), int(aura_color[2]), 0))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QBrush(grad))
        p.drawEllipse(QtCore.QPointF(cx, cy), R * 1.5, R * 1.5)

        # 8-band equalizer interpolation along the sphere's latitude rings (0..1 -> bands 0..7)
        band_pos = self._ring_u * 7.0
        b_idx = np.clip(band_pos.astype(np.int32), 0, 6)
        b_frac = band_pos - b_idx
        ring_band_energy = self._bands[b_idx] * (1.0 - b_frac) + self._bands[b_idx + 1] * b_frac

        # Pitch-dependent spherical harmonic wave deformation:
        # - Grave (low pitch): l=2 (deep bi-polar / equatorial heavy pulse)
        # - Médio (mid pitch): l=3,4 flowing waves
        # - Agudo (high pitch): l=7,9 rapid crystalline ripples + longitudinal star harmonics
        wp = self._wave_phase
        grave_wave = self._bass * 0.28 * np.cos(self._lat * 2.0 - wp * 0.65)
        mid_wave = self._mid * 0.24 * (
            0.65 * np.sin(self._lat * 3.5 - wp * 1.0)
            + 0.35 * np.cos(self._lon * 2.0 + wp * 0.8)
        )
        agudo_wave = self._treble * 0.26 * (
            0.55 * np.sin(self._lat * 8.0 - wp * 1.65)
            + 0.45 * np.sin(self._lon * 5.0 + self._lat * 4.0 + wp * 1.9)
        )
        eq_wave = ring_band_energy * 0.18 * np.sin(self._lon * 3.0 + wp)

        # Idle organic wave (thinking-orbs frameWave baseline)
        idle_wave = 0.035 * (
            0.62 * np.sin(t * 2.1 - self._ring_u * 7.8)
            + 0.38 * np.sin(t * 1.27 + self._ring_u * 12.4)
        )

        disp = 1.0 + idle_wave + grave_wave + mid_wave + agudo_wave + eq_wave

        # Rotate around Y and tilt around X (tilt reacts subtly to pitch)
        ca, sa = math.cos(self._angle), math.sin(self._angle)
        x1 = self._x * ca + self._z * sa
        z1 = -self._x * sa + self._z * ca
        tilt = 0.38 + 0.08 * math.sin(t * 0.5) + 0.14 * (self._pitch - 0.4) * self._level
        ct, st = math.cos(tilt), math.sin(tilt)
        y2 = self._y * ct - z1 * st
        z2 = self._y * st + z1 * ct

        px = cx + x1 * R * disp
        py = cy + y2 * R * disp
        depth = np.clip((z2 + 1.0) * 0.5, 0.0, 1.0)

        # Scan meridian (for working / transcribing state, from thinking-orbs frameGlobe)
        lon_rot = np.arctan2(z1, x1)
        d_ang = np.angle(np.exp(1j * (lon_rot - t * 2.6)))
        scan_boost = np.exp(-(d_ang / 0.45) ** 2) * np.maximum(0.0, z2) * self._scan

        # Crest highlight on vocal wave peaks
        crest = np.clip((disp - 1.0) * 2.8, 0.0, 1.0)

        # Color lookup per dot with smooth latitude + hue shift
        color_phase = (self._hue + self._ring_u * 0.35 + t * 0.06) % 1.0
        idx = (color_phase * 255).astype(np.int32)
        rgb = lut[idx].copy()

        # Brighten dots on wave crests and scan meridian
        highlight = np.clip(scan_boost * 0.7 + crest * 0.45 + self._treble * 0.25, 0.0, 1.0)
        rgb = rgb + (255.0 - rgb) * highlight[:, None]
        if colorMode != "dark":
            rgb = rgb * 0.88

        # Dot radius: Grave makes dots fuller/heavier; Agudo makes crests sparkle
        base_r = max(1.05, R * 0.042) * (1.0 + 0.22 * self._bass - 0.08 * self._treble)
        order = np.argsort(z2)  # painter's algorithm: back to front

        for n in order:
            dp = float(depth[n])
            b = float(scan_boost[n])
            cr = float(crest[n])
            rad = base_r * (0.42 + 0.78 * dp) * (1.0 + 0.75 * b + 0.38 * cr)
            alpha = int(min(255, 48 + 205 * dp + 45 * b + 30 * cr))
            r, gg, bb = rgb[n]
            p.setBrush(QtGui.QColor(int(r), int(gg), int(bb), alpha))
            p.drawEllipse(QtCore.QPointF(float(px[n]), float(py[n])), rad, rad)

        p.end()
