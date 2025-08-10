from PySide6.QtCore import Qt

import os
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel, QComboBox, QSlider, QHBoxLayout, QSpinBox
from config.settings import BackgroundConfig

class BackgroundPanel(QWidget):
    def __init__(self, parent, on_change):
        super().__init__(parent)
        self.on_change = on_change
        self.cfg = BackgroundConfig()

        lay = QVBoxLayout(self)
        self.path_lbl = QLabel("No image")
        btn = QPushButton("Load Background…")
        clr = QPushButton("Clear")
        btn.clicked.connect(self._load)
        clr.clicked.connect(self._clear)

        self.scale = QComboBox()
        self.scale.addItems(["fill","fit","stretch","center","tile"])
        self.offset_x = QSpinBox(); self.offset_x.setRange(-5000, 5000)
        self.offset_y = QSpinBox(); self.offset_y.setRange(-5000, 5000)
        self.dim = QSlider(); self.dim.setRange(0,100); self.dim.setValue(0); self.dim.setOrientation(Qt.Horizontal) # 1=Qt.Horizontal

        lay.addWidget(self.path_lbl)
        row = QHBoxLayout(); row.addWidget(btn); row.addWidget(clr); lay.addLayout(row)
        lay.addWidget(QLabel("Scale Mode")); lay.addWidget(self.scale)
        row2 = QHBoxLayout(); row2.addWidget(QLabel("Offset X")); row2.addWidget(self.offset_x); lay.addLayout(row2)
        row3 = QHBoxLayout(); row3.addWidget(QLabel("Offset Y")); row3.addWidget(self.offset_y); lay.addLayout(row3)
        lay.addWidget(QLabel("Dim (%)")); lay.addWidget(self.dim)

        self.scale.currentTextChanged.connect(self._emit)
        self.offset_x.valueChanged.connect(self._emit)
        self.offset_y.valueChanged.connect(self._emit)
        self.dim.valueChanged.connect(self._emit)

    def set_config(self, cfg: BackgroundConfig):
        self.cfg = cfg
        self.path_lbl.setText(cfg.path or "No image")
        self.scale.setCurrentText(cfg.scale_mode)
        self.offset_x.setValue(cfg.offset_x)
        self.offset_y.setValue(cfg.offset_y)
        self.dim.setValue(cfg.dim_percent)

    def _emit(self, *a):
        self.cfg.scale_mode = self.scale.currentText()
        self.cfg.offset_x = self.offset_x.value()
        self.cfg.offset_y = self.offset_y.value()
        self.cfg.dim_percent = self.dim.value()
        self.on_change(self.cfg)

    def _load(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Choose Background", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not fn: return
        self.cfg.path = fn
        self.path_lbl.setText(fn)
        self.on_change(self.cfg)

    def _clear(self):
        self.cfg.path = None
        self.path_lbl.setText("No image")
        self.on_change(self.cfg)
