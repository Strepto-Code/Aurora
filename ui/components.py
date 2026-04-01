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


def _compact_form(parent: QWidget) -> QFormLayout:
    """Return a QFormLayout with tight margins suitable for sidebar panels."""
    f = QFormLayout(parent)
    f.setContentsMargins(4, 4, 4, 4)
    f.setSpacing(5)
    f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return f


class BackgroundPanel(QWidget):
    def __init__(self, parent: QWidget, on_change: Callable[[BackgroundConfig], None]):
        super().__init__(parent)
        self._on_change = on_change
        self._cfg = BackgroundConfig()

        lay = _compact_form(self)

        self.path_lbl = QLabel("No image")
        self.path_lbl.setWordWrap(True)
        self.path_lbl.setMaximumHeight(32)
        self.path_lbl.setStyleSheet("font-size: 10px; color: #777;")
        lay.addRow(self.path_lbl)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        btn = QPushButton("Load...")
        clr = QPushButton("Clear")
        row.addWidget(btn)
        row.addWidget(clr)
        btn_wrap = QWidget()
        btn_wrap.setLayout(row)
        lay.addRow(btn_wrap)

        self.scale = QComboBox()
        self.scale.addItems(["fill", "fit", "stretch"])
        lay.addRow("Scale", self.scale)

        self.offset_x = QSpinBox()
        self.offset_x.setRange(-2000, 2000)
        self.offset_y = QSpinBox()
        self.offset_y.setRange(-2000, 2000)
        lay.addRow("Offset X", self.offset_x)
        lay.addRow("Offset Y", self.offset_y)

        self.dim = QSlider(Qt.Horizontal)
        self.dim.setRange(0, 100)
        self.dim.setValue(0)
        lay.addRow("Dim", self.dim)

        btn.clicked.connect(self._pick)
        clr.clicked.connect(self._clear)

        self.scale.currentTextChanged.connect(self._emit)
        self.offset_x.valueChanged.connect(self._emit)
        self.offset_y.valueChanged.connect(self._emit)
        self.dim.valueChanged.connect(self._emit)

    def set_config(self, cfg: BackgroundConfig) -> None:
        self._cfg = cfg
        name = os.path.basename(cfg.path) if cfg.path else "No image"
        self.path_lbl.setText(name)
        self.path_lbl.setToolTip(cfg.path or "")
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
        self.path_lbl.setText(os.path.basename(self._cfg.path))
        self.path_lbl.setToolTip(self._cfg.path)
        self._emit()

    def _clear(self) -> None:
        self._cfg.path = None
        self.path_lbl.setText("No image")
        self.path_lbl.setToolTip("")
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

        lay = _compact_form(self)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        btn_a = QPushButton("Color A")
        btn_b = QPushButton("Color B")
        btn_a.clicked.connect(lambda: self._pick("a"))
        btn_b.clicked.connect(lambda: self._pick("b"))
        row.addWidget(btn_a)
        row.addWidget(btn_b)
        btn_wrap = QWidget()
        btn_wrap.setLayout(row)
        lay.addRow(btn_wrap)

        self.curve = QDoubleSpinBox()
        self.curve.setRange(0.1, 8.0)
        self.curve.setSingleStep(0.1)
        self.curve.setValue(1.0)
        self.curve.valueChanged.connect(lambda v: self._set_curve(float(v)))
        lay.addRow("Curve", self.curve)

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
        lay.addRow("Min", self.min_box)
        lay.addRow("Max", self.max_box)

        self.smooth = QSlider(Qt.Horizontal)
        self.smooth.setRange(0, 100)
        self.smooth.setValue(50)
        self.smooth.valueChanged.connect(lambda v: self._set_smoothing(float(v) / 100.0))
        lay.addRow("Smooth", self.smooth)

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

        lay = _compact_form(self)

        self.chk = QCheckBox("Enabled")
        self.chk.toggled.connect(lambda b: self._set_enabled(bool(b)))
        lay.addRow(self.chk)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(50)
        self.opacity.valueChanged.connect(lambda v: self._set_opacity(int(v)))
        lay.addRow("Opacity", self.opacity)

        self.blur = QSlider(Qt.Horizontal)
        self.blur.setRange(0, 128)
        self.blur.setValue(16)
        self.blur.valueChanged.connect(self._on_blur)
        lay.addRow("Blur", self.blur)

        self.distance = QSlider(Qt.Horizontal)
        self.distance.setRange(0, 200)
        self.distance.setValue(8)
        self.distance.valueChanged.connect(self._on_distance)
        lay.addRow("Distance", self.distance)

        self.angle = QSlider(Qt.Horizontal)
        self.angle.setRange(0, 360)
        self.angle.setValue(45)
        self.angle.valueChanged.connect(self._on_angle)
        lay.addRow("Angle", self.angle)

        self.spread = QSlider(Qt.Horizontal)
        self.spread.setRange(1, 16)
        self.spread.setValue(6)
        self.spread.valueChanged.connect(self._on_spread)
        lay.addRow("Spread", self.spread)

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

        lay = _compact_form(self)

        self.chk = QCheckBox("Enabled")
        self.chk.toggled.connect(lambda b: self._set_enabled(bool(b)))
        lay.addRow(self.chk)

        btn = QPushButton("Color...")
        btn.clicked.connect(self._pick_color)
        lay.addRow(btn)

        self.blend = QComboBox()
        self.blend.addItems(["normal", "add", "multiply"])
        self.blend.currentTextChanged.connect(lambda s: self._set_blend(str(s)))
        lay.addRow("Blend", self.blend)

        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(0, 100)
        self.thr.setValue(15)
        self.thr.valueChanged.connect(lambda v: self._set_threshold(float(v) / 100.0))
        lay.addRow("Threshold", self.thr)

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

        lay = _compact_form(self)

        self.chk = QCheckBox("Enabled")
        self.chk.toggled.connect(lambda b: self._set_enabled(bool(b)))
        lay.addRow(self.chk)

        btn = QPushButton("Color...")
        btn.clicked.connect(self._pick_color)
        lay.addRow(btn)

        self.radius = QSlider(Qt.Horizontal)
        self.radius.setRange(0, 120)
        self.radius.setValue(22)
        self.radius.valueChanged.connect(lambda v: self._set_radius(int(v)))
        lay.addRow("Radius", self.radius)

        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setValue(80)
        self.strength.valueChanged.connect(lambda v: self._set_strength(int(v)))
        lay.addRow("Strength", self.strength)

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
        mode: str = "load",
        state: Optional[AppState] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Presets")

        self._store = store
        self._mode = mode
        self._state = state
        self.loaded_state: Optional[AppState] = None

        lay = QVBoxLayout(self)

        self.list = QListWidget()
        lay.addWidget(self.list)

        if mode == "save":
            row = QHBoxLayout()
            self.name = QLineEdit()
            self.name.setPlaceholderText("Preset name...")
            row.addWidget(self.name)
            btn_save = QPushButton("Save")
            btn_save.clicked.connect(self._save)
            row.addWidget(btn_save)
            lay.addLayout(row)
        else:
            self.name = None
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
        if self.name is None:
            return
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Preset name", "Please enter a preset name.")
            return
        if self._state is None:
            return
        self._store.save(name, self._state)
        self._refresh()
        self.accept()

    def _load(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text()
        try:
            st = self._store.load(name)
        except Exception:
            QMessageBox.warning(self, "Preset", "Failed to load preset.")
            return
        self.loaded_state = st
        self.accept()

    def _delete(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text()
        if QMessageBox.question(self, "Delete preset", f"Delete '{name}'?") != QMessageBox.Yes:
            return
        self._store.delete(name)
        self._refresh()
