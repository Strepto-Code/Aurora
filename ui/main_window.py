
import os
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QFileDialog, QComboBox, QPushButton, QSlider,
    QLabel, QHBoxLayout, QVBoxLayout, QSpinBox, QProgressBar, QCheckBox, QDockWidget, QTabWidget
)
from audio.input import AudioEngine
from export.exporter import Exporter
from widgets.rt_widget import RTVisualizerWidget

from PySide6.QtGui import QShortcut, QKeySequence, QColor
from config.settings import (
    PresetStore, DEFAULT_STATE, AppState,
    BackgroundConfig, ShadowConfig, RadialFillConfig, HotkeyConfig,
    load_audio_state, save_audio_state
)
from ui.presets_dialog import PresetsDialog
from ui.hotkeys_dialog import HotkeysDialog
from ui.background_panel import BackgroundPanel
from ui.gradient_panel import GradientPanel
from ui.shadow_panel import ShadowPanel
from ui.radial_fill_panel import RadialFillPanel



class _ExportSignals(QObject):
    progress = Signal(int)
    done = Signal()
    failed = Signal(str)


MODES = [
    "Spectrum - Radial",
    "Waveform - Linear",
    "Waveform - Circular",
    "Spectrum - Bars",
    "Particles",
]

class MainWindow(QMainWindow):
    def _on_mode_changed(self, text: str):
        self.view.set_mode(text)
        radial = ("Radial" in text)
        # show/hide radial-only controls
        try:
            self._radial_group.setVisible(radial)
        except Exception:
            pass
        self.view.update()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Visualizer (Realtime + Export)")
        self._preset_store = PresetStore()
        self._app_state = DEFAULT_STATE
        self._hotkeys = self._app_state.hotkeys

        self._preset_store = PresetStore()
        self._app_state = DEFAULT_STATE
        self._hotkeys = self._app_state.hotkeys

        self._preset_store = PresetStore()
        self._app_state = DEFAULT_STATE
        self._hotkeys = self._app_state.hotkeys


        # Engine + realtime view
        self.engine = AudioEngine(sample_rate=48000, block_size=1024)
        self.view = RTVisualizerWidget(self.engine)

        # Radial waveform smoothness controls
        try:
            from PySide6.QtWidgets import QDockWidget, QWidget, QFormLayout, QSlider
            dock = QDockWidget("Radial Smoothness", self); body = QWidget(dock); form = QFormLayout(body)
            self.radial_smoothness_slider = QSlider(Qt.Horizontal); self.radial_smoothness_slider.setRange(0, 100); self.radial_smoothness_slider.setValue(50)
            self.radial_temporal_slider = QSlider(Qt.Horizontal); self.radial_temporal_slider.setRange(0, 95); self.radial_temporal_slider.setValue(30)
            self.radial_smoothness_slider.valueChanged.connect(lambda v: hasattr(self.view,'set_radial_waveform_smoothness') and self.view.set_radial_waveform_smoothness(v))
            self.radial_temporal_slider.valueChanged.connect(lambda v: hasattr(self.view,'set_radial_temporal_smoothing') and self.view.set_radial_temporal_smoothing(v))
            form.addRow("Spatial", self.radial_smoothness_slider)
            form.addRow("Temporal", self.radial_temporal_slider)
            body.setLayout(form); dock.setWidget(body)
            self.addDockWidget(Qt.RightDockWidgetArea, dock)
        except Exception:
            pass

        # Split sensitivities
        try:
            from PySide6.QtWidgets import QSlider, QLabel, QFormLayout, QWidget, QDockWidget
            sens_dock = QDockWidget("Sensitivity", self); sens_body = QWidget(sens_dock); sens_form = QFormLayout(sens_body)
            self.wave_sens = QSlider(Qt.Horizontal); self.wave_sens.setRange(5, 300); self.wave_sens.setValue(100)
            self.wave_sens.valueChanged.connect(lambda v: hasattr(self.view,'set_waveform_sensitivity') and self.view.set_waveform_sensitivity(v/100.0))
            self.feather_sens = QSlider(Qt.Horizontal); self.feather_sens.setRange(5, 500); self.feather_sens.setValue(200)
            self.feather_sens.valueChanged.connect(lambda v: hasattr(self.view,'set_feather_sensitivity') and self.view.set_feather_sensitivity(v/100.0))
            sens_form.addRow("Waveform Sensitivity", self.wave_sens)
            sens_form.addRow("Feather Sensitivity",  self.feather_sens)
            sens_body.setLayout(sens_form); sens_dock.setWidget(sens_body)
            self.addDockWidget(Qt.RightDockWidgetArea, sens_dock)
        except Exception:
            pass

        # Menus
        mb = self.menuBar()
        m_file = mb.addMenu('File')
        m_presets = m_file.addMenu('Presets')
        m_presets.addAction('Save…').triggered.connect(self._menu_preset_save)
        m_presets.addAction('Load…').triggered.connect(self._menu_preset_load)
        m_presets.addAction('Manage…').triggered.connect(self._menu_preset_manage)
        m_edit = mb.addMenu('Edit')
        m_edit.addAction('Hotkeys…').triggered.connect(self._menu_hotkeys)
        m_view = mb.addMenu('View')
        act_hud = m_view.addAction('Performance HUD'); act_hud.setCheckable(True); act_hud.setChecked(True); act_hud.toggled.connect(self.view.set_hud_enabled)
        m_fps = m_view.addMenu('FPS Cap')
        for cap in (30,45,60,120):
            a = m_fps.addAction(f'FPS {cap}')
            a.triggered.connect(lambda chk, c=cap: self.view.set_fps_cap(c))
        m_tools = mb.addMenu('Tools')
        m_tools.addAction('Test Device').triggered.connect(self._test_device)

        # Visual FX dock
        self.fxDock = QDockWidget("Visual FX", self)
        self.fxTabs = QTabWidget(self.fxDock)
        self.bg_panel = BackgroundPanel(self, self.view.set_background_config)
        self.grad_panel = GradientPanel(self, lambda a,b: self._set_grad_colors(a,b), self.view.set_gradient_curve, self.view.set_gradient_clamp, self.view.set_gradient_smoothing)
        self.shadow_panel = ShadowPanel(self, self.view.set_shadow_enabled, self.view.set_shadow_opacity)
        self.radial_panel = RadialFillPanel(self, self.view.set_radial_fill_enabled, self.view.set_radial_fill_color, self.view.set_radial_fill_blend, self.view.set_radial_fill_threshold)
        self.fxTabs.addTab(self.bg_panel, "Background")
        self.fxTabs.addTab(self.grad_panel, "Gradient")
        self.fxTabs.addTab(self.shadow_panel, "Shadow")
        self.fxTabs.addTab(self.radial_panel, "Radial Fill")
        self.fxDock.setWidget(self.fxTabs)
        self.addDockWidget(Qt.RightDockWidgetArea, self.fxDock)

        self._apply_hotkeys()


        # Side controls
        self.open_btn = QPushButton("Open Audio")
        self.mode_combo = QComboBox(); self.mode_combo.addItems(MODES)
        self.rot_slider = QSlider(Qt.Horizontal); self.rot_slider.setRange(0, 360); self.rot_slider.setValue(0)
        self.mirror_check = QPushButton("Radial Mirror"); self.mirror_check.setCheckable(True)
        self.color_btn = QPushButton("Color")
        self.sens_slider = QSlider(Qt.Horizontal); self.sens_slider.setRange(1, 400); self.sens_slider.setValue(100)
        self.vol_slider = QSlider(Qt.Horizontal); self.vol_slider.setRange(0, 150); self.vol_slider.setValue(100)
        self.fps_spin = QSpinBox(); self.fps_spin.setRange(10, 120); self.fps_spin.setValue(60); self.fps_spin.setSuffix(" fps")

        # Center image & feather for radial spectrum
        self.center_btn = QPushButton("Center Image...")
        self.waviness_slider = QSlider(Qt.Horizontal); self.waviness_slider.setRange(0, 100); self.waviness_slider.setValue(30)

        # Export
        self.exp_w = QSpinBox(); self.exp_w.setRange(256, 3840); self.exp_w.setValue(1920); self.exp_w.setSuffix(" w")
        self.exp_h = QSpinBox(); self.exp_h.setRange(256, 2160); self.exp_h.setValue(1080); self.exp_h.setSuffix(" h")
        self.exp_fps = QSpinBox(); self.exp_fps.setRange(10, 120); self.exp_fps.setValue(60); self.exp_fps.setSuffix(" fps")
        self.export_btn = QPushButton("Export MP4")

        # Right column
        right = QVBoxLayout()
        # Open Audio on very top
        right.addWidget(self.open_btn)
        # Output device selector
        self.output_combo = QComboBox()
        right.addWidget(QLabel("Output Device")); right.addWidget(self.output_combo)
        # Mode below
        right.addWidget(QLabel("Mode")); right.addWidget(self.mode_combo)
        # Radial-only controls group
        self._radial_group = QWidget()
        _rg = QVBoxLayout(); _rg.setContentsMargins(0,0,0,0)
        _rg.addWidget(QLabel("Radial Rotation")); _rg.addWidget(self.rot_slider)
        _rg.addWidget(self.mirror_check)
        _rg.addWidget(self.center_btn)
        self.feather_audio_check = QPushButton("Audio-react Feather"); self.feather_audio_check.setCheckable(True)
        _rg.addWidget(self.feather_audio_check)
        self.center_motion_slider = QSlider(Qt.Horizontal); self.center_motion_slider.setRange(0, 100); self.center_motion_slider.setValue(0)
        _rg.addWidget(QLabel("Image Motion")); _rg.addWidget(self.center_motion_slider)
        self.edge_waviness_slider = QSlider(Qt.Horizontal); self.edge_waviness_slider.setRange(0, 100); self.edge_waviness_slider.setValue(30)
        _rg.addWidget(QLabel("Edge Waviness")); _rg.addWidget(self.edge_waviness_slider)
        self.feather_audio_slider = QSlider(Qt.Horizontal); self.feather_audio_slider.setRange(0, 100); self.feather_audio_slider.setValue(40)
        _rg.addWidget(QLabel("Feather Audio")); _rg.addWidget(self.feather_audio_slider)
        self._radial_group.setLayout(_rg)
        right.addWidget(self._radial_group)
        # Common controls
        right.addWidget(QLabel("Sensitivity")); right.addWidget(self.sens_slider)
        right.addWidget(QLabel("Volume")); right.addWidget(self.vol_slider)
        right.addWidget(self.color_btn)
        right.addWidget(QLabel("Realtime FPS")); right.addWidget(self.fps_spin)
        right.addSpacing(12)
        right.addWidget(QLabel("Export:"))
        row1 = QHBoxLayout(); row1.addWidget(self.exp_w); row1.addWidget(self.exp_h); right.addLayout(row1)
        right.addWidget(self.exp_fps); right.addWidget(self.export_btn)
        right.addStretch(1)
        right_wrap = QWidget(); right_wrap.setLayout(right); right_wrap.setFixedWidth(280)

        # Root layout
        root = QHBoxLayout()
        root.addWidget(self.view, stretch=1)
        root.addWidget(right_wrap, stretch=0)

        # Bottom transport bar
        self.scrub = QSlider(Qt.Horizontal); self.scrub.setRange(0, 1000)
        self.btn_to_start = QPushButton("⏮")
        self.btn_play = QPushButton("▶")
        self.btn_pause = QPushButton("⏸")

        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_to_start)
        bottom.addWidget(self.btn_play)
        bottom.addWidget(self.btn_pause)
        bottom.addWidget(self.scrub, stretch=1)

        # Compose central widget
        outer = QVBoxLayout()
        outer.addLayout(root, stretch=1)
        outer.addLayout(bottom, stretch=0)
        # Export progress bar (hidden until exporting)
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0,100)
        self.export_progress.setValue(0)
        self.export_progress.setVisible(False)
        outer.addWidget(self.export_progress)
        cw = QWidget(); cw.setLayout(outer)
        self.setCentralWidget(cw)

        # Populate outputs
        self._refresh_outputs()
        try:
            st = load_audio_state()
            dev_id = st.get('device_id')
            if dev_id is not None:
                self.engine.set_output_device_by_index(int(dev_id))
                # reflect in combo
                for idx in range(self.output_combo.count()):
                    if self.output_combo.itemData(idx) == int(dev_id):
                        self.output_combo.setCurrentIndex(idx)
                        break
        except Exception:
            pass

        # Wire signals
        self.open_btn.clicked.connect(self._open_file)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.fps_spin.valueChanged.connect(self.view.set_fps_cap)
        self.sens_slider.valueChanged.connect(lambda v: self.view.set_sensitivity(v/100.0))
        self.color_btn.clicked.connect(self._pick_color)
        self.export_btn.clicked.connect(self._export)
        # Radial controls
        self.rot_slider.valueChanged.connect(lambda v: self.view.set_radial_rotation_deg(v))
        self.mirror_check.toggled.connect(self.view.set_radial_mirror)
        self.center_motion_slider.valueChanged.connect(self.view.set_center_motion)
        self.edge_waviness_slider.valueChanged.connect(self.view.set_edge_waviness)
        self.feather_audio_check.toggled.connect(self.view.set_feather_audio_enabled)
        self.feather_audio_slider.valueChanged.connect(self.view.set_feather_audio_amount)
        if hasattr(self, 'smooth_check'):
            self.smooth_check.toggled.connect(self.view.set_radial_smooth)
        if hasattr(self, 'smooth_amt'):
            self.smooth_amt.valueChanged.connect(self.view.set_radial_smooth_amount)
        self.output_combo.currentIndexChanged.connect(self._on_output_changed)
        self.center_motion_slider.valueChanged.connect(self.view.set_center_motion)
        self.edge_waviness_slider.valueChanged.connect(self.view.set_edge_waviness)
        self.feather_audio_check.toggled.connect(self.view.set_feather_audio_enabled)
        self.feather_audio_slider.valueChanged.connect(self.view.set_feather_audio_amount)
        if hasattr(self, 'smooth_check'):
            self.smooth_check.toggled.connect(self.view.set_radial_smooth)
        if hasattr(self, 'smooth_amt'):
            self.smooth_amt.valueChanged.connect(self.view.set_radial_smooth_amount)
        # Output device
        self.output_combo.currentIndexChanged.connect(self._on_output_changed)

        # Volume wiring
        self.vol_slider.valueChanged.connect(self._on_volume_change)
        self._on_volume_change(self.vol_slider.value())  # init

        # Center image + feather wiring
        self.center_btn.clicked.connect(self.pick_center_image)
        self.waviness_slider.valueChanged.connect(self.view.set_waviness)
        self.feather_audio_check.toggled.connect(self.view.set_feather_audio_enabled)
        self.feather_audio_slider.valueChanged.connect(self.view.set_feather_audio_amount)
        if hasattr(self, 'smooth_check'):
            self.smooth_check.toggled.connect(self.view.set_radial_smooth)
        if hasattr(self, 'smooth_amt'):
            self.smooth_amt.valueChanged.connect(self.view.set_radial_smooth_amount)

        # Transport wiring
        self.btn_to_start.clicked.connect(self._jump_to_start)
        self.btn_play.clicked.connect(self._play_only)
        self.btn_pause.clicked.connect(self._pause_only)
        self.scrub.sliderPressed.connect(self._begin_scrub)
        self.scrub.sliderReleased.connect(self._end_scrub)

        self._user_scrubbing = False
        self._transport_timer = QTimer(self)
        self._transport_timer.timeout.connect(self._tick_transport)
        self._transport_timer.start(100)

        # Export progress signals
        self._export_signals = _ExportSignals(self)
        self._export_signals.progress.connect(self.export_progress.setValue)
        def _on_done():
            self.export_progress.setVisible(False)
            self.export_btn.setEnabled(True)
        def _on_failed(msg:str):
            self.export_progress.setVisible(False)
            self.export_btn.setEnabled(True)
            print('Export failed:', msg)
        self._export_signals.done.connect(_on_done)
        self._export_signals.failed.connect(_on_failed)

        # Defaults
        idx = self.mode_combo.findText("Spectrum - Radial")
        if idx != -1:
            self.mode_combo.setCurrentIndex(idx)
        self.view.set_mode(self.mode_combo.currentText())
        self._on_mode_changed(self.mode_combo.currentText())
        self.view.set_fps_cap(self.fps_spin.value())
        self.view.set_sensitivity(self.sens_slider.value() / 100.0)

    # ---------- slots ----------
    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open audio", "", "Audio (*.wav *.flac *.ogg)")
        if not path: return
        self.engine.load_file(path)
        self.setWindowTitle(f"Audio Visualizer — {os.path.basename(path)}")

    def _play_only(self):
        self.engine.play()
        self.view.reset_time()

    def _pause_only(self):
        self.engine.pause()

    def _pick_color(self):
        from PySide6.QtWidgets import QColorDialog
        col = QColorDialog.getColor()
        if col.isValid():
            self.view.set_color((col.redF(), col.greenF(), col.blueF()))

    def _export(self):
        if not self.engine.current_audio_path:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save video", "", "MP4 Video (*.mp4)")
        if not path: return
        
        import os
        if not os.path.splitext(path)[1]:
            path = path + ".mp4"
        w = self.exp_w.value(); h = self.exp_h.value(); fps = self.exp_fps.value()
        mode = self.mode_combo.currentText(); color = self.view.color; sens = getattr(self.view, 'waveform_sensitivity', 1.0)
        # Collect radial + feather + image settings
        view_state = {
            'background_path': getattr(self.view, '_bg_path', None),
            'background_scale_mode': getattr(self.view, '_bg_scale_mode', 'fill'),
            'background_offset_x': getattr(self.view, '_bg_off', (0,0))[0],
            'background_offset_y': getattr(self.view, '_bg_off', (0,0))[1],
            'background_dim_percent': getattr(self.view, '_bg_dim', 0),
            'radial_rotation_deg': getattr(self.view, 'radial_rotation_deg', 0.0),
            'radial_mirror': getattr(self.view, 'radial_mirror', False),
            'feather_enabled': getattr(self.view, 'feather_enabled', False),
            'center_motion': getattr(self.view, 'center_motion', 0),
            'edge_waviness': getattr(self.view, 'edge_waviness', getattr(self.view, 'feather_noise', 0)),
            'feather_audio_enabled': getattr(self.view, 'feather_audio_enabled', False),
            'feather_audio_amount': getattr(self.view, 'feather_audio_amount', 0),
            'center_image_path': getattr(self.view, 'center_image_path', None),
            'radial_smooth': bool(getattr(self, 'smooth_check', None) and self.smooth_check.isChecked()),
            'radial_smooth_amount': int(self.smooth_amt.value() if hasattr(self, 'smooth_amt') else 50),
            'radial_fill_enabled': bool(getattr(self.view, '_fill_enabled', False)),            'radial_fill_color': (getattr(self.view, '_fill_color', None).name(QColor.HexArgb) if getattr(self.view, '_fill_color', None) is not None else '#80FFFFFF'),
            'radial_fill_blend': getattr(self.view, '_fill_blend', 'normal'),
            'radial_fill_threshold': float(getattr(self.view, '_fill_threshold', 0.1)),
        
            'radial_waveform_smoothness': int(getattr(self.view, 'radial_wave_smoothness', 0)),
            'radial_temporal_smoothing': int(round(getattr(self.view, 'radial_temporal_alpha', 0.0) * 100)),
            'shadow_enabled': bool(getattr(self.view, '_shadow_enabled', False)),
            'shadow_opacity': int(round(getattr(self.view, '_shadow_opacity', 0.6) * 100)),
            'shadow_blur_radius': int(getattr(self.view, '_shadow_blur', 16)),
            'shadow_distance': int(getattr(self.view, '_shadow_distance', 8)),
            'shadow_angle_deg': int(getattr(self.view, '_shadow_angle_deg', 45)),
            'shadow_spread': int(getattr(self.view, '_shadow_spread', 6)),
}
        exporter = Exporter(audio_path=self.engine.current_audio_path, width=w, height=h, fps=fps,
                            mode=mode, color=color, sensitivity=sens, view_state=view_state)

        # render in background
        import threading
        self.export_btn.setEnabled(False)
        self.export_progress.setValue(0)
        self.export_progress.setVisible(True)
        def _run():
            try:
                exporter.render_to_file(path, progress_cb=lambda pct: self._export_signals.progress.emit(int(pct)))
                self._export_signals.done.emit()
            except Exception as e:
                self._export_signals.failed.emit(str(e))
        threading.Thread(target=_run, daemon=True).start()

    # Transport helpers
    def _jump_to_start(self):
        self.engine.jump_to_start()
        self.view.reset_time()
        self._tick_transport()

    def _begin_scrub(self):
        self._user_scrubbing = True

    def _end_scrub(self):
        try:
            val = self.scrub.value()
            dur = max(0.001, self.engine.get_duration_seconds())
            t = (val / 1000.0) * dur
            self.engine.seek_seconds(t)
        finally:
            self._user_scrubbing = False

    def _tick_transport(self):
        if not self.engine.current_audio_path or self._user_scrubbing:
            return
        dur = max(0.001, self.engine.get_duration_seconds())
        pos = max(0.0, min(dur, self.engine.get_position_seconds()))
        val = int((pos / dur) * 1000)
        self.scrub.blockSignals(True)
        self.scrub.setValue(val)
        self.scrub.blockSignals(False)

    # Volume handler
    def _on_volume_change(self, v):
        # Map slider percent to 0.0..1.0 for realtime playback (avoid double attenuation)
        try:
            vol = float(v) / 100.0
        except Exception:
            vol = 1.0
        self.engine.set_volume(max(0.0, min(1.0, vol)))

    # Center image handler + alias
    def _pick_center_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose center image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        ok = self.view.set_center_image(path)
        if ok:
            self.view.update()
    # Back-compat alias for signal wiring
    def pick_center_image(self):
        return self._pick_center_image()




    def _refresh_outputs(self):
        try:
            items = self.engine.list_output_devices()
        except Exception:
            items = []
        self.output_combo.blockSignals(True)
        self.output_combo.clear()
        for idx, name in items:
            label = f"[{idx}] {name}"
            self.output_combo.addItem(label, idx)
        self.output_combo.blockSignals(False)
        # Try to select current device
        try:
            cur = self.engine.output_device_index
        except Exception:
            cur = None
        if cur is not None:
            for i in range(self.output_combo.count()):
                if self.output_combo.itemData(i) == cur:
                    self.output_combo.setCurrentIndex(i)
                    break

    def _on_output_changed(self, i):
        dev_index = self.output_combo.itemData(i)
        if dev_index is not None:
            self.engine.set_output_device_by_index(dev_index)
            try:
                save_audio_state(device_id=int(dev_index), sample_rate=int(getattr(self.engine, 'sample_rate', 48000)))
            except Exception:
                pass

    # ===== Presets / Hotkeys / State =====
    def _menu_preset_save(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save Preset", "Name:")
        if not ok or not name:
            return
        self._preset_store.save(name, self._capture_state())

    def _menu_preset_load(self):
        dlg = PresetsDialog(self, self._preset_store, self._preset_store.save, self._load_preset_by_name, self._capture_state)
        dlg.exec()

    def _menu_preset_manage(self):
        dlg = PresetsDialog(self, self._preset_store, self._preset_store.save, self._load_preset_by_name, self._capture_state)
        dlg.exec()

    def _menu_hotkeys(self):
        dlg = HotkeysDialog(self, self._hotkeys)
        if dlg.exec():
            self._hotkeys = dlg.get_config()
            self._apply_hotkeys()

    def _apply_hotkeys(self):
        try:
            self._shortcuts = []
            sc = QShortcut(QKeySequence(self._hotkeys.start_stop), self); sc.activated.connect(self._toggle_play); self._shortcuts.append(sc)
            sc = QShortcut(QKeySequence(self._hotkeys.next_preset), self); sc.activated.connect(lambda: self._cycle_preset(+1)); self._shortcuts.append(sc)
            sc = QShortcut(QKeySequence(self._hotkeys.prev_preset), self); sc.activated.connect(lambda: self._cycle_preset(-1)); self._shortcuts.append(sc)
            sc = QShortcut(QKeySequence(self._hotkeys.screenshot), self); sc.activated.connect(self._screenshot); self._shortcuts.append(sc)
            sc = QShortcut(QKeySequence(self._hotkeys.toggle_safe_mode), self); sc.activated.connect(self._toggle_safe_mode); self._shortcuts.append(sc)
        except Exception:
            pass

    def _toggle_play(self):
        try:
            if self.engine.is_playing:
                self.engine.pause()
            else:
                self.engine.play()
        except Exception:
            pass

    def _cycle_preset(self, delta: int):
        names = self._preset_store.list_presets()
        if not names:
            return
        # naive cycle (you can improve to track current)
        idx = (delta) % len(names)
        self._load_preset_by_name(names[idx])

    def _screenshot(self):
        try:
            img = self.view.grab()
            import os
            out = os.path.join(os.path.expanduser("~"), "Desktop", "audiovis_screenshot.png")
            img.save(out)
        except Exception:
            pass

    def _toggle_safe_mode(self):
        try:
            self.view.set_safe_mode(True)
        except Exception:
            pass

    def _test_device(self):
        try:
            self.engine.test_output_device()
        except Exception:
            pass

    def _set_grad_colors(self, a, b):
        try:
            if a is None: a = getattr(self.view, '_grad_a', '#00ffff')
            if b is None: b = getattr(self.view, '_grad_b', '#ff00ff')
            self.view.set_gradient_colors(a, b)
        except Exception:
            pass

    def _capture_state(self) -> AppState:
        params = {}
        try:
            params['sensitivity'] = float(getattr(self, 'sens_slider').value()) / 100.0
        except Exception:
            pass
        bg = getattr(self.view, '_bg_path', None)
        bcfg = BackgroundConfig(
            path=bg,
            scale_mode=getattr(self.view, '_bg_scale_mode', 'fill'),
            offset_x=getattr(self.view, '_bg_off', (0,0))[0],
            offset_y=getattr(self.view, '_bg_off', (0,0))[1],
            dim_percent=getattr(self.view, '_bg_dim', 0)
        )
        scfg = ShadowConfig(
            enabled=getattr(self.view, '_shadow_enabled', False),
            opacity_percent=int(getattr(self.view, '_shadow_opacity', 0.5) * 100),
            blur_radius=int(getattr(self.view, '_shadow_blur', 8))
        )
        rfc = RadialFillConfig(
            enabled=getattr(self.view, '_fill_enabled', False),
            color=getattr(self.view, '_fill_color', None).name() if getattr(self.view, '_fill_color', None) else '#80FFFFFF',
            blend=getattr(self.view, '_fill_blend', 'normal'),
            threshold=float(getattr(self.view, '_fill_threshold', 0.1))
        )
        st = AppState(
            version=self._app_state.version,
            theme=self._app_state.theme,
            audio=self._app_state.audio,
            visualizer=self.view._mode,
            params=params,
            export=self._app_state.export,
            hotkeys=self._hotkeys,
            background=bcfg,
            shadow=scfg,
            radial_fill=rfc
        )
        return st

    def _load_preset_by_name(self, name: str):
        st = self._preset_store.load(name)
        self._apply_state(st)

    def _apply_state(self, st: AppState):
        self._app_state = st
        self._hotkeys = st.hotkeys
        self._apply_hotkeys()
        try:
            self.view.set_background_config(st.background)
            self.view.set_shadow_enabled(st.shadow.enabled)
            self.view.set_shadow_opacity(st.shadow.opacity_percent)
            self.view.set_radial_fill_enabled(st.radial_fill.enabled)
            self.view.set_radial_fill_color(st.radial_fill.color)
            self.view.set_radial_fill_blend(st.radial_fill.blend)
            self.view.set_radial_fill_threshold(st.radial_fill.threshold)
        except Exception:
            pass

    

# ----- Presets/Hotkeys menus -----
def _menu_preset_save(self):
    from PySide6.QtWidgets import QInputDialog
    name, ok = QInputDialog.getText(self, "Save Preset", "Name:")
    if not ok or not name: return
    state = self._capture_state()
    self._preset_store.save(name, state)

def _menu_preset_load(self):
    dlg = PresetsDialog(self, self._preset_store, self._preset_store.save, self._load_preset_by_name, self._capture_state)
    dlg.exec()

def _menu_preset_manage(self):
    dlg = PresetsDialog(self, self._preset_store, self._preset_store.save, self._load_preset_by_name, self._capture_state)
    dlg.exec()

def _menu_hotkeys(self):
    dlg = HotkeysDialog(self, self._hotkeys)
    if dlg.exec():
        self._hotkeys = dlg.get_config()
        self._apply_hotkeys()
        # apply persisted audio
        try:
            if self._app_state.audio.sample_rate:
                self.engine.set_sample_rate(self._app_state.audio.sample_rate)
        except Exception:
            pass
        try:
            if self._app_state.audio.device_id is not None:
                self.engine.set_output_device_by_index(self._app_state.audio.device_id)
        except Exception:
            pass


def _apply_hotkeys(self):
    try:
        # Clear and rebind
        self._shortcuts = []
        sc = QShortcut(QKeySequence(self._hotkeys.start_stop), self); sc.activated.connect(self._toggle_play); self._shortcuts.append(sc)
        sc = QShortcut(QKeySequence(self._hotkeys.next_preset), self); sc.activated.connect(lambda: self._cycle_preset(+1)); self._shortcuts.append(sc)
        sc = QShortcut(QKeySequence(self._hotkeys.prev_preset), self); sc.activated.connect(lambda: self._cycle_preset(-1)); self._shortcuts.append(sc)
        sc = QShortcut(QKeySequence(self._hotkeys.screenshot), self); sc.activated.connect(self._screenshot); self._shortcuts.append(sc)
        sc = QShortcut(QKeySequence(self._hotkeys.toggle_safe_mode), self); sc.activated.connect(self._toggle_safe_mode); self._shortcuts.append(sc)
    except Exception:
        pass

def _toggle_play(self):
    try:
        if self.engine.is_playing:
            self.engine.pause()
        else:
            self.engine.play()
    except Exception:
        pass

def _cycle_preset(self, delta:int):
    names = self._preset_store.list_presets()
    if not names: return
    # pick next from current theme name in state
    try:
        cur = names.index(self._app_state.theme)
    except Exception:
        cur = 0
    idx = (cur + delta) % len(names)
    self._load_preset_by_name(names[idx])

def _screenshot(self):
    try:
        img = self.view.grab()
        out = os.path.join(os.path.expanduser("~"), "Desktop", "audiovis_screenshot.png")
        img.save(out)
    except Exception:
        pass

def _toggle_safe_mode(self):
    try:
        self.view.set_safe_mode(True)
    except Exception:
        pass

def _capture_state(self) -> AppState:
    # Collect parameters from current UI if available
    params = {}
    try:
        params['sensitivity'] = float(getattr(self, 'sens_slider').value())/100.0
    except Exception:
        pass
    bg = getattr(self.view, '_bg_path', None)
    bcfg = BackgroundConfig(path=bg, scale_mode=getattr(self.view,'_bg_scale_mode','fill'), offset_x=getattr(self.view,'_bg_off',(0,0))[0], offset_y=getattr(self.view,'_bg_off',(0,0))[1], dim_percent=getattr(self.view,'_bg_dim',0))
    scfg = ShadowConfig(enabled=getattr(self.view,'_shadow_enabled', False), opacity_percent=int(getattr(self.view,'_shadow_opacity',0.5)*100), blur_radius=int(getattr(self.view,'_shadow_blur',8)))
    fcfg = RadialFillConfig(enabled=getattr(self.view,'_fill_enabled', False), color=getattr(self.view,'_fill_color').name(QColor.HexArgb) if hasattr(self.view,'_fill_color') else '#80FFFFFF', blend=getattr(self.view,'_fill_blend','normal'), threshold=float(getattr(self.view,'_fill_threshold',0.1)))
    exp = self._app_state.export
    return AppState(version=1, theme=self._app_state.theme, audio=self._app_state.audio, visualizer=self.view._mode, params=params, export=exp, hotkeys=self._hotkeys, background=bcfg, shadow=scfg, radial_fill=fcfg)

def _load_preset_by_name(self, name:str):
    st = self._preset_store.load(name)
    self._apply_state(st)

def _apply_state(self, st: AppState):
    self._app_state = st
    try:
        self.view.set_background_config(st.background)
        self.view.set_shadow_enabled(st.shadow.enabled)
        self.view.set_shadow_opacity(st.shadow.opacity_percent)
        self.view.set_radial_fill_enabled(st.radial_fill.enabled)
        self.view.set_radial_fill_color(st.radial_fill.color)
        self.view.set_radial_fill_blend(st.radial_fill.blend)
        self.view.set_radial_fill_threshold(st.radial_fill.threshold)
        self._hotkeys = st.hotkeys
        self._apply_hotkeys()
        # apply persisted audio
        try:
            if self._app_state.audio.sample_rate:
                self.engine.set_sample_rate(self._app_state.audio.sample_rate)
        except Exception:
            pass
        try:
            if self._app_state.audio.device_id is not None:
                self.engine.set_output_device_by_index(self._app_state.audio.device_id)
        except Exception:
            pass

    except Exception as e:
        print("Apply state error", e)

    def _set_grad_colors(self, a, b):
        try:
            if a is None: a = getattr(self.view,'_grad_a','#00ffff')
            if b is None: b = getattr(self.view,'_grad_b','#ff00ff')
            self.view.set_gradient_colors(a,b)
        except Exception:
            pass

    def _test_device(self):
        try:
            self.engine.test_output_device()
        except Exception:
            pass

    def _randomize_params(self):
        import random
        # intensity heuristic: medium
        def rand_color():
            return '#%02x%02x%02x' % (random.randint(0,255), random.randint(0,255), random.randint(0,255))
        try:
            self.view.set_gradient_colors(rand_color(), rand_color())
            self.view.set_radial_fill_threshold(random.uniform(0.05, 0.3))
        except Exception:
            pass