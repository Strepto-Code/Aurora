import logging
import math
import os
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, QSize, QRect
from PySide6.QtGui import (
    QPainter, QColor, QPen, QImage, QPainterPath, QPixmap,
    QTransform, QSurfaceFormat,
)
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    _HAS_GL_WIDGET = True
except ImportError:
    _HAS_GL_WIDGET = False

logger = logging.getLogger(__name__)
_WidgetBase = QOpenGLWidget if _HAS_GL_WIDGET else QWidget


class RTVisualizerWidget(_WidgetBase):

    # -- Init --

    def __init__(self, audio_engine, start_timer=True):
        super().__init__()

        if _HAS_GL_WIDGET:
            fmt = QSurfaceFormat()
            fmt.setStencilBufferSize(8)
            fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
            self.setFormat(fmt)
            logger.info("RTVisualizerWidget: QOpenGLWidget (GPU-backed QPainter)")
        else:
            logger.info("RTVisualizerWidget: QWidget (software raster)")

        self.audio = audio_engine
        self._mode = "Waveform - Linear"
        self._offscreen_paint = False
        self._offscreen_size = None
        self._bg_img = None

        self.color = (0.2, 0.8, 1.0)
        self._grad_a = QColor(32, 255, 255)
        self._grad_b = QColor(255, 0, 255)
        self._grad_curve = "linear"
        self._grad_min = 0.0
        self._grad_max = 1.0
        self._amp_ema = 0.0
        self._amp_alpha = 0.2

        self.waveform_sensitivity = 1.0
        self.feather_sensitivity = 2.0

        self.radial_rotation_deg = 0.0
        self.radial_mirror = True
        self.radial_smooth = True
        self.radial_smooth_amount = 50
        self.radial_wave_smoothness = 50
        self.radial_temporal_alpha = 0.3
        self._radial_prev = None

        self.particle_density = 200
        self.particle_speed = 1.0
        self.particle_glow = 0.6

        self._bg_path = None
        self._bg_pix = None
        self._bg_scale_mode = "fill"
        self._bg_off = (0, 0)
        self._bg_dim = 0

        self._shadow_enabled = False
        self._shadow_opacity = 0.6
        self._shadow_blur = 16
        self._shadow_distance = 8
        self._shadow_angle_deg = 45
        self._shadow_spread = 6

        self._glow_enabled = False
        self._glow_color = QColor(80, 220, 255, 255)
        self._glow_radius = 22
        self._glow_strength = 0.8
        self._glow_layers = 8

        self._glow_rt_scale = 0.35
        self._glow_rt_every_n = 2
        self._glow_rt_frame = 0
        self._glow_rt_img = None
        self._glow_rt_buf = None
        self._glow_rt_w = 0
        self._glow_rt_h = 0

        self._fill_enabled = False
        self._fill_color = QColor(255, 255, 255, 48)
        self._fill_blend = "normal"
        self._fill_threshold = 0.1

        self.center_image_zoom = 100
        self.center_image = None
        self.center_image_path = ""
        self.center_motion = 0
        self.feather_enabled = False
        self.edge_waviness = 30
        self.feather_noise = 30
        self.feather_audio_enabled = False
        self.feather_audio_amount = 40

        self._hud = True
        self._fps_cap = 60
        self._last_paint_t = None
        self._fps_smoothed = 0.0
        self._phase = 0.0
        self._img = None

        self._timer = QTimer(self) if start_timer else None
        if self._timer is not None:
            self._timer.setTimerType(Qt.PreciseTimer)
            self._timer.timeout.connect(self.update)
            self._update_timer()
        self.setMinimumSize(400, 300)

    # -- Public setters: mode, color, sensitivity --

    def set_mode(self, mode):
        self._mode = str(mode)
        self.update()

    def set_color(self, rgb):
        self.color = tuple(float(x) for x in rgb)
        self.update()

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

    def set_sensitivity(self, s):
        self.set_waveform_sensitivity(s)

    # -- Setters: gradient --

    def set_gradient_colors(self, a_hex, b_hex):
        if a_hex:
            ca = QColor(a_hex)
            if ca.isValid():
                self._grad_a = ca
        if b_hex:
            cb = QColor(b_hex)
            if cb.isValid():
                self._grad_b = cb
        self.update()

    def set_gradient_curve(self, name):
        self._grad_curve = (name or "linear").lower()
        self.update()

    def set_gradient_clamp(self, amin, amax):
        self._grad_min = float(min(amin, amax))
        self._grad_max = float(max(amin, amax))
        self.update()

    def set_gradient_smoothing(self, alpha):
        self._amp_alpha = float(max(0.0, min(1.0, alpha)))
        self.update()

    # -- Setters: radial --

    def set_radial_rotation_deg(self, deg):
        try:
            self.radial_rotation_deg = float(deg)
        except Exception:
            self.radial_rotation_deg = 0.0
        self.update()

    def set_radial_mirror(self, on):
        self.radial_mirror = bool(on)
        self.update()

    def set_radial_smooth(self, on):
        self.radial_smooth = True
        self.update()

    def set_radial_smooth_amount(self, v):
        try:
            v = int(v)
        except Exception:
            v = 0
        self.radial_smooth_amount = max(0, min(100, v))
        self.update()

    def set_radial_waveform_smoothness(self, v):
        try:
            v = int(v)
        except Exception:
            v = 0
        self.radial_wave_smoothness = max(0, min(100, v))
        self.update()

    def set_radial_temporal_smoothing(self, v):
        try:
            v = int(v)
        except Exception:
            v = 0
        self.radial_temporal_alpha = max(0.0, min(0.95, v / 100.0))
        self.update()

    # -- Setters: center image / feather --

    def set_center_image(self, image_path):
        try:
            if not image_path:
                return False
            norm = os.path.abspath(os.path.expanduser(str(image_path)))
            img = QImage(norm)
            if img.isNull():
                img = QImage(str(image_path))
                if img.isNull():
                    return False
                norm = str(image_path)
            self.center_image = img
            self.center_image_path = norm
            self.update()
            return True
        except Exception:
            return False

    def clear_center_image(self):
        self.center_image = None
        self.update()

    def set_center_image_zoom(self, value):
        try:
            v = int(value)
        except Exception:
            v = 100
        self.center_image_zoom = max(50, min(250, v))
        self.update()

    def set_center_motion(self, value):
        try:
            v = int(value)
        except Exception:
            v = 0
        self.center_motion = max(0, min(100, v))
        self.update()

    def set_feather_enabled(self, enabled):
        self.feather_enabled = bool(enabled)
        self.update()

    def set_waviness(self, value):
        try:
            v = int(value)
        except Exception:
            v = 0
        v = max(0, min(100, v))
        self.edge_waviness = v
        self.feather_noise = v
        self.update()

    def set_feather_noise(self, value):
        self.set_waviness(value)

    def set_edge_waviness(self, value):
        self.set_waviness(value)

    def set_feather_audio_enabled(self, enabled):
        self.feather_audio_enabled = bool(enabled)
        self.update()

    def set_feather_audio_amount(self, value):
        try:
            v = int(value)
        except Exception:
            v = 0
        self.feather_audio_amount = max(0, min(100, v))
        self.update()

    # -- Setters: shadow --

    def set_shadow_enabled(self, enabled):
        self._shadow_enabled = bool(enabled)
        self.update()

    def set_shadow_opacity(self, pct):
        try:
            self._shadow_opacity = max(0.0, min(1.0, pct / 100.0))
        except Exception:
            self._shadow_opacity = 0.6
        self.update()

    def set_shadow_blur_radius(self, r):
        try:
            self._shadow_blur = int(max(0, r))
        except Exception:
            self._shadow_blur = 16
        self.update()

    def set_shadow_distance(self, dist):
        try:
            self._shadow_distance = int(max(0, dist))
        except Exception:
            self._shadow_distance = 8
        self.update()

    def set_shadow_angle_deg(self, deg):
        try:
            self._shadow_angle_deg = int(deg)
        except Exception:
            self._shadow_angle_deg = 45
        self.update()

    def set_shadow_spread(self, n):
        try:
            self._shadow_spread = int(max(1, n))
        except Exception:
            self._shadow_spread = 6
        self.update()

    # -- Setters: glow --

    def set_glow_enabled(self, enabled):
        self._glow_enabled = bool(enabled)
        self.update()

    def set_glow_color(self, rgba_hex):
        c = QColor(rgba_hex)
        if c.isValid():
            self._glow_color = c
        self.update()

    def set_glow_radius(self, px):
        try:
            self._glow_radius = int(max(0, px))
        except Exception:
            self._glow_radius = 26
        self.update()

    def set_glow_strength(self, pct):
        try:
            self._glow_strength = max(0.0, min(1.0, float(pct) / 100.0))
        except Exception:
            self._glow_strength = 0.9
        self.update()

    # -- Setters: radial fill --

    def set_radial_fill_enabled(self, on):
        self._fill_enabled = bool(on)
        self.update()

    def set_radial_fill_color(self, rgba_hex):
        c = QColor(rgba_hex)
        if c.isValid():
            self._fill_color = c
        self.update()

    def set_radial_fill_blend(self, mode):
        self._fill_blend = mode or "normal"
        self.update()

    def set_radial_fill_threshold(self, t):
        try:
            self._fill_threshold = float(max(0.0, min(1.0, t)))
        except Exception:
            self._fill_threshold = 0.1
        self.update()

    # -- Setters: background --

    def set_background_config(self, cfg):
        try:
            path = cfg.path if getattr(cfg, 'path', None) else None
            if path:
                path = os.path.abspath(str(path))
            self._bg_path = path
            self._bg_scale_mode = getattr(cfg, 'scale_mode', 'fill')
            try:
                self._bg_off = (int(getattr(cfg, 'offset_x', 0)), int(getattr(cfg, 'offset_y', 0)))
            except Exception:
                self._bg_off = (0, 0)
            try:
                self._bg_dim = int(getattr(cfg, 'dim_percent', 0))
            except Exception:
                self._bg_dim = 0

            self._bg_img = QImage(self._bg_path) if self._bg_path else None

            # QPixmap is only safe in the GUI thread
            from PySide6.QtCore import QThread, QCoreApplication
            try:
                app = QCoreApplication.instance()
                in_gui = bool(app and app.thread() == QThread.currentThread())
            except Exception:
                in_gui = False

            if self._bg_path and in_gui:
                try:
                    self._bg_pix = QPixmap(self._bg_path)
                except Exception:
                    self._bg_pix = None
            else:
                self._bg_pix = None
        except Exception:
            pass
        self.update()

    # -- Setters: misc --

    def set_fps_cap(self, fps):
        try:
            fps = int(fps)
        except Exception:
            fps = 60
        self._fps_cap = max(1, fps)
        self._update_timer()
        self.update()

    def set_hud_enabled(self, on):
        self._hud = bool(on)
        self.update()

    def set_safe_mode(self, on):
        if on:
            self._shadow_blur = min(self._shadow_blur, 6)
        self.update()

    def set_particle_density(self, v):
        try:
            self.particle_density = int(max(10, min(2000, v)))
        except Exception:
            self.particle_density = 200
        self.update()

    def set_particle_speed(self, v):
        try:
            self.particle_speed = float(max(0.1, min(5.0, v / 50.0)))
        except Exception:
            self.particle_speed = 1.0
        self.update()

    def set_particle_glow(self, v):
        try:
            self.particle_glow = float(max(0.0, min(2.0, v / 50.0)))
        except Exception:
            self.particle_glow = 0.6
        self.update()

    def reset_time(self):
        self._phase = 0.0

    # -- Timer --

    def _update_timer(self):
        if self._timer is None:
            return
        interval_ms = max(5, int(round(1000.0 / float(max(1, self._fps_cap)))))
        if self._timer.isActive():
            self._timer.stop()
        self._timer.start(interval_ms)

    # -- Color helpers --

    def _amp_to_color(self, amp):
        try:
            self._amp_ema = self._amp_alpha * self._amp_ema + (1.0 - self._amp_alpha) * float(amp)
            t = (self._amp_ema - self._grad_min) / max(1e-6, self._grad_max - self._grad_min)
            t = max(0.0, min(1.0, t))
            if self._grad_curve == 'ease-in':
                t = t * t
            elif self._grad_curve == 'ease-out':
                t = 1.0 - (1.0 - t) * (1.0 - t)
            elif self._grad_curve == 'smoothstep':
                t = t * t * (3 - 2 * t)
            r = int(self._grad_a.red() + t * (self._grad_b.red() - self._grad_a.red()))
            g = int(self._grad_a.green() + t * (self._grad_b.green() - self._grad_a.green()))
            b = int(self._grad_a.blue() + t * (self._grad_b.blue() - self._grad_a.blue()))
            return QColor(r, g, b)
        except Exception:
            return QColor(255, 255, 255)

    # -- Background drawing --

    def _draw_background(self, p):
        try:
            if self._offscreen_paint:
                if not self._bg_img or self._bg_img.isNull():
                    return
                ow, oh = self._offscreen_size if self._offscreen_size else (self.width(), self.height())
                self._draw_bg_image(p, self._bg_img, ow, oh, draw_image=True)
                return
            if not self._bg_pix or self._bg_pix.isNull():
                return
            self._draw_bg_image(p, self._bg_pix, self.width(), self.height(), draw_image=False)
        except Exception:
            pass

    def _draw_bg_image(self, p, source, w, h, draw_image):
        mode = (self._bg_scale_mode or "fill").lower()
        if mode == 'tile':
            sw, sh = source.width(), source.height()
            for ty in range(0, h, sh):
                for tx in range(0, w, sw):
                    if draw_image:
                        p.drawImage(tx + self._bg_off[0], ty + self._bg_off[1], source)
                    else:
                        p.drawPixmap(tx + self._bg_off[0], ty + self._bg_off[1], source)
        else:
            target_size = QSize(w, h)
            if mode == 'fit':
                scaled = source.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            elif mode == 'stretch':
                scaled = source.scaled(target_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            elif mode == 'center':
                scaled = source
            else:
                scaled = source.scaled(target_size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (w - scaled.width()) // 2 + self._bg_off[0]
            y = (h - scaled.height()) // 2 + self._bg_off[1]
            if draw_image:
                p.drawImage(x, y, scaled)
            else:
                p.drawPixmap(x, y, scaled)

        d = max(0, min(100, int(self._bg_dim)))
        if d > 0:
            if draw_image:
                p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(255 * (d / 100.0))))
            else:
                p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * (d / 100.0))))

    # -- Shadow drawing --

    def _draw_shadow_path(self, p, path):
        if not p.isActive() or not self._shadow_enabled or self._shadow_opacity <= 0.0:
            return
        a = int(255 * self._shadow_opacity)
        sc = QColor(0, 0, 0, a)
        ang = float(self._shadow_angle_deg)
        dist = float(max(0.0, self._shadow_distance))
        dx = dist * math.cos(math.radians(ang))
        dy = dist * math.sin(math.radians(ang))
        p.save()
        p.translate(dx, dy)
        pen = QPen(sc)
        pen.setWidth(max(2, self._shadow_blur))
        p.setPen(pen)
        p.drawPath(path)
        p.restore()

    # -- Glow drawing --

    def _draw_glow_path(self, p, path, energy):
        if not p.isActive() or not self._glow_enabled:
            return
        radius = self._glow_radius
        strength = self._glow_strength
        if radius <= 0 or strength <= 0.0:
            return

        audio_intensity = 0.15 + 0.85 * max(0.0, min(1.0, energy))
        intensity = strength * audio_intensity
        if intensity <= 0.0:
            return

        base = self._glow_color
        base_alpha = max(0.0, min(1.0, base.alphaF()))

        if self._offscreen_paint:
            self._draw_glow_offscreen(p, path, base, base_alpha, intensity, radius)
            return

        self._glow_rt_frame += 1
        if (self._glow_rt_frame % max(1, self._glow_rt_every_n)) != 0:
            if self._glow_rt_img is not None and not self._glow_rt_img.isNull():
                p.save()
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setCompositionMode(QPainter.CompositionMode_Plus)
                p.setOpacity(max(0.0, min(1.0, intensity)))
                p.drawImage(QRect(0, 0, self.width(), self.height()), self._glow_rt_img)
                p.restore()
            return

        try:
            self._draw_glow_bloom_cached(p, path, base, intensity, radius)
        except Exception:
            self._draw_glow_fallback(p, path, base, base_alpha, intensity, radius)

    def _draw_glow_offscreen(self, p, path, base, base_alpha, intensity, radius):
        layers = max(4, min(16, self._glow_layers))
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for i in range(layers):
            t = i / float(max(1, layers - 1))
            falloff = (1.0 - t) ** 2
            a = int(255 * base_alpha * intensity * 0.55 * falloff)
            if a <= 0:
                continue
            c = QColor(base)
            c.setAlpha(a)
            pen = QPen(c)
            pen.setWidth(max(3, int(2 + radius * (0.35 + 0.9 * t))))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)
        p.restore()

    def _draw_glow_fallback(self, p, path, base, base_alpha, intensity, radius):
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for t in [0.15, 0.55, 1.0]:
            falloff = (1.0 - t) ** 2
            a = int(255 * base_alpha * intensity * 0.62 * falloff)
            if a <= 0:
                continue
            c = QColor(base)
            c.setAlpha(a)
            pen = QPen(c)
            pen.setWidth(max(2, int(2 + radius * (0.55 + 0.85 * t))))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)
        p.restore()

    def _draw_glow_bloom_cached(self, p, path, base, intensity, radius):
        w = max(1, self.width())
        h = max(1, self.height())
        scale = max(0.2, min(0.6, self._glow_rt_scale))
        sw = max(64, int(w * scale))
        sh = max(64, int(h * scale))

        if self._glow_rt_img is None or self._glow_rt_w != sw or self._glow_rt_h != sh:
            self._glow_rt_img = QImage(sw, sh, QImage.Format_ARGB32_Premultiplied)
            self._glow_rt_w, self._glow_rt_h = sw, sh

        self._glow_rt_img.fill(0)
        sp = QPainter(self._glow_rt_img)
        sp.setRenderHint(QPainter.Antialiasing, True)
        sp.setCompositionMode(QPainter.CompositionMode_Source)

        t = QTransform()
        t.scale(scale, scale)
        spath = t.map(path)
        pen_w = max(2, int((2 + radius * 0.55) * scale))
        mpen = QPen(QColor(255, 255, 255, 255))
        mpen.setWidth(pen_w)
        mpen.setCapStyle(Qt.RoundCap)
        mpen.setJoinStyle(Qt.RoundJoin)
        sp.setPen(mpen)
        sp.drawPath(spath)
        sp.end()

        # Box blur via integral image
        ptr = self._glow_rt_img.bits()
        ptr.setsize(self._glow_rt_img.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((sh, self._glow_rt_img.bytesPerLine()))
        rgba = arr[:, :sw * 4].reshape((sh, sw, 4))
        alpha = rgba[:, :, 3].astype(np.float32)

        r = max(1, int(radius * scale * 0.65))
        pad = np.pad(alpha, ((r, r), (r, r)), mode="edge")
        integ = pad.cumsum(axis=0).cumsum(axis=1)
        k = 2 * r + 1
        blur = (integ[k:, k:] - integ[:-k, k:] - integ[k:, :-k] + integ[:-k, :-k]) / float(k * k)

        a = np.clip(blur * (255.0 * max(0.0, min(1.0, intensity))), 0.0, 255.0).astype(np.uint8)
        af = a.astype(np.float32) / 255.0
        rgba[:, :, 2] = np.clip(float(base.red()) * af, 0, 255).astype(np.uint8)
        rgba[:, :, 1] = np.clip(float(base.green()) * af, 0, 255).astype(np.uint8)
        rgba[:, :, 0] = np.clip(float(base.blue()) * af, 0, 255).astype(np.uint8)
        rgba[:, :, 3] = a

        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.drawImage(QRect(0, 0, w, h), self._glow_rt_img)
        p.restore()

    # -- HUD --

    def _draw_hud(self, p):
        if not self._hud:
            return
        now = time.monotonic()
        if self._last_paint_t is not None:
            dt = max(1e-4, now - self._last_paint_t)
            fps = 1.0 / dt
            self._fps_smoothed = (0.9 * self._fps_smoothed + 0.1 * fps) if self._fps_smoothed > 0 else fps
        self._last_paint_t = now
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(10, 20, f"FPS: {self._fps_smoothed:.1f}")

    # -- Paint entry points --

    def _do_paint(self, p, w, h):
        samples, spectrum, _ = self.audio.get_frame()
        energy = float(np.clip(np.mean(np.abs(samples)) * self.waveform_sensitivity, 0.0, 1.0))
        self._phase = (self._phase + 0.04 * (0.5 + energy)) % (2 * np.pi)

        p.fillRect(0, 0, w, h, QColor(13, 13, 18))
        self._draw_background(p)

        try:
            pen = QPen(self._amp_to_color(energy))
        except Exception:
            r, g, b = [int(255 * c) for c in self.color]
            pen = QPen(QColor(r, g, b))
        pen.setWidth(2)
        p.setPen(pen)

        mode = self._mode
        if mode.startswith("Waveform"):
            self._draw_waveform(p, w, h, samples, energy, pen=pen)
        elif mode.startswith("Spectrum"):
            radial = "Radial" in mode
            self._draw_spectrum(p, w, h, spectrum, energy, radial=radial, pen=pen)
            if radial:
                self._draw_center_image_and_mask(p, w, h, energy, spectrum)
        else:
            self._draw_particles(p, w, h, spectrum, energy)

    def initializeGL(self):
        pass

    def paintGL(self):
        w = max(1, self.width())
        h = max(1, self.height())
        p = QPainter(self)
        try:
            self._do_paint(p, w, h)
            self._draw_hud(p)
        finally:
            p.end()

    def paintEvent(self, ev):
        if _HAS_GL_WIDGET:
            super().paintEvent(ev)
            return
        w = max(1, self.width())
        h = max(1, self.height())
        if self._img is None or self._img.width() != w or self._img.height() != h:
            self._img = QImage(w, h, QImage.Format_RGB888)
        p = QPainter(self._img)
        self._do_paint(p, w, h)
        p.end()
        painter = QPainter(self)
        painter.drawImage(0, 0, self._img)
        self._draw_hud(painter)
        painter.end()

    def resizeEvent(self, ev):
        self._img = None
        super().resizeEvent(ev)
        self.update()

    # -- Offscreen export entry points --

    def render_frame_to_qimage(self, w, h, samples, spectrum):
        w = int(max(1, w))
        h = int(max(1, h))
        img = QImage(w, h, QImage.Format_RGB888)
        p = QPainter(img)
        try:
            self.paint_frame(p, w, h, samples, spectrum)
        finally:
            try:
                p.end()
            except Exception:
                pass
        return img

    def paint_frame(self, p, w, h, samples, spectrum):
        w = int(max(1, w))
        h = int(max(1, h))
        energy = float(np.clip(np.mean(np.abs(samples)) * self.waveform_sensitivity, 0.0, 1.0))
        self._phase = (self._phase + 0.04 * (0.5 + energy)) % (2 * np.pi)
        self._offscreen_paint = True
        self._offscreen_size = (w, h)
        try:
            p.fillRect(0, 0, w, h, QColor(13, 13, 18))
            self._draw_background(p)

            try:
                pen = QPen(self._amp_to_color(energy))
            except Exception:
                r, g, b = [int(255 * c) for c in self.color]
                pen = QPen(QColor(r, g, b))
            pen.setWidth(2)
            p.setPen(pen)

            mode = self._mode
            if mode.startswith("Waveform"):
                self._draw_waveform(p, w, h, samples, energy, pen=pen)
            elif mode.startswith("Spectrum"):
                radial = "Radial" in mode
                self._draw_spectrum(p, w, h, spectrum, energy, radial=radial, pen=pen)
                if radial:
                    self._draw_center_image_and_mask(p, w, h, energy, spectrum)
            else:
                self._draw_particles(p, w, h, spectrum, energy)
        finally:
            self._offscreen_paint = False
            self._offscreen_size = None

    # -- Drawing primitives --

    def _apply_fill(self, p, path, energy):
        if self._fill_enabled and energy >= self._fill_threshold:
            p.save()
            if self._fill_blend == 'add':
                p.setCompositionMode(QPainter.CompositionMode_Plus)
            elif self._fill_blend == 'multiply':
                p.setCompositionMode(QPainter.CompositionMode_Multiply)
            p.fillPath(path, self._fill_color)
            p.restore()

    def _draw_waveform(self, p, w, h, samples, energy, pen=None):
        n = len(samples)
        if n <= 1:
            return

        if "Circular" in self._mode:
            cx, cy = w / 2.0, h / 2.0
            base_r = min(w, h) * 0.28
            amp = min(w, h) * 0.22 * (0.5 + 0.6 * energy)

            segs = min(720, n)
            if not self._offscreen_paint and (self._shadow_enabled or self._glow_enabled):
                segs = min(360, n)
                if self._shadow_enabled and self._glow_enabled:
                    segs = min(256, n)
            step = max(1, int(n / segs))

            path = QPainterPath()
            first = True
            for i in range(0, n, step):
                v = float(samples[i])
                a = 2.0 * np.pi * (i / max(1, n - 1)) - (np.pi / 2.0) + np.deg2rad(self.radial_rotation_deg)
                r = base_r + v * amp
                x = cx + np.cos(a) * r
                y = cy + np.sin(a) * r
                if first:
                    path.moveTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            path.closeSubpath()

            self._apply_fill(p, path, energy)
            if self._shadow_enabled:
                self._draw_shadow_path(p, path)
            self._draw_glow_path(p, path, energy)
            p.drawPath(path)
        else:
            amp = 0.45 * (h / 2) * (0.5 + energy)
            mid = h / 2.0
            path = QPainterPath()
            for x in range(w):
                i = int((x / max(1, w - 1)) * (n - 1))
                y = float(mid - samples[i] * amp)
                if x == 0:
                    path.moveTo(0.0, y)
                else:
                    path.lineTo(float(x), y)

            if self._shadow_enabled and self._shadow_opacity > 0.0:
                a = int(255 * self._shadow_opacity)
                sc = QColor(0, 0, 0, a)
                penw = max(2, min(10, self._shadow_blur))
                sp = QPen(sc)
                sp.setWidth(penw)
                sp.setCapStyle(Qt.RoundCap)
                sp.setJoinStyle(Qt.RoundJoin)
                p.save()
                d = float(self._shadow_distance)
                p.translate(d, d)
                p.setPen(sp)
                p.drawPath(path)
                p.restore()
                if pen:
                    p.setPen(pen)

            p.drawPath(path)

    def _draw_spectrum(self, p, w, h, spectrum, energy, radial=False, pen=None):
        N = 80
        if spectrum is None or len(spectrum) == 0:
            return
        spec = spectrum[:N]
        mx = float(np.max(spec)) if np.max(spec) > 0 else 1.0
        spec = spec / mx

        if radial:
            cx, cy = w / 2.0, h / 2.0
            inner = min(w, h) * 0.22
            span = min(w, h) * 0.28 * (0.6 + 0.6 * energy)
            spec_draw = np.concatenate([spec, spec[::-1][1:-1]]) if self.radial_mirror else spec
            L = len(spec_draw)

            if L > 3 and self.radial_wave_smoothness > 0:
                spec_draw = self._smooth_closed(spec_draw, int(self.radial_wave_smoothness))
                L = len(spec_draw)

            if self.radial_temporal_alpha > 0.0:
                if self._radial_prev is None or len(self._radial_prev) != L:
                    self._radial_prev = spec_draw.copy()
                else:
                    self._radial_prev = self.radial_temporal_alpha * self._radial_prev + (1.0 - self.radial_temporal_alpha) * spec_draw
                spec_draw = self._radial_prev

            if self.radial_smooth and L > 3:
                spec_draw = self._smooth_closed(spec_draw, int(self.radial_smooth_amount))
                L = len(spec_draw)

            path = QPainterPath()
            for i in range(L + 1):
                a = 2.0 * np.pi * (i / L) - (np.pi / 2.0) + np.deg2rad(self.radial_rotation_deg)
                v = float(spec_draw[min(i, L - 1)])
                r = inner + span * v
                x = cx + np.cos(a) * r
                y = cy + np.sin(a) * r
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.closeSubpath()

            self._apply_fill(p, path, energy)
            if self._shadow_enabled:
                self._draw_shadow_path(p, path)
            self._draw_glow_path(p, path, energy)
            p.drawPath(path)
        else:
            bar_w = max(1, int(w / N))
            for i in range(N):
                v = float(spec[i])
                bh = int(v * (h - 10) * (0.4 + 0.6 * energy))
                x0 = i * bar_w
                p.drawLine(x0, h - 5, x0, h - 5 - bh)

    def _draw_particles(self, p, w, h, spectrum, energy):
        N = 50
        if spectrum is None or len(spectrum) == 0:
            return
        spec = spectrum[:N]
        spec = spec / (np.max(spec) + 1e-6)
        for i in range(N):
            v = float(spec[i])
            x = int((i + 0.5) / N * w)
            y = int(h / 2 + (v - 0.5) * h * 0.6)
            p.drawPoint(x, y)

    # -- Center image and feather mask --

    def _draw_center_image_and_mask(self, p, w, h, energy, spectrum=None):
        if spectrum is None or len(spectrum) == 0:
            return
        cx, cy = w / 2.0, h / 2.0
        inner = min(w, h) * 0.22

        def fbm(theta, phase):
            v = 0.0
            amp = 1.0
            freq = 1.0
            for k in range(3):
                v += amp * np.sin(freq * theta + (k + 1) * phase)
                amp *= 0.5
                freq *= 2.0
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
        amp_audio_px = min(w, h) * 0.06 * audio_amt * (0.4 + 0.6 * energy_feather)

        path = QPainterPath()
        for i in range(N + 1):
            a = 2.0 * np.pi * (i / N) - (np.pi / 2.0) + np.deg2rad(self.radial_rotation_deg)
            si = int((i / N) * (len(spec_norm) - 1)) if len(spec_norm) > 1 else 0
            s_val = float(spec_norm[si]) if len(spec_norm) else 0.0
            r = inner + amp_noise_px * fbm(a * 2.0, self._phase) + amp_audio_px * s_val

            r_max = 0.48 * min(w, h)
            if r_max > inner:
                g = max(0.0, (r - inner) / (r_max - inner))
                g = math.tanh(1.25 * g) / math.tanh(1.25)
                r = inner + g * (r_max - inner)

            x = cx + np.cos(a) * r
            y = cy + np.sin(a) * r
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        if self._shadow_enabled:
            self._draw_shadow_path(p, path)

        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setClipPath(path)

        if self.center_image is not None:
            img = self.center_image
            img_w, img_h = img.width(), img.height()
            if img_w > 0 and img_h > 0:
                # Scale to cover clip path bounds so reactive expansion never reveals gaps
                clip_rect = path.boundingRect().adjusted(-2, -2, 2, 2)
                rect_w = max(1, int(clip_rect.width()))
                rect_h = max(1, int(clip_rect.height()))
                scale = max(rect_w / float(img_w), rect_h / float(img_h))

                user_zoom = max(0.1, float(self.center_image_zoom) / 100.0)
                scale *= user_zoom

                cm = float(self.center_motion) / 100.0
                if cm > 0.0:
                    motion_zoom = 1.0 + 0.12 * cm * (0.4 + 0.6 * energy) * (0.5 + 0.5 * np.sin(self._phase * 1.7))
                    scale *= max(0.1, motion_zoom)

                out_w = int(max(1, round(img_w * scale)))
                out_h = int(max(1, round(img_h * scale)))
                cx_rect = clip_rect.x() + clip_rect.width() / 2.0
                cy_rect = clip_rect.y() + clip_rect.height() / 2.0
                dx = int(round(cx_rect - out_w / 2.0))
                dy = int(round(cy_rect - out_h / 2.0))

                if cm > 0.0:
                    j = int(cm * 6)
                    dx += int(round(j * np.sin(self._phase * 2.3)))
                    dy += int(round(j * np.cos(self._phase * 1.9)))

                p.drawImage(dx, dy, img.scaled(out_w, out_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            else:
                p.fillPath(path, QColor(30, 30, 40))
        else:
            p.fillPath(path, QColor(30, 30, 40))

        p.restore()

        if self.feather_enabled:
            bg = QColor(13, 13, 18)
            for k in range(1, 7):
                fscale = 1.0 + 0.015 * k
                feather_path = QPainterPath()
                for i in range(N + 1):
                    a = 2.0 * np.pi * (i / N) - (np.pi / 2.0) + np.deg2rad(self.radial_rotation_deg)
                    si = int((i / N) * (len(spec_norm) - 1)) if len(spec_norm) > 1 else 0
                    s_val = float(spec_norm[si]) if len(spec_norm) else 0.0
                    r = (inner + amp_noise_px * np.sin(a * 2.0 + self._phase) + amp_audio_px * s_val) * fscale
                    x = cx + np.cos(a) * r
                    y = cy + np.sin(a) * r
                    if i == 0:
                        feather_path.moveTo(x, y)
                    else:
                        feather_path.lineTo(x, y)
                feather_path.closeSubpath()
                col = QColor(bg)
                col.setAlpha(max(10, 70 - 10 * k))
                p.fillPath(feather_path, col)

    # -- Smoothing utility --

    def _smooth_closed(self, arr, amount):
        if arr is None or len(arr) == 0 or amount <= 0:
            return arr
        L = len(arr)
        up = int(1 + (amount // 25))
        a = np.asarray(arr, dtype=float)
        if up > 1:
            x = np.arange(L, dtype=float)
            xi = np.linspace(0.0, L - 1, L * up)
            a = np.interp(xi, x, a)
        half = int(amount * 0.06)
        win = 2 * half + 1
        if win > 1:
            pad = np.pad(a, (half, half), mode='wrap')
            ker = np.ones(win, dtype=float) / float(win)
            a = np.convolve(pad, ker, mode='valid')
        return a
