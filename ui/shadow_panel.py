
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QSlider, QLabel
from PySide6.QtCore import Qt

class ShadowPanel(QWidget):
    def __init__(self, parent, set_enabled, set_opacity):
        super().__init__(parent)
        self.set_enabled = set_enabled
        self.set_opacity = set_opacity

        lay = QVBoxLayout(self)
        self.chk = QCheckBox("Drop Shadow")
        self.chk.toggled.connect(lambda b: self.set_enabled(b))
        lay.addWidget(self.chk)
        self.opacity = QSlider(Qt.Horizontal); self.opacity.setRange(0,100); self.opacity.setValue(50)
        self.opacity.valueChanged.connect(lambda v: self.set_opacity(v))
        lay.addWidget(QLabel("Shadow Opacity")); lay.addWidget(self.opacity)


# --- extended controls for blur/distance ---
try:
    from PySide6.QtWidgets import QSlider
except Exception:
    QSlider = None

if QSlider is not None and hasattr(ShadowPanel, "__init__"):
    _orig_init = ShadowPanel.__init__
    def _wrap_init(self, *a, **k):
        _orig_init(self, *a, **k)
        try:
            # heuristic: look for 'form' attribute
            form = getattr(self, 'form', None)
            if form is None and hasattr(self, 'layout'):
                form = getattr(self, 'layout')
            self.size_slider = QSlider(Qt.Horizontal); self.size_slider.setRange(0, 64); self.size_slider.setValue(16)
            self.dist_slider = QSlider(Qt.Horizontal); self.dist_slider.setRange(0, 50); self.dist_slider.setValue(8)
            if form is not None and hasattr(form, 'addRow'):
                form.addRow("Blur/Size", self.size_slider)
                form.addRow("Distance", self.dist_slider)
            elif hasattr(self, 'layout'):
                self.layout.addWidget(self.size_slider)
                self.layout.addWidget(self.dist_slider)
            self.size_slider.valueChanged.connect(lambda v: getattr(self.parent().view, 'set_shadow_blur_radius', lambda _: None)(int(v)))
            self.dist_slider.valueChanged.connect(lambda v: getattr(self.parent().view, 'set_shadow_distance', lambda _: None)(int(v)))
        except Exception:
            pass
    ShadowPanel.__init__ = _wrap_init


# --- Extended shadow controls (angle/spread with big ranges) ---
try:
    from PySide6.QtWidgets import QSlider
    _old_init = ShadowPanel.__init__
    def _init_ext(self, *a, **k):
        _old_init(self, *a, **k)
        try:
            form = getattr(self, 'form', None)
            layout = getattr(self, 'layout', None)
            self.blur_slider = getattr(self, 'size_slider', None) or QSlider(Qt.Horizontal)
            self.dist_slider = getattr(self, 'dist_slider', None) or QSlider(Qt.Horizontal)
            # enlarge ranges
            self.blur_slider.setRange(0, 128)
            self.dist_slider.setRange(0, 200)
            # new sliders
            self.angle_slider = QSlider(Qt.Horizontal); self.angle_slider.setRange(0, 360); self.angle_slider.setValue(45)
            self.spread_slider = QSlider(Qt.Horizontal); self.spread_slider.setRange(1, 16); self.spread_slider.setValue(6)
            if form is not None and hasattr(form, 'addRow'):
                form.addRow("Angle", self.angle_slider)
                form.addRow("Spread", self.spread_slider)
            elif layout is not None and hasattr(layout, 'addWidget'):
                layout.addWidget(self.angle_slider); layout.addWidget(self.spread_slider)
            # wire
            self.angle_slider.valueChanged.connect(lambda v: getattr(self.parent().view,'set_shadow_angle_deg',lambda _:_)(int(v)))
            self.spread_slider.valueChanged.connect(lambda v: getattr(self.parent().view,'set_shadow_spread',lambda _:_)(int(v)))
        except Exception:
            pass
    ShadowPanel.__init__ = _init_ext
except Exception:
    pass
