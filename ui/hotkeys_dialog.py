
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QKeySequenceEdit, QPushButton
from config.settings import HotkeyConfig

class HotkeysDialog(QDialog):
    def __init__(self, parent, config: HotkeyConfig):
        super().__init__(parent)
        self.setWindowTitle("Hotkeys")
        self.cfg = config
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.ed_start = QKeySequenceEdit(self.cfg.start_stop)
        self.ed_next = QKeySequenceEdit(self.cfg.next_preset)
        self.ed_prev = QKeySequenceEdit(self.cfg.prev_preset)
        self.ed_shot = QKeySequenceEdit(self.cfg.screenshot)
        self.ed_safe = QKeySequenceEdit(self.cfg.toggle_safe_mode)
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
