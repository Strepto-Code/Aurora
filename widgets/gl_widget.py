
import time
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtOpenGLWidgets import QOpenGLWidget
import moderngl
from visuals.waveform import WaveformRenderer
from visuals.spectrum import SpectrumRenderer
from visuals.particles import ParticleRenderer

class GLVisualizerWidget(QOpenGLWidget):
    def __init__(self, audio_engine):
        super().__init__()
        self.audio = audio_engine
        self.ctx = None
        self._mode = "Waveform - Linear"
        self.color = (0.2, 0.8, 1.0)
        self.sensitivity = 1.0
        self.fps_cap = 60
        self.time_accum = 0.0

        self._renderers = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._update_timer()

    # ---------- public API from MainWindow ----------
    def set_mode(self, mode):
        self._mode = str(mode)

    def set_color(self, rgb):
        self.color = tuple(float(x) for x in rgb)

    def set_sensitivity(self, s):
        try:
            self.sensitivity = max(0.1, float(s))
        except Exception:
            self.sensitivity = 1.0

    def set_fps_cap(self, fps):
        try:
            self.fps_cap = int(fps)
        except Exception:
            self.fps_cap = 60
        self._update_timer()

    def reset_time(self):
        self.time_accum = 0.0

    # ---------- QOpenGLWidget lifecycle ----------
    def initializeGL(self):
        # QOpenGLWidget makes the GL context current here
        self.ctx = moderngl.create_context()
        # Line smoothing for nicer waveforms
        self.ctx.enable(moderngl.BLEND)
        # Build renderers
        self._renderers = {
            "Waveform - Linear": WaveformRenderer(self.ctx, circular=False),
            "Waveform - Circular": WaveformRenderer(self.ctx, circular=True),
            "Spectrum - Bars": SpectrumRenderer(self.ctx, radial=False),
            "Spectrum - Radial": SpectrumRenderer(self.ctx, radial=True),
            "Particles": ParticleRenderer(self.ctx),
        }

    def resizeGL(self, w, h):
        if self.ctx is not None:
            self.ctx.viewport = (0, 0, int(w), int(h))

    def paintGL(self):
        if self.ctx is None:
            return
        w = max(1, self.width())
        h = max(1, self.height())
        self.ctx.viewport = (0, 0, w, h)
        self.ctx.clear(0.05, 0.05, 0.07, 1.0)

        # Pull latest frame from audio engine (never blocks)
        samples, spectrum, flux = self.audio.get_frame()
        energy = float(np.clip(np.mean(np.abs(samples)) * self.sensitivity, 0.0, 1.0))

        # Choose renderer
        renderer = self._renderers.get(self._mode)
        if renderer is None:
            # Fallback
            renderer = next(iter(self._renderers.values()))

        renderer.render(w, h, samples, spectrum, energy, flux, self.color, self.sensitivity)

    # ---------- internals ----------
    def _update_timer(self):
        fps = max(1, int(self.fps_cap))
        interval_ms = max(5, int(1000 / fps))
        if self._timer.isActive():
            self._timer.stop()
        self._timer.start(interval_ms)
