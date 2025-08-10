from PySide6.QtGui import QColor

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QSlider, QDoubleSpinBox, QColorDialog
from PySide6.QtCore import Qt

class GradientPanel(QWidget):
    def __init__(self, parent, set_colors, set_curve, set_clamp, set_smoothing):
        super().__init__(parent)
        self.set_colors = set_colors
        self.set_curve = set_curve
        self.set_clamp = set_clamp
        self.set_smoothing = set_smoothing

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        btnA = QPushButton("Color A")
        btnB = QPushButton("Color B")
        btnA.clicked.connect(lambda: self._pick('A'))
        btnB.clicked.connect(lambda: self._pick('B'))
        row.addWidget(btnA); row.addWidget(btnB)
        lay.addLayout(row)

        self.curve = QComboBox(); self.curve.addItems(["linear","ease-in","ease-out","smoothstep"])
        self.curve.currentTextChanged.connect(lambda s: self.set_curve(s))
        lay.addWidget(QLabel("Mapping curve")); lay.addWidget(self.curve)

        row2 = QHBoxLayout()
        self.minBox = QDoubleSpinBox(); self.minBox.setRange(0.0, 1.0); self.minBox.setSingleStep(0.01); self.minBox.setValue(0.05)
        self.maxBox = QDoubleSpinBox(); self.maxBox.setRange(0.0, 1.0); self.maxBox.setSingleStep(0.01); self.maxBox.setValue(0.6)
        self.minBox.valueChanged.connect(self._emit_clamp); self.maxBox.valueChanged.connect(self._emit_clamp)
        row2.addWidget(QLabel("Amp min")); row2.addWidget(self.minBox)
        row2.addWidget(QLabel("Amp max")); row2.addWidget(self.maxBox)
        lay.addLayout(row2)

        self.smooth = QSlider(Qt.Horizontal); self.smooth.setRange(0,100); self.smooth.setValue(50)
        self.smooth.valueChanged.connect(lambda v: self.set_smoothing(v/100.0))
        lay.addWidget(QLabel("Temporal smoothing")); lay.addWidget(self.smooth)

    def _pick(self, which):
        col = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
        if not col.isValid(): return
        if which == 'A':
            self.set_colors(col.name(QColor.HexArgb), None)
        else:
            self.set_colors(None, col.name(QColor.HexArgb))

    def _emit_clamp(self, *a):
        self.set_clamp(self.minBox.value(), self.maxBox.value())
