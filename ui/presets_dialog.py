
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QLineEdit, QLabel, QMessageBox
)
from config.settings import PresetStore, AppState

class PresetsDialog(QDialog):
    def __init__(self, parent, store: PresetStore, on_save, on_load, capture_state):
        super().__init__(parent)
        self.setWindowTitle("Presets")
        self.store = store
        self.on_save = on_save
        self.on_load = on_load
        self.capture_state = capture_state

        self.list = QListWidget()
        self.refresh()

        self.name_edit = QLineEdit()
        save_btn = QPushButton("Save As…")
        load_btn = QPushButton("Load")
        del_btn = QPushButton("Delete")
        close_btn = QPushButton("Close")

        save_btn.clicked.connect(self._save_as)
        load_btn.clicked.connect(self._load)
        del_btn.clicked.connect(self._delete)
        close_btn.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Existing Presets"))
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Name:"))
        row.addWidget(self.name_edit, 1)
        lay.addLayout(row)
        btns = QHBoxLayout()
        for b in (save_btn, load_btn, del_btn, close_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

    def refresh(self):
        self.list.clear()
        for n in self.store.list_presets():
            self.list.addItem(n)

    def _save_as(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Preset", "Enter a name.")
            return
        state = self.capture_state()
        self.on_save(name, state)
        self.refresh()

    def _load(self):
        item = self.list.currentItem()
        if not item:
            return
        self.on_load(item.text())
        self.accept()

    def _delete(self):
        item = self.list.currentItem()
        if not item:
            return
        import os
        path = self.store.dir + "/" + item.text() + ".json"
        try:
            os.remove(path)
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", str(e))
