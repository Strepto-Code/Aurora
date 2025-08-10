import numpy as np
import math
from PySide6.QtCore import Qt, QTimer, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QImage, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

class RTVisualizerWidget(QWidget):
    def __init__(self, audio_engine, start_timer=True):
        super().__init__()
        # Offscreen/export rendering guard
        self._offscreen_paint = False  # toggled True only during offscreen QImage rendering
        # Background assets for GUI (QPixmap) and export (QImage)
        self._bg_img = None  # QImage copy used for offscreen/export to avoid QPixmap in non-GUI threads
# Engine & state
        self.audio = audio_engine
        self._mode = "Waveform - Linear"
        # Colors & gradient-by-amplitude
        self.color = (0.2, 0.8, 1.0)
        self._grad_a = QColor(32, 255, 255)
        self._grad_b = QColor(255, 0, 255)
        self._grad_curve = "linear"
        self._grad_min = 0.0
        self._grad_max = 1.0
        self._amp_ema = 0.0
        self._amp_alpha = 0.2

        # Sensitivities
        self.waveform_sensitivity = 1.0
        self.feather_sensitivity = 2.0

        # Radial/spectrum controls
        self.radial_rotation_deg = 0.0
        self.radial_mirror = False
        self.radial_smooth = False
        self.radial_smooth_amount = 50
        self.radial_wave_smoothness = 50
        self.radial_temporal_alpha = 0.3
        self._radial_prev = None

        # Particles (simple demo mode)
        self.particle_density = 200
        self.particle_speed = 1.0
        self.particle_glow = 0.6

        # Background
        self._bg_path = None
        self._bg_pix = None
        self._bg_scale_mode = "fill"  # fill=cover, fit=contain, stretch, center, tile
        self._bg_off = (0, 0)
        self._bg_dim = 0  # 0..100

        # Shadows
        self._shadow_enabled = False
        self._shadow_opacity = 0.6  # 0..1
        self._shadow_blur = 16
        self._shadow_distance = 8
        self._shadow_angle_deg = 45
        self._shadow_spread = 6

        # Radial interior fill
        self._fill_enabled = False
        self._fill_color = QColor(255, 255, 255, 48)
        self._fill_blend = "normal"
        self._fill_threshold = 0.1

        # Center image + feather mask
        self.center_image = None
        self.center_motion = 0         # 0..100
        self.feather_enabled = False
        self.edge_waviness = 30        # 0..100
        self.feather_noise = 30
        self.feather_audio_enabled = False
        self.feather_audio_amount = 40 # 0..100

        # HUD / FPS
        self._hud = True
        self._fps_cap = 60
        self._last_paint_t = None
        self._fps_smoothed = 0.0

        # Timing
        self._phase = 0.0

        # Backbuffer
        self._img = None

        # Timer
        self._timer = QTimer(self) if start_timer else None
        if self._timer is not None:
            self._timer.setTimerType(Qt.PreciseTimer)
            self._timer.timeout.connect(self.update)
            self._update_timer()
        self.setMinimumSize(400, 300)

    # ---------- Public API ----------
    def set_mode(self, mode): self._mode = str(mode); self.update()

    # Sensitivities
    def set_waveform_sensitivity(self, s):
        try:
            self.waveform_sensitivity = max(0.05, float(s))
        except Exception:
            self.waveform_sensitivity = 1.0
        self.update()

    def set_feather_sensitivity(self, s):
        try:
            self.feather_sensitivity = max(0.05, float(s))
        except Exception:
            self.feather_sensitivity = 2.0
        self.update()

    # Back-compat alias
    def set_sensitivity(self, s):
        self.set_waveform_sensitivity(s)

    # Radial/spectrum config
    def set_radial_rotation_deg(self, deg):
        try: self.radial_rotation_deg = float(deg)
        except Exception: self.radial_rotation_deg = 0.0
        self.update()

    def set_radial_mirror(self, on):
        self.radial_mirror = bool(on); self.update()

    # Color/direct
    def set_color(self, rgb):
        self.color = tuple(float(x) for x in rgb); self.update()

    # FPS / HUD / Safe Mode
    def set_fps_cap(self, fps: int):
        try: fps = int(fps)
        except Exception: fps = 60
        self._fps_cap = max(1, fps)
        self._update_timer()
        self.update()

    def set_hud_enabled(self, on: bool):
        self._hud = bool(on); self.update()

    def set_safe_mode(self, on: bool):
        if on:
            self._shadow_blur = min(self._shadow_blur, 6)
        self.update()

    # Background
    def set_background_config(self, cfg):
        # Always set thread-safe QImage first; create QPixmap only if in GUI thread.
        try:
            path = cfg.path if getattr(cfg, 'path', None) else None
            if path:
                try:
                    # Normalize to absolute to avoid CWD differences during export
                    import os
                    path = os.path.abspath(str(path))
                except Exception:
                    pass
            self._bg_path = path
            self._bg_scale_mode = getattr(cfg, 'scale_mode', 'fill')
            try:
                ox = int(getattr(cfg, 'offset_x', 0)); oy = int(getattr(cfg, 'offset_y', 0))
            except Exception:
                ox, oy = 0, 0
            self._bg_off = (ox, oy)
            try:
                self._bg_dim = int(getattr(cfg, 'dim_percent', 0))
            except Exception:
                self._bg_dim = 0
            # Thread-safe image for offscreen/export
            self._bg_img = QImage(self._bg_path) if self._bg_path else None
            # GUI-only pixmap (avoid in worker/export threads)
            try:
                from PySide6.QtCore import QThread, QCoreApplication
                app = QCoreApplication.instance()
                in_gui_thread = bool(app and app.thread() == QThread.currentThread())
            except Exception:
                in_gui_thread = False
            if self._bg_path:
                if in_gui_thread:
                    try:
                        self._bg_pix = QPixmap(self._bg_path)
                    except Exception:
                        self._bg_pix = None
                else:
                    # Avoid QPixmap in non-GUI/export threads
                    self._bg_pix = None
            else:
                # No path => always clear both image and pixmap
                self._bg_pix = None
        except Exception:
            # Last resort: do not crash if config couldn't be applied
            pass
        self.update()
    def set_shadow_enabled(self, enabled: bool):
        self._shadow_enabled = bool(enabled); self.update()

    def set_shadow_opacity(self, pct: int):
        try: self._shadow_opacity = max(0.0, min(1.0, pct/100.0))
        except Exception: self._shadow_opacity = 0.6
        self.update()

    def set_shadow_blur_radius(self, r: int):
        try: self._shadow_blur = int(max(0, r))
        except Exception: self._shadow_blur = 16
        self.update()

    def set_shadow_distance(self, dist: int):
        try: self._shadow_distance = int(max(0, dist))
        except Exception: self._shadow_distance = 8
        self.update()

    # Radial fill
    def set_radial_fill_enabled(self, on: bool):
        self._fill_enabled = bool(on); self.update()

    def set_radial_fill_color(self, rgba_hex: str):
        c = QColor(rgba_hex)
        if c.isValid(): self._fill_color = c
        self.update()

    def set_radial_fill_blend(self, mode: str):
        self._fill_blend = mode or "normal"; self.update()

    def set_radial_fill_threshold(self, t: float):
        try: self._fill_threshold = float(max(0.0, min(1.0, t)))
        except Exception: self._fill_threshold = 0.1
        self.update()

    # Amplitude → Gradient
    def set_gradient_colors(self, a_hex: str|None, b_hex: str|None):
        if a_hex:
            ca = QColor(a_hex); 
            if ca.isValid(): self._grad_a = ca
        if b_hex:
            cb = QColor(b_hex);
            if cb.isValid(): self._grad_b = cb
        self.update()

    def set_gradient_curve(self, name: str):
        self._grad_curve = (name or "linear").lower(); self.update()

    def set_gradient_clamp(self, amin: float, amax: float):
        self._grad_min = float(min(amin, amax))
        self._grad_max = float(max(amin, amax)); self.update()

    def set_gradient_smoothing(self, alpha: float):
        self._amp_alpha = float(max(0.0, min(1.0, alpha))); self.update()

    # Center image / feather
    def set_center_image(self, image_path: str):
        try:
            img = QImage(image_path)
            if img.isNull():
                return False
            self.center_image = img
            try:
                self.center_image_path = image_path
            except Exception:
                pass
            self.update()
            return True
        except Exception:
            return False

    def clear_center_image(self):
        self.center_image = None; self.update()

    def set_feather_enabled(self, enabled: bool):
        self.feather_enabled = bool(enabled); self.update()

    def set_waviness(self, value: int):
        try: v = int(value)
        except Exception: v = 0
        v = max(0, min(100, v))
        self.edge_waviness = v
        self.feather_noise = v
        self.update()

    def set_feather_noise(self, value: int):
        try: v = int(value)
        except Exception: v = 0
        v = max(0, min(100, v))
        self.feather_noise = v
        self.edge_waviness = v
        self.update()

    def set_feather_audio_enabled(self, enabled: bool):
        self.feather_audio_enabled = bool(enabled); self.update()

    def set_feather_audio_amount(self, value: int):
        try: v = int(value)
        except Exception: v = 0
        self.feather_audio_amount = max(0, min(100, v)); self.update()

    def set_center_motion(self, value: int):
        try: v = int(value)
        except Exception: v = 0
        self.center_motion = max(0, min(100, v)); self.update()

    def set_edge_waviness(self, value: int):
        self.set_waviness(value)

    # Misc UI
    def reset_time(self): self._phase = 0.0

    def set_radial_smooth(self, on: bool):
        self.radial_smooth = bool(on); self.update()

    def set_particle_density(self, v: int):
        try: self.particle_density = int(max(10, min(2000, v)))
        except Exception: self.particle_density = 200
        self.update()

    def set_particle_speed(self, v: int):
        try: self.particle_speed = float(max(0.1, min(5.0, v/50.0)))
        except Exception: self.particle_speed = 1.0
        self.update()

    def set_particle_glow(self, v: int):
        try: self.particle_glow = float(max(0.0, min(2.0, v/50.0)))
        except Exception: self.particle_glow = 0.6
        self.update()

    def set_radial_smooth_amount(self, v: int):
        try: v = int(v)
        except Exception: v = 0
        self.radial_smooth_amount = max(0, min(100, v)); self.update()

    # ---------- Timing ----------
    def _update_timer(self):
        if self._timer is None:
            return
        interval_ms = max(5, int(round(1000.0 / float(max(1, self._fps_cap)))))
        if self._timer.isActive(): self._timer.stop()
        self._timer.start(interval_ms)

    # ---------- Helpers ----------
    def _amp_to_color(self, amp: float) -> QColor:
        try:
            # EMA smoothing
            self._amp_ema = self._amp_alpha * self._amp_ema + (1.0 - self._amp_alpha) * float(amp)
            t = (self._amp_ema - self._grad_min) / max(1e-6, (self._grad_max - self._grad_min))
            # curve
            t = max(0.0, min(1.0, t))
            if self._grad_curve == 'ease-in':
                t = t * t
            elif self._grad_curve == 'ease-out':
                t = 1.0 - (1.0 - t) * (1.0 - t)
            elif self._grad_curve == 'smoothstep':
                t = t * t * (3 - 2 * t)
            # lerp
            r = int(self._grad_a.red()   + t * (self._grad_b.red()   - self._grad_a.red()))
            g = int(self._grad_a.green() + t * (self._grad_b.green() - self._grad_a.green()))
            b = int(self._grad_a.blue()  + t * (self._grad_b.blue()  - self._grad_a.blue()))
            return QColor(r, g, b)
        except Exception:
            return QColor(255,255,255)

    

    def _draw_background(self, p: QPainter):
            try:
                if self._offscreen_paint:
                    # Use QImage path for thread-safe painting during export
                    if not self._bg_img or self._bg_img.isNull():
                        return
                    ow, oh = (self._offscreen_size if getattr(self, '_offscreen_size', None) else (self.width(), self.height()))
                    mode = (self._bg_scale_mode or "fill").lower()
                    if mode == 'tile':
                        w, h = self._bg_img.width(), self._bg_img.height()
                        for y in range(0, oh, h):
                            for x in range(0, ow, w):
                                p.drawImage(x + self._bg_off[0], y + self._bg_off[1], self._bg_img)
                    else:
                        if mode == 'fit':
                            scaled = self._bg_img.scaled(QSize(ow, oh), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        elif mode == 'stretch':
                            scaled = self._bg_img.scaled(QSize(ow, oh), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                        elif mode == 'center':
                            scaled = self._bg_img
                        else:  # fill => cover
                            scaled = self._bg_img.scaled(QSize(ow, oh), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                        x = (ow - scaled.width()) // 2 + self._bg_off[0]
                        y = (oh - scaled.height()) // 2 + self._bg_off[1]
                        p.drawImage(x, y, scaled)
                    d = max(0, min(100, int(self._bg_dim)))
                    if d > 0:
                        p.fillRect(0, 0, ow, oh, QColor(0,0,0, int(255 * (d/100.0))))
                    return
                # GUI path (QPixmap)
                if not self._bg_pix or self._bg_pix.isNull():
                    return
                mode = (self._bg_scale_mode or "fill").lower()
                if mode == 'tile':
                    w, h = self._bg_pix.width(), self._bg_pix.height()
                    for y in range(0, self.height(), h):
                        for x in range(0, self.width(), w):
                            p.drawPixmap(x + self._bg_off[0], y + self._bg_off[1], self._bg_pix)
                else:
                    if mode == 'fit':
                        scaled = self._bg_pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    elif mode == 'stretch':
                        scaled = self._bg_pix.scaled(self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    elif mode == 'center':
                        scaled = self._bg_pix
                    else:  # fill => cover
                        scaled = self._bg_pix.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    x = (self.width() - scaled.width()) // 2 + self._bg_off[0]
                    y = (self.height() - scaled.height()) // 2 + self._bg_off[1]
                    p.drawPixmap(x, y, scaled)
                d = max(0, min(100, int(self._bg_dim)))
                if d > 0:
                    p.fillRect(self.rect(), QColor(0,0,0, int(255 * (d/100.0))))
            except Exception:
                pass
    def _draw_shadow_path(self, p: QPainter, path: QPainterPath):
        if not p.isActive():
            return
        if not getattr(self, "_shadow_enabled", False) or getattr(self, "_shadow_opacity", 0.0) <= 0.0:
            return
        a = int(255 * float(getattr(self, "_shadow_opacity", 0.6)))
        sc = QColor(0,0,0,a)
        ang_deg = float(getattr(self, "_shadow_angle_deg", 45)) if hasattr(self, "_shadow_angle_deg") else 45.0
        dist = float(max(0.0, getattr(self, "_shadow_distance", 8)))
        dx = dist * math.cos(math.radians(ang_deg)); dy = dist * math.sin(math.radians(ang_deg))
        p.save(); p.translate(dx, dy)
        penw = max(2, int(getattr(self, "_shadow_blur", 12)))
        pen = QPen(sc); pen.setWidth(penw); p.setPen(pen)
        p.drawPath(path)
        p.restore()


    def _draw_hud(self, p: QPainter):
        if not self._hud:
            return
        import time
        now = time.monotonic()
        if self._last_paint_t is not None:
            dt = max(1e-4, now - self._last_paint_t)
            fps = 1.0 / dt
            self._fps_smoothed = (0.9 * self._fps_smoothed + 0.1 * fps) if self._fps_smoothed > 0 else fps
        self._last_paint_t = now
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(10, 20, f"FPS: {self._fps_smoothed:.1f}")

    # ---------- Painting ----------
    def paintEvent(self, ev):
        w = max(1, self.width())
        h = max(1, self.height())

        if self._img is None or self._img.width() != w or self._img.height() != h:
            self._img = QImage(w, h, QImage.Format_RGB888)

        # pull latest audio frame (non-blocking)
        samples, spectrum, _ = self.audio.get_frame()
        energy = float(np.clip(np.mean(np.abs(samples)) * self.waveform_sensitivity, 0.0, 1.0))

        self._phase = (self._phase + 0.04 * (0.5 + energy)) % (2 * np.pi)

        p = QPainter(self._img)
        p.fillRect(0, 0, w, h, QColor(13, 13, 18))
        self._draw_background(p)

        try:
            pen = QPen(self._amp_to_color(energy))
        except Exception:
            r, g, b = [int(255*c) for c in self.color]
            pen = QPen(QColor(r, g, b))
        pen.setWidth(2)
        p.setPen(pen)

        mode = self._mode
        if mode.startswith("Waveform"):
            self._draw_waveform(p, w, h, samples, energy, pen=pen)
        elif mode.startswith("Spectrum"):
            radial = ("Radial" in mode)
            self._draw_spectrum(p, w, h, spectrum, energy, radial=radial, pen=pen)
            if radial:
                self._draw_center_image_and_mask(p, w, h, energy, spectrum)
        else:
            self._draw_particles(p, w, h, spectrum, energy)

        p.end()

        # composite
        painter = QPainter(self)
        painter.drawImage(0, 0, self._img)
        self._draw_hud(painter)
        painter.end()

    # ---------- Primitives ----------
    def _draw_waveform(self, p, w, h, samples, energy, pen=None):
        n = len(samples)
        if n <= 1:
            return
        if "Circular" in self._mode:
            cx, cy = w/2.0, h/2.0
            base_r = min(w, h) * 0.28
            amp = min(w, h) * 0.22 * (0.5 + 0.6*energy)
            segs = min(720, n)
            step = max(1, int(n / segs))
            path = QPainterPath()
            first = True
            for i in range(0, n, step):
                v = float(samples[i])
                a = 2.0*np.pi*(i / max(1, n-1)) - (np.pi/2.0) + np.deg2rad(self.radial_rotation_deg)
                r = base_r + v * amp
                x = cx + np.cos(a) * r
                y = cy + np.sin(a) * r
                if first:
                    path.moveTo(x, y); first = False
                else:
                    path.lineTo(x, y)
            path.closeSubpath()
            if self._fill_enabled and energy >= self._fill_threshold:
                p.save()
                if self._fill_blend == 'add':
                    p.setCompositionMode(QPainter.CompositionMode_Plus)
                elif self._fill_blend == 'multiply':
                    p.setCompositionMode(QPainter.CompositionMode_Multiply)
                p.fillPath(path, self._fill_color)
                p.restore()
            if self._shadow_enabled:
                self._draw_shadow_path(p, path)
            p.drawPath(path)
        else:
            amp = 0.45 * (h/2) * (0.5 + energy)
            mid = h/2.0
            step = max(1, int(n / w))
            if self._shadow_enabled and self._shadow_opacity > 0.0:
                sp = QPen(QColor(0,0,0,int(180*self._shadow_opacity))); sp.setWidth(3)
                p.save(); p.translate(self._shadow_distance, self._shadow_distance); p.setPen(sp)
                last_y = int(mid)
                for x in range(w):
                    i = int((x / max(1, w-1)) * (n-1))
                    y = int(mid - samples[i] * amp)
                    p.drawLine(x, last_y, x, y)
                    last_y = y
                p.restore()
                if pen: p.setPen(pen)
            # Draw per pixel to fully span width
            last_y = int(mid)
            for x in range(w):
                i = int((x / max(1, w-1)) * (n-1))
                y = int(mid - samples[i] * amp)
                p.drawLine(x, last_y, x, y)
                last_y = y

    def _draw_spectrum(self, p, w, h, spectrum, energy, radial=False, pen=None):
        N = 80
        if spectrum is None or len(spectrum) == 0:
            return
        spec = spectrum[:N]
        mx = float(np.max(spec)) if np.max(spec) > 0 else 1.0
        spec = spec / mx
        if radial:
            cx, cy = w/2.0, h/2.0
            inner = min(w, h) * 0.22
            span = min(w, h) * 0.28 * (0.6 + 0.6*energy)
            spec_draw = np.concatenate([spec, spec[::-1][1:-1]]) if self.radial_mirror else spec
            L = len(spec_draw)
            # Spatial smoothing (new)
            if L > 3 and getattr(self, 'radial_wave_smoothness', 0) > 0:
                spec_draw = self._smooth_closed(spec_draw, int(self.radial_wave_smoothness))
                L = len(spec_draw)
            # Temporal EMA smoothing
            alpha = getattr(self, 'radial_temporal_alpha', 0.0)
            if alpha > 0.0:
                if self._radial_prev is None or len(self._radial_prev) != L:
                    self._radial_prev = spec_draw.copy()
                else:
                    self._radial_prev = alpha * self._radial_prev + (1.0 - alpha) * spec_draw
                spec_draw = self._radial_prev
            if self.radial_smooth and L > 3:
                spec_draw = self._smooth_closed(spec_draw, int(self.radial_smooth_amount))
                L = len(spec_draw)
            path = QPainterPath()
            for i in range(L+1):
                a = 2.0*np.pi*(i / L) - (np.pi/2.0) + np.deg2rad(self.radial_rotation_deg)
                v = float(spec_draw[min(i, L-1)])
                r = inner + span * v
                x = cx + np.cos(a) * r
                y = cy + np.sin(a) * r
                if i == 0: path.moveTo(x, y)
                else: path.lineTo(x, y)
            path.closeSubpath()
            if self._fill_enabled and energy >= self._fill_threshold:
                p.save()
                if self._fill_blend == 'add':
                    p.setCompositionMode(QPainter.CompositionMode_Plus)
                elif self._fill_blend == 'multiply':
                    p.setCompositionMode(QPainter.CompositionMode_Multiply)
                p.fillPath(path, self._fill_color)
                p.restore()
            if self._shadow_enabled:
                self._draw_shadow_path(p, path)
            p.drawPath(path)
        else:
            bar_w = max(1, int(w / N))
            for i in range(N):
                v = float(spec[i])
                bh = int(v * (h-10) * (0.4 + 0.6*energy))
                x0 = i * bar_w
                p.drawLine(x0, h-5, x0, h-5-bh)

    def _draw_particles(self, p, w, h, spectrum, energy):
        N = 50
        if spectrum is None or len(spectrum) == 0:
            return
        spec = spectrum[:N]
        spec = spec / (np.max(spec)+1e-6)
        for i in range(N):
            v = float(spec[i])
            x = int((i+0.5)/N * w)
            y = int(h/2 + (v-0.5) * h * 0.6)
            p.drawPoint(x, y)

    def _draw_center_image_and_mask(self, p, w, h, energy, spectrum=None):
        if spectrum is None or len(spectrum) == 0:
            return
        cx, cy = w/2.0, h/2.0
        inner = min(w, h) * 0.22

        def fbm(theta, phase):
            v = 0.0; amp = 1.0; freq = 1.0
            for _ in range(3):
                v += amp * np.sin(freq * theta + phase * (0.6 + 0.4*freq))
                amp *= 0.5; freq *= 2.0
            return v / 1.75

        noise_amt = float(self.edge_waviness) / 100.0
        audio_amt = float(self.feather_audio_amount) / 100.0 if self.feather_audio_enabled else 0.0

        s = np.asarray(spectrum, dtype=np.float32)
        mx = float(np.max(s)) if np.max(s) > 1e-9 else 1.0
        base_norm = s / mx
        spec_norm = np.concatenate([base_norm, base_norm[::-1][1:-1]]) if self.radial_mirror else base_norm

        N = 360
        amp_noise_px = min(w, h) * 0.025 * noise_amt
        energy_feather = min(1.0, energy * (self.feather_sensitivity / max(1e-3, self.waveform_sensitivity)))
        amp_audio_px = min(w, h) * 0.06 * audio_amt * (0.4 + 0.6*energy_feather)

        path = QPainterPath()
        for i in range(N+1):
            a = 2.0*np.pi*(i / N) - (np.pi/2.0) + np.deg2rad(self.radial_rotation_deg)
            si = int((i / N) * (len(spec_norm)-1)) if len(spec_norm) > 1 else 0
            s_val = float(spec_norm[si]) if len(spec_norm) else 0.0
            r = inner
            r += amp_noise_px * fbm(a*2.0 + self._phase*0.8, self._phase)
            r += amp_audio_px * s_val
            r_max = 0.48 * min(w, h)
            r_max = 0.48 * min(w, h)
            # Soft-compress radius near edges to avoid hard clipping at high sensitivity
            if r_max > inner:
                g = (r - inner) / (r_max - inner)
                g = max(0.0, g)
                g = math.tanh(1.25 * g) / math.tanh(1.25)
                r = inner + g * (r_max - inner)
            x = cx + np.cos(a) * r
            y = cy + np.sin(a) * r
            if i == 0: path.moveTo(x, y)
            else: path.lineTo(x, y)

        # Shadow for center shape
        if self._shadow_enabled:
            self._draw_shadow_path(p, path)

        # Clip & draw image
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setClipPath(path)
        if self.center_image is not None:
            mean_spec = float(np.mean(spec_norm)) if len(spec_norm) else 0.0
            stable_r = inner + (min(w, h) * 0.03 * audio_amt * (0.4 + 0.6*energy)) * mean_spec
            stable_r = max(inner*0.9, min(stable_r, inner + min(w,h)*0.3))
            br_stable = QRectF(cx - stable_r, cy - stable_r, 2*stable_r, 2*stable_r)
            img = self.center_image
            img_w = img.width(); img_h = img.height()
            if img_w > 0 and img_h > 0:
                rect_w = int(br_stable.width()); rect_h = int(br_stable.height())
                if rect_w <= 0 or rect_h <= 0:
                    rect_w = rect_h = int(inner*2)
                scale_w = rect_w / img_w; scale_h = rect_h / img_h
                scale = max(scale_w, scale_h)  # cover
                out_w = max(1, int(img_w * scale)); out_h = max(1, int(img_h * scale))
                # Center using round to avoid left/top bias
                cx_rect = br_stable.x() + rect_w/2.0
                cy_rect = br_stable.y() + rect_h/2.0
                cm = float(self.center_motion) / 100.0
                if cm > 0.0:
                    zoom = 1.0 + 0.12 * cm * (0.4 + 0.6*energy) * (0.5 + 0.5*np.sin(self._phase*1.7))
                    out_w = int(max(1, round(out_w * zoom))); out_h = int(max(1, round(out_h * zoom)))
                dx = int(round(cx_rect - out_w/2.0)); dy = int(round(cy_rect - out_h/2.0))
                if cm > 0.0:
                    j = int(cm * 6)
                    dx += int(round(j * np.sin(self._phase*2.3))); dy += int(round(j * np.cos(self._phase*1.9)))
                p.drawImage(dx, dy, img.scaled(out_w, out_h))
            else:
                p.fillPath(path, QColor(30, 30, 40))
        else:
            p.fillPath(path, QColor(30, 30, 40))
        p.restore()

        # Feather rings
        if self.feather_enabled:
            bg = QColor(13, 13, 18)
            layers = 6
            for k in range(1, layers+1):
                scale = 1.0 + 0.015 * k
                feather_path = QPainterPath()
                for i in range(N+1):
                    a = 2.0*np.pi*(i / N) - (np.pi/2.0) + np.deg2rad(self.radial_rotation_deg)
                    si = int((i / N) * (len(spec_norm)-1)) if len(spec_norm) > 1 else 0
                    s_val = float(spec_norm[si]) if len(spec_norm) else 0.0
                    r = inner
                    r += amp_noise_px * np.sin(a*2.0 + self._phase)  # cheaper for outer rings
                    r += amp_audio_px * s_val
                    r *= scale
                    x = cx + np.cos(a) * r
                    y = cy + np.sin(a) * r
                    if i == 0: feather_path.moveTo(x, y)
                    else: feather_path.lineTo(x, y)
                feather_path.closeSubpath()
                col = QColor(bg); col.setAlpha(max(10, 70 - 10*k))
                p.fillPath(feather_path, col)

    def render_frame_to_qimage(self, w, h, samples, spectrum):
        w = int(max(1, w)); h = int(max(1, h))
        img = QImage(w, h, QImage.Format_RGB888)
        energy = float(np.clip(np.mean(np.abs(samples)) * self.waveform_sensitivity, 0.0, 1.0))
        self._phase = (self._phase + 0.04 * (0.5 + energy)) % (2*np.pi)
        p = QPainter(img)
        self._offscreen_paint = True
        self._offscreen_size = (w, h)
        try:
            p.fillRect(0, 0, w, h, QColor(13, 13, 18))
            self._draw_background(p)
            try:
                pen = QPen(self._amp_to_color(energy))
            except Exception:
                r, g, b = [int(255*c) for c in self.color]
                pen = QPen(QColor(r, g, b))
            pen.setWidth(2)
            p.setPen(pen)
            mode = self._mode
            if mode.startswith("Waveform"):
                self._draw_waveform(p, w, h, samples, energy, pen=pen)
            elif mode.startswith("Spectrum"):
                radial = ("Radial" in mode)
                self._draw_spectrum(p, w, h, spectrum, energy, radial=radial, pen=pen)
                if radial:
                    self._draw_center_image_and_mask(p, w, h, energy, spectrum)
            else:
                self._draw_particles(p, w, h, spectrum, energy)
        finally:
            try:
                p.end()
            except Exception:
                pass
            self._offscreen_paint = False
            try:
                self._offscreen_size = None
            except Exception:
                pass
        return img

    def _smooth_closed(self, arr, amount):
        if arr is None or len(arr) == 0 or amount <= 0:
            return arr
        L = len(arr)
        up = int(1 + (amount // 25))
        a = np.asarray(arr, dtype=float)
        if up > 1:
            x = np.arange(L, dtype=float)
            xi = np.linspace(0.0, L-1, L*up)
            a = np.interp(xi, x, a)
        half = int(amount * 0.06)
        win = 2*half + 1
        if win > 1:
            pad = np.pad(a, (half, half), mode='wrap')
            ker = np.ones(win, dtype=float) / float(win)
            a = np.convolve(pad, ker, mode='valid')
        return a


    def set_shadow_angle_deg(self, deg: int):
        try:
            self._shadow_angle_deg = int(deg)
        except Exception:
            self._shadow_angle_deg = 45
        self.update()

    def set_shadow_spread(self, n: int):
        try:
            self._shadow_spread = int(max(1, n))
        except Exception:
            self._shadow_spread = 6
        self.update()


    def set_radial_waveform_smoothness(self, v: int):
        try: v = int(v)
        except Exception: v = 0
        self.radial_wave_smoothness = max(0, min(100, v))
        self.update()

    def set_radial_temporal_smoothing(self, v: int):
        try: v = int(v)
        except Exception: v = 0
        self.radial_temporal_alpha = max(0.0, min(0.95, v/100.0))
        self.update()
    def resizeEvent(self, ev):
        try:
            self._img = None
        except Exception:
            pass
        super().resizeEvent(ev)
        self.update()