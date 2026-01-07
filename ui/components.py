from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QKeySequenceEdit,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QColorDialog,
)

from config.settings import AppState, BackgroundConfig, HotkeyConfig, PresetStore


class BackgroundPanel(QWidget):
    def __init__(self, parent: QWidget, on_change: Callable[[BackgroundConfig], None]):
        super().__init__(parent)
        self._on_change = on_change
        self._cfg = BackgroundConfig()

        lay = QVBoxLayout(self)

        self.path_lbl = QLabel("No image")
        btn = QPushButton("Load Background…")
        clr = QPushButton("Clear")
        row = QHBoxLayout()
        row.addWidget(btn)
        row.addWidget(clr)

        lay.addWidget(self.path_lbl)
        lay.addLayout(row)

        self.scale = QComboBox()
        self.scale.addItems(["fill", "fit", "stretch"])
        lay.addWidget(QLabel("Scale"))
        lay.addWidget(self.scale)

        self.offset_x = QSpinBox()
        self.offset_x.setRange(-2000, 2000)
        self.offset_y = QSpinBox()
        self.offset_y.setRange(-2000, 2000)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Offset X"))
        row2.addWidget(self.offset_x)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Offset Y"))
        row3.addWidget(self.offset_y)
        lay.addLayout(row3)

        self.dim = QSlider(Qt.Horizontal)
        self.dim.setRange(0, 100)
        self.dim.setValue(0)
        lay.addWidget(QLabel("Dim (%)"))
        lay.addWidget(self.dim)

        btn.clicked.connect(self._pick)
        clr.clicked.connect(self._clear)

        self.scale.currentTextChanged.connect(self._emit)
        self.offset_x.valueChanged.connect(self._emit)
        self.offset_y.valueChanged.connect(self._emit)
        self.dim.valueChanged.connect(self._emit)

    def set_config(self, cfg: BackgroundConfig) -> None:
        self._cfg = cfg
        self.path_lbl.setText(cfg.path or "No image")
        self.scale.setCurrentText(cfg.scale_mode)
        self.offset_x.setValue(int(cfg.offset_x))
        self.offset_y.setValue(int(cfg.offset_y))
        self.dim.setValue(int(cfg.dim_percent))

    def _pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose background image",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)",
        )
        if not path:
            return
        self._cfg.path = os.path.abspath(os.path.expanduser(path))
        self.path_lbl.setText(self._cfg.path)
        self._emit()

    def _clear(self) -> None:
        self._cfg.path = None
        self.path_lbl.setText("No image")
        self._emit()

    def _emit(self) -> None:
        self._cfg.scale_mode = str(self.scale.currentText() or "fill")
        self._cfg.offset_x = int(self.offset_x.value())
        self._cfg.offset_y = int(self.offset_y.value())
        self._cfg.dim_percent = int(self.dim.value())
        self._on_change(self._cfg)


class GradientPanel(QWidget):
    def __init__(
        self,
        parent: QWidget,
        set_colors: Callable[[Tuple[int, int, int], Tuple[int, int, int]], None],
        set_curve: Callable[[float], None],
        set_clamp: Callable[[float, float], None],
        set_smoothing: Callable[[float], None],
    ):
        super().__init__(parent)
        self._set_colors = set_colors
        self._set_curve = set_curve
        self._set_clamp = set_clamp
        self._set_smoothing = set_smoothing

        self._a = QColor("#12d6ff")
        self._b = QColor("#ffffff")

        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        btn_a = QPushButton("Color A")
        btn_b = QPushButton("Color B")
        btn_a.clicked.connect(lambda: self._pick("a"))
        btn_b.clicked.connect(lambda: self._pick("b"))
        row.addWidget(btn_a)
        row.addWidget(btn_b)
        lay.addLayout(row)

        self.curve = QDoubleSpinBox()
        self.curve.setRange(0.1, 8.0)
        self.curve.setSingleStep(0.1)
        self.curve.setValue(1.0)
        self.curve.valueChanged.connect(lambda v: self._set_curve(float(v)))
        lay.addWidget(QLabel("Curve"))
        lay.addWidget(self.curve)

        row2 = QHBoxLayout()
        self.min_box = QDoubleSpinBox()
        self.min_box.setRange(0.0, 1.0)
        self.min_box.setSingleStep(0.01)
        self.min_box.setValue(0.05)
        self.max_box = QDoubleSpinBox()
        self.max_box.setRange(0.0, 1.0)
        self.max_box.setSingleStep(0.01)
        self.max_box.setValue(0.6)
        self.min_box.valueChanged.connect(self._emit_clamp)
        self.max_box.valueChanged.connect(self._emit_clamp)
        row2.addWidget(QLabel("Amp min"))
        row2.addWidget(self.min_box)
        row2.addWidget(QLabel("Amp max"))
        row2.addWidget(self.max_box)
        lay.addLayout(row2)

        self.smooth = QSlider(Qt.Horizontal)
        self.smooth.setRange(0, 100)
        self.smooth.setValue(50)
        self.smooth.valueChanged.connect(lambda v: self._set_smoothing(float(v) / 100.0))
        lay.addWidget(QLabel("Temporal smoothing"))
        lay.addWidget(self.smooth)

        self._emit_colors()
        self._emit_clamp()

    def _pick(self, which: str) -> None:
        col = QColorDialog.getColor(
            self._a if which == "a" else self._b,
            self,
            options=QColorDialog.ShowAlphaChannel,
        )
        if not col.isValid():
            return
        if which == "a":
            self._a = col
        else:
            self._b = col
        self._emit_colors()

    def _emit_colors(self) -> None:
        a = (self._a.red(), self._a.green(), self._a.blue())
        b = (self._b.red(), self._b.green(), self._b.blue())
        self._set_colors(a, b)

    def _emit_clamp(self) -> None:
        self._set_clamp(float(self.min_box.value()), float(self.max_box.value()))


class ShadowPanel(QWidget):
    def __init__(
        self,
        parent: QWidget,
        set_enabled: Callable[[bool], None],
        set_opacity: Callable[[int], None],
        set_blur_radius: Optional[Callable[[int], None]] = None,
        set_distance: Optional[Callable[[int], None]] = None,
        set_angle_deg: Optional[Callable[[int], None]] = None,
        set_spread: Optional[Callable[[int], None]] = None,
    ):
        super().__init__(parent)
        self._set_enabled = set_enabled
        self._set_opacity = set_opacity
        self._set_blur_radius = set_blur_radius
        self._set_distance = set_distance
        self._set_angle_deg = set_angle_deg
        self._set_spread = set_spread

        lay = QVBoxLayout(self)

        self.chk = QCheckBox("Drop Shadow")
        self.chk.toggled.connect(lambda b: self._set_enabled(bool(b)))
        lay.addWidget(self.chk)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(50)
        self.opacity.valueChanged.connect(lambda v: self._set_opacity(int(v)))
        lay.addWidget(QLabel("Opacity"))
        lay.addWidget(self.opacity)

        self.blur = QSlider(Qt.Horizontal)
        self.blur.setRange(0, 128)
        self.blur.setValue(16)
        self.blur.valueChanged.connect(self._on_blur)
        lay.addWidget(QLabel("Blur"))
        lay.addWidget(self.blur)

        self.distance = QSlider(Qt.Horizontal)
        self.distance.setRange(0, 200)
        self.distance.setValue(8)
        self.distance.valueChanged.connect(self._on_distance)
        lay.addWidget(QLabel("Distance"))
        lay.addWidget(self.distance)

        self.angle = QSlider(Qt.Horizontal)
        self.angle.setRange(0, 360)
        self.angle.setValue(45)
        self.angle.valueChanged.connect(self._on_angle)
        lay.addWidget(QLabel("Angle"))
        lay.addWidget(self.angle)

        self.spread = QSlider(Qt.Horizontal)
        self.spread.setRange(1, 16)
        self.spread.setValue(6)
        self.spread.valueChanged.connect(self._on_spread)
        lay.addWidget(QLabel("Spread"))
        lay.addWidget(self.spread)

    def _try_parent_view(self, method: str):
        view = getattr(self.parent(), "view", None)
        fn = getattr(view, method, None)
        return fn if callable(fn) else None

    def _on_blur(self, v: int) -> None:
        fn = self._set_blur_radius or self._try_parent_view("set_shadow_blur_radius")
        if fn:
            fn(int(v))

    def _on_distance(self, v: int) -> None:
        fn = self._set_distance or self._try_parent_view("set_shadow_distance")
        if fn:
            fn(int(v))

    def _on_angle(self, v: int) -> None:
        fn = self._set_angle_deg or self._try_parent_view("set_shadow_angle_deg")
        if fn:
            fn(int(v))

    def _on_spread(self, v: int) -> None:
        fn = self._set_spread or self._try_parent_view("set_shadow_spread")
        if fn:
            fn(int(v))


class RadialFillPanel(QWidget):
    def __init__(
        self,
        parent: QWidget,
        set_enabled: Callable[[bool], None],
        set_color: Callable[[str], None],
        set_blend: Callable[[str], None],
        set_threshold: Callable[[float], None],
    ):
        super().__init__(parent)
        self._set_enabled = set_enabled
        self._set_color = set_color
        self._set_blend = set_blend
        self._set_threshold = set_threshold

        lay = QVBoxLayout(self)

        self.chk = QCheckBox("Radial Fill")
        self.chk.toggled.connect(lambda b: self._set_enabled(bool(b)))
        lay.addWidget(self.chk)

        btn = QPushButton("Fill Color")
        btn.clicked.connect(self._pick_color)
        lay.addWidget(btn)

        self.blend = QComboBox()
        self.blend.addItems(["normal", "add", "multiply"])
        self.blend.currentTextChanged.connect(lambda s: self._set_blend(str(s)))
        lay.addWidget(QLabel("Blend"))
        lay.addWidget(self.blend)

        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(0, 100)
        self.thr.setValue(15)
        self.thr.valueChanged.connect(lambda v: self._set_threshold(float(v) / 100.0))
        lay.addWidget(QLabel("Threshold"))
        lay.addWidget(self.thr)

    def _pick_color(self) -> None:
        col = QColorDialog.getColor(
            QColor(255, 255, 255, 64),
            self,
            options=QColorDialog.ShowAlphaChannel,
        )
        if col.isValid():
            self._set_color(col.name(QColor.HexArgb))


class GlowPanel(QWidget):
    def __init__(
        self,
        parent: QWidget,
        set_enabled: Callable[[bool], None],
        set_color: Callable[[str], None],
        set_radius: Callable[[int], None],
        set_strength: Callable[[int], None],
    ):
        super().__init__(parent)
        self._set_enabled = set_enabled
        self._set_color = set_color
        self._set_radius = set_radius
        self._set_strength = set_strength

        lay = QVBoxLayout(self)

        self.chk = QCheckBox("Glow")
        self.chk.toggled.connect(lambda b: self._set_enabled(bool(b)))
        lay.addWidget(self.chk)

        btn = QPushButton("Glow Color")
        btn.clicked.connect(self._pick_color)
        lay.addWidget(btn)

        self.radius = QSlider(Qt.Horizontal)
        self.radius.setRange(0, 120)
        self.radius.setValue(22)
        self.radius.valueChanged.connect(lambda v: self._set_radius(int(v)))
        lay.addWidget(QLabel("Radius"))
        lay.addWidget(self.radius)

        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setValue(80)
        self.strength.valueChanged.connect(lambda v: self._set_strength(int(v)))
        lay.addWidget(QLabel("Strength"))
        lay.addWidget(self.strength)

    def _pick_color(self) -> None:
        col = QColorDialog.getColor(
            QColor(80, 220, 255, 200),
            self,
            options=QColorDialog.ShowAlphaChannel,
        )
        if col.isValid():
            self._set_color(col.name(QColor.HexArgb))


class HotkeysDialog(QDialog):
    def __init__(self, parent: QWidget, config: HotkeyConfig):
        super().__init__(parent)
        self.setWindowTitle("Hotkeys")
        self._cfg = config

        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.ed_start = QKeySequenceEdit(self._cfg.start_stop)
        self.ed_next = QKeySequenceEdit(self._cfg.next_preset)
        self.ed_prev = QKeySequenceEdit(self._cfg.prev_preset)
        self.ed_shot = QKeySequenceEdit(self._cfg.screenshot)
        self.ed_safe = QKeySequenceEdit(self._cfg.toggle_safe_mode)

        form.addRow("Start/Stop", self.ed_start)
        form.addRow("Next Preset", self.ed_next)
        form.addRow("Prev Preset", self.ed_prev)
        form.addRow("Screenshot", self.ed_shot)
        form.addRow("Toggle Safe Mode", self.ed_safe)

        lay.addLayout(form)
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok)

    def get_config(self) -> HotkeyConfig:
        return HotkeyConfig(
            start_stop=self.ed_start.keySequence().toString(),
            next_preset=self.ed_next.keySequence().toString(),
            prev_preset=self.ed_prev.keySequence().toString(),
            screenshot=self.ed_shot.keySequence().toString(),
            toggle_safe_mode=self.ed_safe.keySequence().toString(),
        )


class PresetsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        store: PresetStore,
        on_save: Callable[[str, AppState], None],
        on_load: Callable[[AppState], None],
        capture_state: Callable[[], AppState],
    ):
        super().__init__(parent)
        self.setWindowTitle("Presets")

        self._store = store
        self._on_save = on_save
        self._on_load = on_load
        self._capture_state = capture_state

        lay = QVBoxLayout(self)

        self.list = QListWidget()
        lay.addWidget(self.list)

        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Preset name…")
        row.addWidget(self.name)
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._save)
        row.addWidget(btn_save)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        btn_load = QPushButton("Load")
        btn_del = QPushButton("Delete")
        btn_load.clicked.connect(self._load)
        btn_del.clicked.connect(self._delete)
        row2.addWidget(btn_load)
        row2.addWidget(btn_del)
        lay.addLayout(row2)

        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for name in self._store.list_presets():
            self.list.addItem(name)

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Preset name", "Please enter a preset name.")
            return
        st = self._capture_state()
        self._on_save(name, st)
        self._refresh()

    def _load(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text()
        st = self._store.load_preset(name)
        if st is None:
            QMessageBox.warning(self, "Preset", "Failed to load preset.")
            return
        self._on_load(st)
        self.accept()

    def _delete(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text()
        if QMessageBox.question(self, "Delete preset", f"Delete '{name}'?") != QMessageBox.Yes:
            return
        self._store.delete_preset(name)
        self._refresh()
