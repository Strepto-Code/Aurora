import os
import sys
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QProcess, QProcessEnvironment
from PySide6.QtGui import QShortcut, QKeySequence, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QFileDialog, QComboBox, QPushButton, QSlider,
    QLabel, QHBoxLayout, QVBoxLayout, QSpinBox, QProgressBar, QCheckBox,
    QTabWidget, QScrollArea, QGroupBox, QFormLayout, QSizePolicy,
    QColorDialog,
)

from audio.input import AudioEngine
from export.exporter import Exporter, list_gpu_export_devices
from widgets.rt_widget import RTVisualizerWidget
from ui.components import (
    BackgroundPanel, GradientPanel, HotkeysDialog, PresetsDialog,
    RadialFillPanel, ShadowPanel, GlowPanel,
)
from config.settings import (
    PresetStore, DEFAULT_STATE, AppState,
    BackgroundConfig, ShadowConfig, RadialFillConfig,
    load_audio_state, save_audio_state,
)
from config.persist import load_state_ini, save_state_ini

class _ExportSignals(QObject):
    progress = Signal(int)
    done = Signal()
    failed = Signal(str)

MODES = [
    "Spectrum - Radial",
    "Spectrum - Linear",
    "Waveform - Linear",
    "Waveform - Circular",
]

class MainWindow(QMainWindow):
    def _on_mode_changed(self, idx):
        mode = self.mode_combo.currentText()
        try:
            self._radial_group.setVisible("Radial" in mode or "Circular" in mode)
        except Exception:
            pass
        try:
            self.view.set_mode(mode)
        except Exception:
            pass

    def _on_output_changed(self, idx):
        try:
            dev_idx = self.output_combo.itemData(idx)
            if dev_idx is None:
                return
            self.engine.set_output_device_by_index(dev_idx)
        except Exception:
            pass

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aurora Visualizer")

        self.engine = AudioEngine(sample_rate=48000, block_size=1024)
        self.view = RTVisualizerWidget(self.engine)

        self._preset_store = PresetStore()
        self._app_state = DEFAULT_STATE
        self._hotkeys = DEFAULT_STATE.hotkeys

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        self._user_scrubbing = False
        self._transport_timer = QTimer(self)
        self._transport_timer.timeout.connect(self._tick_transport)
        self._transport_timer.start(100)

        root = QWidget()
        self.setCentralWidget(root)

        self.open_btn = QPushButton("Open Audio")
        self.output_combo = QComboBox()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODES)

        self.color_btn = QPushButton("Color...")

        self.sens_slider = QSlider(Qt.Horizontal)
        self.sens_slider.setRange(1, 400)
        self.sens_slider.setValue(100)

        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 200)
        self.vol_slider.setValue(100)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(10, 240)
        self.fps_spin.setValue(60)
        self.fps_spin.setSuffix(" fps")

        self.rot_slider = QSlider(Qt.Horizontal)
        self.rot_slider.setRange(0, 360)
        self.rot_slider.setValue(0)

        self.mirror_check = QCheckBox("Mirror")
        self.mirror_check.setChecked(True)

        self.smooth_amt = QSpinBox()
        self.smooth_amt.setRange(0, 100)
        self.smooth_amt.setValue(50)

        self.radial_smoothness_slider = QSlider(Qt.Horizontal)
        self.radial_smoothness_slider.setRange(0, 100)
        self.radial_smoothness_slider.setValue(50)

        self.radial_temporal_slider = QSlider(Qt.Horizontal)
        self.radial_temporal_slider.setRange(0, 95)
        self.radial_temporal_slider.setValue(30)

        self.center_btn = QPushButton("Center Image...")
        self.center_zoom_slider = QSlider(Qt.Horizontal)
        self.center_zoom_slider.setRange(50, 250)
        self.center_zoom_slider.setValue(100)

        self.center_motion_slider = QSlider(Qt.Horizontal)
        self.center_motion_slider.setRange(0, 100)
        self.center_motion_slider.setValue(0)

        self.edge_waviness_slider = QSlider(Qt.Horizontal)
        self.edge_waviness_slider.setRange(0, 100)
        self.edge_waviness_slider.setValue(30)

        self.feather_audio_check = QCheckBox("Feather Audio")
        self.feather_audio_check.setChecked(False)

        self.feather_audio_slider = QSlider(Qt.Horizontal)
        self.feather_audio_slider.setRange(0, 100)
        self.feather_audio_slider.setValue(40)

        self.exp_w = QSpinBox()
        self.exp_w.setRange(64, 3840)
        self.exp_w.setValue(1280)

        self.exp_h = QSpinBox()
        self.exp_h.setRange(64, 2160)
        self.exp_h.setValue(720)

        self.exp_fps = QSpinBox()
        self.exp_fps.setRange(10, 120)
        self.exp_fps.setValue(60)
        self.exp_fps.setSuffix(" fps")

        self.exp_gpu = QCheckBox("GPU Encode")
        self.exp_gpu.setChecked(False)
        self.exp_gpu_device = QComboBox()
        self.exp_gpu_device.setEnabled(False)

        self.export_btn = QPushButton("Export MP4")

        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 100)
        self.export_progress.setValue(0)
        self.export_progress.setVisible(False)

        self._build_menu()
        self._apply_hotkeys()

        self.bg_panel = BackgroundPanel(self, self.view.set_background_config)
        self.grad_panel = GradientPanel(
            self,
            lambda a, b: self._set_grad_colors(a, b),
            self.view.set_gradient_curve,
            self.view.set_gradient_clamp,
            self.view.set_gradient_smoothing,
        )
        self.shadow_panel = ShadowPanel(
            self,
            self.view.set_shadow_enabled,
            self.view.set_shadow_opacity,
            getattr(self.view, 'set_shadow_blur_radius', None),
            getattr(self.view, 'set_shadow_distance', None),
            getattr(self.view, 'set_shadow_angle_deg', None),
            getattr(self.view, 'set_shadow_spread', None),
        )
        self.glow_panel = GlowPanel(
            self,
            self.view.set_glow_enabled,
            self.view.set_glow_color,
            self.view.set_glow_radius,
            self.view.set_glow_strength,
        )
        self.radial_panel = RadialFillPanel(
            self,
            self.view.set_radial_fill_enabled,
            self.view.set_radial_fill_color,
            self.view.set_radial_fill_blend,
            self.view.set_radial_fill_threshold,
        )

        sidebar_inner = QWidget()
        sb = QVBoxLayout(sidebar_inner)
        sb.setContentsMargins(6, 6, 6, 6)
        sb.setSpacing(6)

        grp_audio = QGroupBox("Audio")
        fa = QFormLayout(grp_audio)
        fa.setContentsMargins(6, 4, 6, 4)
        fa.setSpacing(5)
        fa.addRow(self.open_btn)
        fa.addRow("Output", self.output_combo)
        fa.addRow("Volume", self.vol_slider)
        sb.addWidget(grp_audio)

        grp_vis = QGroupBox("Visualization")
        fv = QFormLayout(grp_vis)
        fv.setContentsMargins(6, 4, 6, 4)
        fv.setSpacing(5)
        fv.addRow("Mode", self.mode_combo)
        fv.addRow("Sensitivity", self.sens_slider)
        fv.addRow(self.color_btn)
        fv.addRow("FPS Cap", self.fps_spin)
        sb.addWidget(grp_vis)

        self._radial_group = QGroupBox("Radial / Circular")
        fr = QFormLayout(self._radial_group)
        fr.setContentsMargins(6, 4, 6, 4)
        fr.setSpacing(5)
        fr.addRow("Rotation", self.rot_slider)
        fr.addRow(self.mirror_check)
        fr.addRow("Smooth", self.smooth_amt)
        fr.addRow("Spatial", self.radial_smoothness_slider)
        fr.addRow("Temporal", self.radial_temporal_slider)
        fr.addRow(self.center_btn)
        fr.addRow("Zoom", self.center_zoom_slider)
        fr.addRow("Motion", self.center_motion_slider)
        fr.addRow("Waviness", self.edge_waviness_slider)
        fr.addRow(self.feather_audio_check)
        fr.addRow("Audio Amt", self.feather_audio_slider)
        sb.addWidget(self._radial_group)

        grp_fx = QGroupBox("Visual FX")
        fx_lay = QVBoxLayout(grp_fx)
        fx_lay.setContentsMargins(2, 2, 2, 2)
        fx_lay.setSpacing(0)
        self.fxTabs = QTabWidget()
        self.fxTabs.setDocumentMode(True)
        self.fxTabs.addTab(self.bg_panel, "BG")
        self.fxTabs.addTab(self.grad_panel, "Grad")
        self.fxTabs.addTab(self.shadow_panel, "Shadow")
        self.fxTabs.addTab(self.glow_panel, "Glow")
        self.fxTabs.addTab(self.radial_panel, "Fill")
        fx_lay.addWidget(self.fxTabs)
        sb.addWidget(grp_fx)

        grp_exp = QGroupBox("Export")
        fe = QFormLayout(grp_exp)
        fe.setContentsMargins(6, 4, 6, 4)
        fe.setSpacing(5)
        res_row = QHBoxLayout()
        res_row.setContentsMargins(0, 0, 0, 0)
        res_row.setSpacing(4)
        res_row.addWidget(self.exp_w)
        lbl_x = QLabel("\u00d7")
        lbl_x.setFixedWidth(12)
        lbl_x.setAlignment(Qt.AlignCenter)
        res_row.addWidget(lbl_x)
        res_row.addWidget(self.exp_h)
        res_wrap = QWidget()
        res_wrap.setContentsMargins(0, 0, 0, 0)
        res_wrap.setLayout(res_row)
        fe.addRow("Size", res_wrap)
        fe.addRow("FPS", self.exp_fps)
        fe.addRow(self.exp_gpu)
        fe.addRow("Device", self.exp_gpu_device)
        fe.addRow(self.export_btn)
        fe.addRow(self.export_progress)
        sb.addWidget(grp_exp)

        sb.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(sidebar_inner)
        scroll.setFixedWidth(280)
        scroll.setFrameShape(QScrollArea.NoFrame)

        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setRange(0, 1000)
        self.btn_to_start = QPushButton("\u23ee")
        self.btn_play = QPushButton("\u25b6")
        self.btn_pause = QPushButton("\u23f8")
        for tb in (self.btn_to_start, self.btn_play, self.btn_pause):
            tb.setFixedSize(32, 26)

        transport = QHBoxLayout()
        transport.setContentsMargins(4, 2, 4, 2)
        transport.setSpacing(2)
        transport.addWidget(self.btn_to_start)
        transport.addWidget(self.btn_play)
        transport.addWidget(self.btn_pause)
        transport.addWidget(self.scrub, 1)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(0)
        main_row.addWidget(self.view, 1)
        main_row.addWidget(scroll, 0)
        outer.addLayout(main_row, 1)
        outer.addLayout(transport, 0)

        self._refresh_output_devices()

        self.open_btn.clicked.connect(self._open_audio)
        self.export_btn.clicked.connect(self._export)
        self.exp_gpu.toggled.connect(self._on_gpu_export_toggled)

        self.btn_to_start.clicked.connect(self._jump_to_start)
        self.btn_play.clicked.connect(self._play_only)
        self.btn_pause.clicked.connect(self._pause_only)
        self.scrub.sliderPressed.connect(self._begin_scrub)
        self.scrub.sliderReleased.connect(self._end_scrub)

        self.rot_slider.valueChanged.connect(lambda v: self.view.set_radial_rotation_deg(v))
        self.mirror_check.toggled.connect(self.view.set_radial_mirror)
        self.center_motion_slider.valueChanged.connect(self.view.set_center_motion)
        self.center_zoom_slider.valueChanged.connect(self.view.set_center_image_zoom)
        self.edge_waviness_slider.valueChanged.connect(self.view.set_edge_waviness)
        self.feather_audio_check.toggled.connect(self.view.set_feather_audio_enabled)
        self.feather_audio_slider.valueChanged.connect(self.view.set_feather_audio_amount)
        self.smooth_amt.valueChanged.connect(self.view.set_radial_smooth_amount)
        self.radial_smoothness_slider.valueChanged.connect(
            lambda v: self.view.set_radial_waveform_smoothness(v)
        )
        self.radial_temporal_slider.valueChanged.connect(
            lambda v: self.view.set_radial_temporal_smoothing(v)
        )

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.sens_slider.valueChanged.connect(lambda v: self.view.set_waveform_sensitivity(v / 100.0))
        self.vol_slider.valueChanged.connect(lambda v: self.engine.set_volume(v / 100.0))
        self.fps_spin.valueChanged.connect(self._set_realtime_fps)
        self.output_combo.currentIndexChanged.connect(self._on_output_changed)

        self.center_btn.clicked.connect(self._choose_center_image)
        self.color_btn.clicked.connect(self._choose_color)

        self._apply_stylesheet()

        self._on_mode_changed(0)
        self._load_audio_state()
        self._refresh_gpu_export_options()
        self._load_state_ini()

    def _build_menu(self):
        mb = self.menuBar()
        filem = mb.addMenu("&File")
        filem.addAction("Open Audio...").triggered.connect(self._open_audio)
        filem.addAction("Export...").triggered.connect(self._export)
        filem.addSeparator()
        filem.addAction("Quit").triggered.connect(self.close)

        presetm = mb.addMenu("&Presets")
        presetm.addAction("Save Preset...").triggered.connect(self._menu_preset_save)
        presetm.addAction("Load Preset...").triggered.connect(self._menu_preset_load)

        hotm = mb.addMenu("&Hotkeys")
        hotm.addAction("Edit Hotkeys...").triggered.connect(self._menu_hotkeys)

    def _menu_preset_save(self):
        dlg = PresetsDialog(self, self._preset_store, mode="save", state=self._get_state_snapshot())
        dlg.exec()

    def _menu_preset_load(self):
        dlg = PresetsDialog(self, self._preset_store, mode="load")
        if dlg.exec():
            state = dlg.loaded_state
            if state:
                self._apply_state(state)

    def _menu_hotkeys(self):
        dlg = HotkeysDialog(self, self._hotkeys)
        if dlg.exec():
            self._hotkeys = dlg.get_config()
            self._apply_hotkeys()

    def _apply_hotkeys(self):
        try:
            for sc in getattr(self, "_shortcuts", []):
                sc.setParent(None)
        except Exception:
            pass
        self._shortcuts = []
        try:
            sc = QShortcut(QKeySequence(self._hotkeys.start_stop), self)
            sc.activated.connect(self._toggle_play_pause)
            self._shortcuts.append(sc)

            sc = QShortcut(QKeySequence(self._hotkeys.next_preset), self)
            sc.activated.connect(lambda: self._cycle_preset(+1))
            self._shortcuts.append(sc)

            sc = QShortcut(QKeySequence(self._hotkeys.prev_preset), self)
            sc.activated.connect(lambda: self._cycle_preset(-1))
            self._shortcuts.append(sc)

            sc = QShortcut(QKeySequence(self._hotkeys.screenshot), self)
            sc.activated.connect(self._screenshot)
            self._shortcuts.append(sc)

            sc = QShortcut(QKeySequence(self._hotkeys.toggle_safe_mode), self)
            sc.activated.connect(self._toggle_safe_mode)
            self._shortcuts.append(sc)
        except Exception:
            pass

    def _cycle_preset(self, step):
        try:
            names = self._preset_store.list_names()
            if not names:
                return
            cur = getattr(self, "_current_preset_name", None)
            if cur in names:
                i = names.index(cur)
            else:
                i = 0
            i = (i + step) % len(names)
            name = names[i]
            st = self._preset_store.load(name)
            if st:
                self._apply_state(st)
                self._current_preset_name = name
        except Exception:
            pass

    def _screenshot(self):
        try:
            path, _ = QFileDialog.getSaveFileName(self, "Save screenshot", "", "PNG (*.png)")
            if not path:
                return
            if not os.path.splitext(path)[1]:
                path = path + ".png"
            img = self.view.grabFramebuffer()
            img.save(path)
        except Exception:
            pass

    def _toggle_safe_mode(self):
        try:
            self.view.set_safe_mode(not getattr(self.view, "safe_mode", False))
        except Exception:
            pass

    def _refresh_output_devices(self):
        try:
            items = self.engine.list_output_devices()
        except Exception:
            items = []
        self.output_combo.blockSignals(True)
        self.output_combo.clear()
        for idx, name in items:
            self.output_combo.addItem(f"[{idx}] {name}", idx)
        self.output_combo.blockSignals(False)

        try:
            cur = self.engine.output_device_index
        except Exception:
            cur = None
        if cur is not None:
            for i in range(self.output_combo.count()):
                if self.output_combo.itemData(i) == cur:
                    self.output_combo.setCurrentIndex(i)
                    break

    def _set_realtime_fps(self, fps):
        try:
            self.view.set_fps_cap(int(fps))
        except Exception:
            pass

    def _on_gpu_export_toggled(self, checked: bool) -> None:
        try:
            self.exp_gpu_device.setEnabled(bool(checked) and self.exp_gpu.isEnabled())
        except Exception:
            pass

    def _refresh_gpu_export_options(self) -> None:
        opts = []
        try:
            opts = list_gpu_export_devices()
        except Exception:
            opts = []

        self._gpu_export_opts = opts
        try:
            self.exp_gpu_device.blockSignals(True)
            self.exp_gpu_device.clear()
            for o in opts:
                self.exp_gpu_device.addItem(o.get("label", "GPU"), o.get("id", ""))
            if not opts:
                self.exp_gpu_device.addItem("No supported GPU encoders found", "")
            self.exp_gpu_device.blockSignals(False)
        except Exception:
            pass

        if not opts:
            try:
                self.exp_gpu.setChecked(False)
                self.exp_gpu.setEnabled(False)
                self.exp_gpu_device.setEnabled(False)
            except Exception:
                pass
        else:
            try:
                self.exp_gpu.setEnabled(True)
                self.exp_gpu_device.setEnabled(bool(self.exp_gpu.isChecked()))
            except Exception:
                pass

    def _tick(self):
        try:
            self.view.update()
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self._save_audio_state()
        except Exception:
            pass
        try:
            self._save_state_ini()
        except Exception:
            pass
        try:
            self.engine.close()
        except Exception:
            pass
        super().closeEvent(event)

    def _open_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open audio",
            "",
            "Audio Files (*.wav *.wave *.flac *.ogg *.oga *.mp3);;All Files (*)",
        )
        if not path:
            return
        try:
            self.engine.load_file(path)
            self.setWindowTitle(f"Aurora Visualizer — {os.path.basename(path)}")
            self.engine.play()
            try:
                self.view.reset_time()
            except Exception:
                pass
            self._tick_transport()
        except Exception:
            return

    def _toggle_play_pause(self):
        try:
            if self.engine._playing:
                self.engine.pause()
            else:
                self.engine.play()
                try:
                    self.view.reset_time()
                except Exception:
                    pass
        except Exception:
            pass

    def _jump_to_start(self):
        try:
            self.engine.jump_to_start()
            try:
                self.view.reset_time()
            except Exception:
                pass
            self._tick_transport()
        except Exception:
            pass

    def _tick_transport(self):
        try:
            if not self.engine.current_audio_path or self._user_scrubbing:
                return
            dur = max(0.001, float(self.engine.get_duration_seconds()))
            pos = max(0.0, min(dur, float(self.engine.get_position_seconds())))
            val = int((pos / dur) * 1000)
            self.scrub.blockSignals(True)
            self.scrub.setValue(val)
            self.scrub.blockSignals(False)
        except Exception:
            pass

    def _end_scrub(self):
        try:
            val = int(self.scrub.value())
            dur = max(0.001, float(self.engine.get_duration_seconds()))
            t = (val / 1000.0) * dur
            self.engine.seek_seconds(t)
        finally:
            self._user_scrubbing = False

    def _begin_scrub(self):
        self._user_scrubbing = True

    def _pause_only(self):
        try:
            self.engine.pause()
        except Exception:
            pass

    def _play_only(self):
        try:
            self.engine.play()
            try:
                self.view.reset_time()
            except Exception:
                pass
        except Exception:
            pass

    def _choose_center_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose center image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        try:
            ok = self.view.set_center_image(path)
            if ok:
                self.view.update()
        except Exception:
            pass

    def _set_grad_colors(self, a, b):
        try:
            self.view.set_gradient_colors(a, b)
        except Exception:
            pass

    def _choose_color(self):
        try:
            current = self.view.color
            if isinstance(current, QColor):
                init_color = current
            else:
                r, g, b = current
                init_color = QColor(int(r * 255), int(g * 255), int(b * 255))
            col = QColorDialog.getColor(init_color, self)
            if col.isValid():
                self.view.set_color((col.redF(), col.greenF(), col.blueF()))
        except Exception:
            pass

    def _get_state_snapshot(self):
        try:
            bg = getattr(self.view, "_bg_cfg", BackgroundConfig())
        except Exception:
            bg = BackgroundConfig()
        try:
            sh = ShadowConfig(
                enabled=bool(self.view._shadow_enabled),
                opacity_percent=int(float(self.view._shadow_opacity) * 100),
                blur_radius=int(self.view._shadow_blur),
                distance=int(self.view._shadow_distance),
                angle_deg=int(self.view._shadow_angle_deg),
                spread=int(self.view._shadow_spread),
            )
        except Exception:
            sh = ShadowConfig()
        try:
            rf = RadialFillConfig(
                enabled=bool(self.view._fill_enabled),
                color=self.view._fill_color.name(QColor.HexArgb),
                blend=str(self.view._fill_blend),
                threshold=float(self.view._fill_threshold),
            )
        except Exception:
            rf = RadialFillConfig()

        return AppState(background=bg, shadow=sh, radial_fill=rf, hotkeys=self._hotkeys)

    def _apply_state(self, st: AppState):
        try:
            self.view.set_background_config(st.background)
        except Exception:
            pass
        try:
            self.view.set_shadow_enabled(st.shadow.enabled)
            self.view.set_shadow_opacity(st.shadow.opacity_percent)
            self.view.set_shadow_blur_radius(st.shadow.blur_radius)
            self.view.set_shadow_distance(st.shadow.distance)
            self.view.set_shadow_angle_deg(st.shadow.angle_deg)
            self.view.set_shadow_spread(st.shadow.spread)
        except Exception:
            pass
        try:
            self.view.set_radial_fill_enabled(st.radial_fill.enabled)
            self.view.set_radial_fill_color(st.radial_fill.color)
            self.view.set_radial_fill_blend(st.radial_fill.blend)
            self.view.set_radial_fill_threshold(st.radial_fill.threshold)
        except Exception:
            pass

    def _load_audio_state(self):
        st = load_audio_state()
        if not st:
            return
        try:
            dev = st.get("output_device_index", None)
            if dev is not None:
                self.engine.set_output_device_by_index(int(dev))
                self._refresh_output_devices()
                for i in range(self.output_combo.count()):
                    if self.output_combo.itemData(i) == int(dev):
                        self.output_combo.setCurrentIndex(i)
                        break
        except Exception:
            pass
        try:
            self.vol_slider.setValue(int(st.get("volume", 100)))
            self.engine.set_volume(self.vol_slider.value() / 100.0)
        except Exception:
            pass

    def _save_audio_state(self):
        try:
            st = {
                "output_device_index": self.engine.output_device_index,
                "volume": int(self.vol_slider.value()),
            }
            save_audio_state(st)
        except Exception:
            pass

    def _collect_state_ini(self):
        def _color_hex() -> str:
            c = self.view.color
            try:
                if isinstance(c, QColor):
                    return c.name()
            except Exception:
                pass
            try:
                r, g, b = c
                r = int(max(0, min(255, round(float(r) * 255))))
                g = int(max(0, min(255, round(float(g) * 255))))
                b = int(max(0, min(255, round(float(b) * 255))))
                return QColor(r, g, b).name()
            except Exception:
                return ""

        st = {
            "mode": self.mode_combo.currentText(),
            "realtime_fps": int(self.fps_spin.value()),
            "sensitivity": float(self.view.waveform_sensitivity),
            "volume": float(self.vol_slider.value() / 100.0),
            "output_device_index": self.engine.output_device_index,
            "color": _color_hex(),
            "fx_tab": self.fxTabs.currentIndex(),

            "radial_rotation_deg": float(self.view.radial_rotation_deg),
            "radial_mirror": bool(self.view.radial_mirror),
            "radial_smooth_amount": int(self.smooth_amt.value() if self.smooth_amt is not None else 50),
            "center_motion": int(self.view.center_motion),
            "center_image_zoom": int(self.view.center_image_zoom),
            "edge_waviness": int(self.view.edge_waviness),
            "feather_audio_enabled": bool(self.view.feather_audio_enabled),
            "feather_audio_amount": int(self.view.feather_audio_amount),
            "center_image_path": str(self.view.center_image_path or ""),
            "radial_waveform_smoothness": int(self.view.radial_wave_smoothness),
            "radial_temporal_smoothing": int(float(self.view.radial_temporal_alpha) * 100.0),

            "export_width": int(self.exp_w.value()),
            "export_height": int(self.exp_h.value()),
            "export_fps": int(self.exp_fps.value()),
            "export_gpu": bool(self.exp_gpu.isChecked()),
            "export_gpu_device": str(self.exp_gpu_device.currentData() or ""),

            "bg_path": str(self.view._bg_path or ""),
            "bg_scale_mode": str(self.view._bg_scale_mode),
            "bg_offset_x": int(self.view._bg_off[0]),
            "bg_offset_y": int(self.view._bg_off[1]),
            "bg_dim_percent": int(self.view._bg_dim),

            "grad_a": self.view._grad_a.name(),
            "grad_b": self.view._grad_b.name(),
            "grad_curve": str(self.view._grad_curve),
            "grad_min": float(self.view._grad_min),
            "grad_max": float(self.view._grad_max),
            "grad_smoothing": float(self.view._amp_alpha),

            "shadow_enabled": bool(self.view._shadow_enabled),
            "shadow_opacity": int(float(self.view._shadow_opacity) * 100.0),
            "shadow_blur_radius": int(self.view._shadow_blur),
            "shadow_distance": int(self.view._shadow_distance),
            "shadow_angle_deg": int(self.view._shadow_angle_deg),
            "shadow_spread": int(self.view._shadow_spread),

            "glow_enabled": bool(self.view._glow_enabled),
            "glow_color": self.view._glow_color.name(QColor.HexArgb),
            "glow_radius": int(self.view._glow_radius),
            "glow_strength": int(float(self.view._glow_strength) * 100.0),

            "fill_enabled": bool(self.view._fill_enabled),
            "fill_color": self.view._fill_color.name(QColor.HexArgb),
            "fill_blend": str(self.view._fill_blend),
            "fill_threshold": float(self.view._fill_threshold),
        }

        return st

    def _save_state_ini(self) -> None:
        try:
            save_state_ini(self._collect_state_ini())
        except Exception:
            pass

    def _load_state_ini(self) -> None:
        st = load_state_ini()
        if not st:
            return

        try:
            fx_tab = int(st.get("fx_tab", 0))
            if 0 <= fx_tab < self.fxTabs.count():
                self.fxTabs.setCurrentIndex(fx_tab)
        except Exception:
            pass

        try:
            mode = str(st.get("mode") or "")
            if mode in MODES:
                i = MODES.index(mode)
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(i)
                self.mode_combo.blockSignals(False)
                self._on_mode_changed(i)
        except Exception:
            try:
                self.mode_combo.blockSignals(False)
            except Exception:
                pass

        try:
            self.fps_spin.setValue(int(st.get("realtime_fps", self.fps_spin.value())))
        except Exception:
            pass

        try:
            sens = float(st.get("sensitivity", 1.0))
            self.sens_slider.setValue(int(max(self.sens_slider.minimum(), min(self.sens_slider.maximum(), round(sens * 100.0)))))
        except Exception:
            pass

        try:
            vol = float(st.get("volume", 1.0))
            self.vol_slider.setValue(int(max(0, min(200, round(vol * 100.0)))))
            self.engine.set_volume(self.vol_slider.value() / 100.0)
        except Exception:
            pass

        try:
            odi = int(st.get("output_device_index", -1))
            if odi >= 0:
                self.engine.set_output_device_by_index(odi)
                self._refresh_output_devices()
                for i in range(self.output_combo.count()):
                    if self.output_combo.itemData(i) == int(odi):
                        self.output_combo.setCurrentIndex(i)
                        break
        except Exception:
            pass

        try:
            c = str(st.get("color", "") or "").strip()
            if c:
                qc = QColor(c)
                if qc.isValid():
                    self.view.set_color((qc.redF(), qc.greenF(), qc.blueF()))
        except Exception:
            pass

        try:
            self.exp_w.setValue(int(st.get("export_width", self.exp_w.value())))
            self.exp_h.setValue(int(st.get("export_height", self.exp_h.value())))
            self.exp_fps.setValue(int(st.get("export_fps", self.exp_fps.value())))
            want_gpu = bool(st.get("export_gpu", False))
            self.exp_gpu.setChecked(want_gpu if self.exp_gpu.isEnabled() else False)
            saved_dev = str(st.get("export_gpu_device", "") or "")
            if saved_dev:
                for i in range(self.exp_gpu_device.count()):
                    if str(self.exp_gpu_device.itemData(i) or "") == saved_dev:
                        self.exp_gpu_device.setCurrentIndex(i)
                        break
            self.exp_gpu_device.setEnabled(bool(self.exp_gpu.isChecked()) and self.exp_gpu.isEnabled())
        except Exception:
            pass

        try:
            self.rot_slider.setValue(int(round(float(st.get("radial_rotation_deg", self.rot_slider.value())))))
        except Exception:
            pass

        try:
            self.mirror_check.setChecked(bool(st.get("radial_mirror", True)))
        except Exception:
            pass

        try:
            if self.smooth_amt is not None:
                self.smooth_amt.setValue(int(st.get("radial_smooth_amount", self.smooth_amt.value())))
        except Exception:
            pass

        try:
            self.center_motion_slider.setValue(int(st.get("center_motion", self.center_motion_slider.value())))
            self.center_zoom_slider.setValue(int(st.get("center_image_zoom", self.center_zoom_slider.value())))
            self.edge_waviness_slider.setValue(int(st.get("edge_waviness", self.edge_waviness_slider.value())))
            self.feather_audio_check.setChecked(bool(st.get("feather_audio_enabled", self.feather_audio_check.isChecked())))
            self.feather_audio_slider.setValue(int(st.get("feather_audio_amount", self.feather_audio_slider.value())))
        except Exception:
            pass

        try:
            cip = str(st.get("center_image_path", "") or "")
            if cip:
                self.view.set_center_image(cip)
        except Exception:
            pass

        try:
            if hasattr(self, "radial_smoothness_slider"):
                self.radial_smoothness_slider.setValue(int(st.get("radial_waveform_smoothness", self.radial_smoothness_slider.value())))
            if hasattr(self, "radial_temporal_slider"):
                self.radial_temporal_slider.setValue(int(st.get("radial_temporal_smoothing", self.radial_temporal_slider.value())))
        except Exception:
            pass

        try:
            bg = BackgroundConfig(
                path=str(st.get("bg_path", "") or "") or None,
                scale_mode=str(st.get("bg_scale_mode", "fill") or "fill"),
                offset_x=int(st.get("bg_offset_x", 0)),
                offset_y=int(st.get("bg_offset_y", 0)),
                dim_percent=int(st.get("bg_dim_percent", 0)),
            )
            self.bg_panel.set_config(bg)
            self.view.set_background_config(bg)
        except Exception:
            pass

        try:
            a = str(st.get("grad_a", "") or "")
            b = str(st.get("grad_b", "") or "")
            if a or b:
                self.view.set_gradient_colors(a or None, b or None)
            self.view.set_gradient_curve(str(st.get("grad_curve", "linear") or "linear"))
            self.view.set_gradient_clamp(float(st.get("grad_min", 0.0)), float(st.get("grad_max", 1.0)))
            self.view.set_gradient_smoothing(float(st.get("grad_smoothing", 0.2)))
        except Exception:
            pass

        try:
            self.shadow_panel.chk.setChecked(bool(st.get("shadow_enabled", False)))
            self.shadow_panel.opacity.setValue(int(st.get("shadow_opacity", 50)))
            self.shadow_panel.blur.setValue(int(st.get("shadow_blur_radius", 16)))
            self.shadow_panel.distance.setValue(int(st.get("shadow_distance", 8)))
            self.shadow_panel.angle.setValue(int(st.get("shadow_angle_deg", 45)))
            self.shadow_panel.spread.setValue(int(st.get("shadow_spread", 6)))
        except Exception:
            pass

        try:
            self.glow_panel.chk.setChecked(bool(st.get("glow_enabled", False)))
            self.glow_panel.radius.setValue(int(st.get("glow_radius", 22)))
            self.glow_panel.strength.setValue(int(st.get("glow_strength", 80)))
            gc = str(st.get("glow_color", "") or "")
            if gc:
                self.view.set_glow_color(gc)
        except Exception:
            pass

        try:
            self.radial_panel.chk.setChecked(bool(st.get("fill_enabled", False)))
            self.radial_panel.thr.setValue(int(round(float(st.get("fill_threshold", 0.1)) * 100.0)))
            self.radial_panel.blend.setCurrentText(str(st.get("fill_blend", "normal")))
            fc = str(st.get("fill_color", "") or "")
            if fc:
                self.view.set_radial_fill_color(fc)
        except Exception:
            pass

    def _on_export_progress(self, p):
        try:
            if hasattr(self, "export_progress"):
                self.export_progress.setVisible(True)
                self.export_progress.setValue(int(p))
            self.statusBar().showMessage(f"Exporting... {int(p)}%")
        except Exception:
            pass

    def _on_export_done(self):
        try:
            if hasattr(self, "export_progress"):
                self.export_progress.setVisible(False)
            self.statusBar().showMessage("Export complete")
        except Exception:
            pass
        self.export_btn.setEnabled(True)

    def _on_export_failed(self, msg):
        try:
            if hasattr(self, "export_progress"):
                self.export_progress.setVisible(False)
            self.statusBar().showMessage(f"Export failed: {msg}")
        except Exception:
            pass
        self.export_btn.setEnabled(True)

    def _export(self):
        if not self.engine.current_audio_path:
            return

        if getattr(self, "_export_proc", None) is not None:
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save video", "", "MP4 Video (*.mp4)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path = path + ".mp4"

        w = int(self.exp_w.value())
        h = int(self.exp_h.value())
        fps = int(self.exp_fps.value())
        mode = str(self.mode_combo.currentText())
        color = tuple(self.view.color)
        sens = float(self.view.waveform_sensitivity)

        gpu_device = ""
        if bool(self.exp_gpu.isChecked()) and self.exp_gpu.isEnabled():
            gpu_device = str(self.exp_gpu_device.currentData() or "auto")

        view_state = {
            "background_path": self.view._bg_path,
            "background_scale_mode": self.view._bg_scale_mode,
            "background_offset_x": self.view._bg_off[0],
            "background_offset_y": self.view._bg_off[1],
            "background_dim_percent": self.view._bg_dim,

            "radial_rotation_deg": self.view.radial_rotation_deg,
            "radial_mirror": self.view.radial_mirror,

            "feather_enabled": self.view.feather_enabled,
            "center_motion": self.view.center_motion,
            "center_image_zoom": self.view.center_image_zoom,
            "center_image_path": (
                os.path.abspath(os.path.expanduser(self.view.center_image_path))
                if self.view.center_image_path else None
            ),
            "edge_waviness": self.view.edge_waviness,

            "feather_audio_enabled": bool(self.view.feather_audio_enabled),
            "feather_audio_amount": int(self.view.feather_audio_amount),

            "radial_smooth_amount": int(self.smooth_amt.value() if self.smooth_amt is not None else 50),

            "shadow_enabled": bool(self.view._shadow_enabled),
            "shadow_opacity": int(float(self.view._shadow_opacity) * 100),
            "shadow_blur_radius": int(self.view._shadow_blur),
            "shadow_distance": int(self.view._shadow_distance),
            "shadow_angle_deg": int(self.view._shadow_angle_deg),
            "shadow_spread": int(self.view._shadow_spread),

            "glow_enabled": bool(self.view._glow_enabled),
            "glow_color": self.view._glow_color.name(QColor.HexArgb),
            "glow_radius": int(self.view._glow_radius),
            "glow_strength": int(float(self.view._glow_strength) * 100),

            "radial_fill_enabled": bool(self.view._fill_enabled),
            "radial_fill_color": self.view._fill_color.name(QColor.HexArgb),
            "radial_fill_blend": str(self.view._fill_blend),
            "radial_fill_threshold": float(self.view._fill_threshold),
        }

        project_root = str(Path(__file__).resolve().parents[1])
        cfg = {
            "project_root": project_root,
            "audio_path": self.engine.current_audio_path,
            "out_path": path,
            "width": w,
            "height": h,
            "fps": fps,
            "mode": mode,
            "color": list(color),
            "sensitivity": sens,
            "view_state": view_state,
            "gpu_device": gpu_device,
        }

        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8")
        tmp.write(json.dumps(cfg))
        tmp.flush()
        tmp.close()
        self._export_cfg_path = tmp.name
        self._export_stdout_buf = ""
        self._export_stderr = ""
        self._export_worker_error = None
        self._export_worker_done = False

        self.export_btn.setEnabled(False)
        try:
            self.export_progress.setVisible(True)
            self.export_progress.setValue(0)
        except Exception:
            pass

        proc = QProcess(self)
        self._export_proc = proc
        proc.setWorkingDirectory(project_root)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("QT_QPA_PLATFORM", "offscreen")
        proc.setProcessEnvironment(env)

        proc.setProgram(sys.executable)
        proc.setArguments([str(Path(project_root) / "export" / "worker.py"), self._export_cfg_path])
        proc.readyReadStandardOutput.connect(self._on_export_proc_stdout)
        proc.readyReadStandardError.connect(self._on_export_proc_stderr)
        proc.finished.connect(self._on_export_proc_finished)
        proc.start()

    def _on_export_proc_stdout(self) -> None:
        proc = getattr(self, "_export_proc", None)
        if proc is None:
            return
        try:
            chunk = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        except Exception:
            return

        buf = str(getattr(self, "_export_stdout_buf", "")) + chunk
        lines = buf.split("\n")
        self._export_stdout_buf = lines[-1]
        for line in lines[:-1]:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue

            t = str(msg.get("type", ""))
            if t == "progress":
                try:
                    self._on_export_progress(int(msg.get("pct", 0)))
                except Exception:
                    pass
            elif t == "done":
                self._export_worker_done = True
            elif t == "error":
                self._export_worker_error = str(msg.get("message", "Export failed"))

    def _on_export_proc_stderr(self) -> None:
        proc = getattr(self, "_export_proc", None)
        if proc is None:
            return
        try:
            chunk = bytes(proc.readAllStandardError()).decode("utf-8", errors="ignore")
        except Exception:
            return
        prev = str(getattr(self, "_export_stderr", ""))
        self._export_stderr = (prev + chunk)[-20000:]

    def _on_export_proc_finished(self, exit_code: int, exit_status) -> None:
        err = str(getattr(self, "_export_worker_error", "") or "")
        done = bool(getattr(self, "_export_worker_done", False))

        try:
            cfg = getattr(self, "_export_cfg_path", None)
            if cfg and os.path.exists(cfg):
                os.unlink(cfg)
        except Exception:
            pass
        self._export_cfg_path = None

        try:
            if getattr(self, "_export_proc", None) is not None:
                self._export_proc.deleteLater()
        except Exception:
            pass
        self._export_proc = None

        self._export_worker_error = None
        self._export_worker_done = False

        if err:
            self._on_export_failed(err)
            return

        if exit_code == 0 and done:
            self._on_export_done()
        elif exit_code == 0:
            self._on_export_done()
        else:
            stderr = str(getattr(self, "_export_stderr", "") or "")
            self._on_export_failed(stderr.strip() or f"Export failed (exit {exit_code})")

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1a1a24;
                color: #d0d0d8;
                font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #2a2a3a;
                border-radius: 4px;
                margin-top: 10px;
                padding: 8px 4px 4px 4px;
                font-weight: bold;
                color: #8899bb;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #2a2a3a;
                border: 1px solid #3a3a4a;
                border-radius: 3px;
                padding: 4px 10px;
                color: #d0d0d8;
            }
            QPushButton:hover {
                background-color: #3a3a4a;
            }
            QPushButton:pressed {
                background-color: #4a4a5a;
            }
            QPushButton:checked {
                background-color: #2a4a6a;
                border-color: #4a7aaa;
            }
            QPushButton:disabled {
                color: #555568;
                background-color: #1e1e28;
            }
            QComboBox {
                background-color: #222232;
                border: 1px solid #3a3a4a;
                border-radius: 3px;
                padding: 3px 6px;
                color: #d0d0d8;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox QAbstractItemView {
                background-color: #222232;
                color: #d0d0d8;
                selection-background-color: #3a5a7a;
            }
            QSpinBox, QDoubleSpinBox {
                background-color: #222232;
                border: 1px solid #3a3a4a;
                border-radius: 3px;
                padding: 2px 4px;
                color: #d0d0d8;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #2a2a3a;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #6688aa;
                width: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QSlider::handle:horizontal:hover {
                background: #88aacc;
            }
            QSlider::sub-page:horizontal {
                background: #3a5a7a;
                border-radius: 2px;
            }
            QCheckBox {
                spacing: 5px;
                color: #d0d0d8;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #3a3a4a;
                border-radius: 2px;
                background-color: #222232;
            }
            QCheckBox::indicator:checked {
                background-color: #3a6a9a;
                border-color: #4a8aba;
            }
            QTabWidget::pane {
                border: 1px solid #2a2a3a;
                border-radius: 3px;
                background-color: #1e1e28;
            }
            QTabBar::tab {
                background: #222232;
                border: 1px solid #2a2a3a;
                padding: 3px 6px;
                color: #8899aa;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
                margin-right: 1px;
            }
            QTabBar::tab:selected {
                background: #1e1e28;
                color: #d0d0d8;
                border-bottom-color: #1e1e28;
            }
            QProgressBar {
                border: 1px solid #3a3a4a;
                border-radius: 3px;
                text-align: center;
                color: #d0d0d8;
                background-color: #222232;
            }
            QProgressBar::chunk {
                background-color: #3a6a9a;
                border-radius: 2px;
            }
            QScrollArea {
                background-color: #1a1a24;
                border-left: 1px solid #2a2a3a;
            }
            QLabel {
                color: #9999aa;
            }
            QMenuBar {
                background-color: #1a1a24;
                color: #d0d0d8;
                border-bottom: 1px solid #2a2a3a;
            }
            QMenuBar::item:selected {
                background-color: #2a2a3a;
            }
            QMenu {
                background-color: #222232;
                color: #d0d0d8;
                border: 1px solid #3a3a4a;
            }
            QMenu::item:selected {
                background-color: #3a5a7a;
            }
        """)
