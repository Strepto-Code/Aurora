
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton, QComboBox, QSlider, QLabel, QColorDialog
from PySide6.QtCore import Qt

class RadialFillPanel(QWidget):
    def __init__(self, parent, set_enabled, set_color, set_blend, set_threshold):
        super().__init__(parent)
        self.set_enabled = set_enabled
        self.set_color = set_color
        self.set_blend = set_blend
        self.set_threshold = set_threshold

        lay = QVBoxLayout(self)
        chk = QCheckBox("Radial Fill")
        chk.toggled.connect(lambda b: self.set_enabled(b))
        lay.addWidget(chk)

        btn = QPushButton("Fill Color")
        btn.clicked.connect(self._pick_color)
        lay.addWidget(btn)

        self.blend = QComboBox(); self.blend.addItems(["normal","add","multiply"])
        self.blend.currentTextChanged.connect(self.set_blend)
        lay.addWidget(self.blend)

        self.thresh = QSlider(Qt.Horizontal); self.thresh.setRange(0,100); self.thresh.setValue(10)
        self.thresh.valueChanged.connect(lambda v: self.set_threshold(v/100.0))
        lay.addWidget(QLabel("Fill threshold")); lay.addWidget(self.thresh)

    def _pick_color(self):
        col = QColorDialog.getColor()
        if not col.isValid(): return
        self.set_color(col.name())
