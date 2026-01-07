import os
import sys
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QProcess, QProcessEnvironment
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QFileDialog, QComboBox, QPushButton, QSlider,
    QLabel, QHBoxLayout, QVBoxLayout, QSpinBox, QProgressBar, QCheckBox,
    QDockWidget, QTabWidget
)

from audio.input import AudioEngine
from export.exporter import Exporter, list_gpu_export_devices
from widgets.rt_widget import RTVisualizerWidget

from ui.components import (
    BackgroundPanel,
    GradientPanel,
    HotkeysDialog,
    PresetsDialog,
    RadialFillPanel,
    ShadowPanel,
    GlowPanel,
)

from PySide6.QtGui import QShortcut, QKeySequence, QColor

from config.settings import (
    PresetStore, DEFAULT_STATE, AppState,
    BackgroundConfig, ShadowConfig, RadialFillConfig, 
    load_audio_state, save_audio_state
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
        # show/hide radial-only controls
        try:
            self._radial_group.setVisible("Radial" in mode or "Circular" in mode)
        except Exception:
            pass
        # Radial/Circular smoothing is always enabled (no UI toggle).
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

        # Engine + view
        self.engine = AudioEngine(sample_rate=48000, block_size=1024)
        self.view = RTVisualizerWidget(self.engine)

        # Radial waveform smoothness controls (created inside the Radial group to avoid stray top-level windows)
        self.smooth_check = None
        self.smooth_amt = None

        # App state/presets/hotkeys
        self._preset_store = PresetStore()
        self._app_state = DEFAULT_STATE
        self._hotkeys = DEFAULT_STATE.hotkeys

        # Timers
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        # Transport (play/pause/scrub)
        self._user_scrubbing = False
        self._transport_timer = QTimer(self)
        self._transport_timer.timeout.connect(self._tick_transport)
        self._transport_timer.start(100)

        # Layout
        root = QWidget()
        self.setCentralWidget(root)

        # Side controls
        self.open_btn = QPushButton("Open Audio")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODES)

        self.rot_slider = QSlider(Qt.Horizontal)
        self.rot_slider.setRange(0, 360)
        self.rot_slider.setValue(0)

        self.mirror_check = QPushButton("Radial Mirror")
        self.mirror_check.setCheckable(True)
        self.mirror_check.setChecked(True)

        self.color_btn = QPushButton("Color")

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

        self.exp_gpu = QCheckBox("GPU Export")
        self.exp_gpu.setChecked(False)
        self.exp_gpu_device = QComboBox()
        self.exp_gpu_device.setEnabled(False)

        self.export_btn = QPushButton("Export MP4")

        # Center image / radial controls
        self.center_btn = QPushButton("Center Image...")
        self.feather_check = QPushButton("Feather")
        self.feather_check.setCheckable(True)

        self.center_motion_slider = QSlider(Qt.Horizontal)
        self.center_motion_slider.setRange(0, 100)
        self.center_motion_slider.setValue(0)

        self.center_zoom_slider = QSlider(Qt.Horizontal)
        self.center_zoom_slider.setRange(50, 250)
        self.center_zoom_slider.setValue(100)

        self.edge_waviness_slider = QSlider(Qt.Horizontal)
        self.edge_waviness_slider.setRange(0, 100)
        self.edge_waviness_slider.setValue(30)

        self.feather_audio_check = QPushButton("Feather Audio")
        self.feather_audio_check.setCheckable(True)
        self.feather_audio_check.setChecked(False)

        self.feather_audio_slider = QSlider(Qt.Horizontal)
        self.feather_audio_slider.setRange(0, 100)
        self.feather_audio_slider.setValue(40)

        # Menu + docks
        self._build_menu()

        # FX dock (right)
        self.fxDock = QDockWidget("Visual FX", self)
        # Keep FX dock anchored to the main window (no floating/closing/detaching)
        self.fxDock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.fxDock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        fx_container = QWidget(self.fxDock)
        fx_layout = QVBoxLayout(fx_container)

        top_pad = 10 if sys.platform == "darwin" else 0
        fx_layout.setContentsMargins(0, top_pad, 0, 0)
        fx_layout.setSpacing(0)

        self.fxTabs = QTabWidget(fx_container)
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

        self.fxTabs.addTab(self.bg_panel, "Background")
        self.fxTabs.addTab(self.grad_panel, "Gradient")
        self.fxTabs.addTab(self.shadow_panel, "Shadow")
        self.fxTabs.addTab(self.glow_panel, "Glow")
        self.fxTabs.addTab(self.radial_panel, "Radial Fill")

        fx_layout.addWidget(self.fxTabs)
        fx_container.setLayout(fx_layout)
        self.fxDock.setWidget(fx_container)
        self.addDockWidget(Qt.RightDockWidgetArea, self.fxDock)

        self._apply_hotkeys()

        # Right-side controls layout
        right = QVBoxLayout()
        right.setContentsMargins(8, 8, 8, 8)

        right.addWidget(self.open_btn)
        right.addWidget(QLabel("Output Device"))
        self.output_combo = QComboBox()
        right.addWidget(self.output_combo)

        right.addWidget(QLabel("Mode"))
        right.addWidget(self.mode_combo)

        # Radial group (only visible in radial/circular modes)
        self._radial_group = QWidget()
        _rg = QVBoxLayout()
        _rg.setContentsMargins(0, 0, 0, 0)

        _rg.addWidget(QLabel("Radial Rotation"))
        _rg.addWidget(self.rot_slider)
        _rg.addWidget(self.mirror_check)

        # Smooth amount (always-on smoothing, no toggle)
        self.smooth_amt = QSpinBox(self._radial_group)
        self.smooth_amt.setRange(0, 100)
        self.smooth_amt.setValue(50)
        _rg.addWidget(QLabel("Smooth Amount"))
        _rg.addWidget(self.smooth_amt)

        _rg.addWidget(self.center_btn)

        _rg.addWidget(QLabel("Center Motion"))
        _rg.addWidget(self.center_motion_slider)

        _rg.addWidget(QLabel("Center Image Zoom"))
        _rg.addWidget(self.center_zoom_slider)

        _rg.addWidget(QLabel("Edge Waviness"))
        _rg.addWidget(self.edge_waviness_slider)

        _rg.addWidget(self.feather_audio_check)
        _rg.addWidget(QLabel("Feather Audio Amount"))
        _rg.addWidget(self.feather_audio_slider)

        self._radial_group.setLayout(_rg)
        right.addWidget(self._radial_group)

        # Common controls
        right.addWidget(QLabel("Sensitivity"))
        right.addWidget(self.sens_slider)

        right.addWidget(QLabel("Volume"))
        right.addWidget(self.vol_slider)

        right.addWidget(self.color_btn)

        right.addWidget(QLabel("Realtime FPS"))
        right.addWidget(self.fps_spin)

        right.addSpacing(12)
        right.addWidget(QLabel("Export:"))
        row1 = QHBoxLayout()
        row1.addWidget(self.exp_w)
        row1.addWidget(self.exp_h)
        right.addLayout(row1)
        right.addWidget(self.exp_fps)
        right.addWidget(self.exp_gpu)
        right.addWidget(self.exp_gpu_device)
        right.addWidget(self.export_btn)

        right.addStretch(1)
        right_wrap = QWidget()
        right_wrap.setLayout(right)
        right_wrap.setFixedWidth(280)

        # Root layout
        outer = QVBoxLayout()
        main_row = QHBoxLayout()
        main_row.addWidget(self.view, 1)
        main_row.addWidget(right_wrap, 0)
        outer.addLayout(main_row, 1)

        # Bottom transport bar
        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setRange(0, 1000)
        self.btn_to_start = QPushButton("⏮")
        self.btn_play = QPushButton("▶")
        self.btn_pause = QPushButton("⏸")

        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_to_start)
        bottom.addWidget(self.btn_play)
        bottom.addWidget(self.btn_pause)
        bottom.addWidget(self.scrub, 1)
        outer.addLayout(bottom, 0)

        # Export progress bar (hidden until exporting)
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 100)
        self.export_progress.setValue(0)
        self.export_progress.setVisible(False)
        outer.addWidget(self.export_progress)

        root.setLayout(outer)

        # Populate output device list
        self._refresh_output_devices()

        # Hook up signals
        self.open_btn.clicked.connect(self._open_audio)
        self.export_btn.clicked.connect(self._export)
        self.exp_gpu.toggled.connect(self._on_gpu_export_toggled)

        # Transport bar
        self.btn_to_start.clicked.connect(self._jump_to_start)
        self.btn_play.clicked.connect(self._play_only)
        self.btn_pause.clicked.connect(self._pause_only)
        self.scrub.sliderPressed.connect(self._begin_scrub)
        self.scrub.sliderReleased.connect(self._end_scrub)

        # Radial controls
        self.rot_slider.valueChanged.connect(lambda v: self.view.set_radial_rotation_deg(v))
        self.mirror_check.toggled.connect(self.view.set_radial_mirror)
        self.center_motion_slider.valueChanged.connect(self.view.set_center_motion)
        self.center_zoom_slider.valueChanged.connect(self.view.set_center_image_zoom)
        self.edge_waviness_slider.valueChanged.connect(self.view.set_edge_waviness)
        self.feather_audio_check.toggled.connect(self.view.set_feather_audio_enabled)
        self.feather_audio_slider.valueChanged.connect(self.view.set_feather_audio_amount)
        self.smooth_amt.valueChanged.connect(self.view.set_radial_smooth_amount)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.sens_slider.valueChanged.connect(lambda v: self.view.set_waveform_sensitivity(v / 100.0))
        self.vol_slider.valueChanged.connect(lambda v: self.engine.set_volume(v / 100.0))
        self.fps_spin.valueChanged.connect(self._set_realtime_fps)
        self.output_combo.currentIndexChanged.connect(self._on_output_changed)

        # Center image
        self.center_btn.clicked.connect(self._choose_center_image)

        # Color
        self.color_btn.clicked.connect(self._choose_color)

        # Initial mode + group visibility
        self._on_mode_changed(0)

        # load persisted audio config (if any)
        self._load_audio_state()

        # Optional: add extra docks for smoothing and split sensitivities (if present in view)
        self._try_add_extra_docks()

        # Detect GPU export options (hardware encoders) once at startup.
        self._refresh_gpu_export_options()

        # Load persisted UI state (INI)
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
            self._hotkeys = dlg.hotkeys
            self._apply_hotkeys()
            # persist
            try:
                self._app_state = AppState(
                    background=self._app_state.background,
                    shadow=self._app_state.shadow,
                    radial_fill=self._app_state.radial_fill,
                    hotkeys=self._hotkeys,
                )
            except Exception:
                pass

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

            sc = QShortcut(QKeySequence(self._hotkeys.toggle_safe), self)
            sc.activated.connect(self._toggle_safe_mode)
            self._shortcuts.append(sc)
        except Exception:
            pass

    def _cycle_preset(self, step):
        # minimal: depends on PresetStore ordering; keep no-op if unsupported
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
            from PySide6.QtWidgets import QFileDialog
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

    def _set_realtime_fps(self, fps):
        try:
            self.view.set_fps_cap(int(fps))
        except Exception:
            pass

    def _on_gpu_export_toggled(self, checked: bool) -> None:
        """Enable/disable the GPU device dropdown alongside the checkbox."""
        try:
            self.exp_gpu_device.setEnabled(bool(checked) and self.exp_gpu.isEnabled())
        except Exception:
            pass

    def _refresh_gpu_export_options(self) -> None:
        """Detect hardware encoder options and update the export UI.

        If no supported options are found, the GPU checkbox is disabled and greyed out.
        """
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
                # Dropdown is enabled only if the checkbox is checked.
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
            if getattr(self.engine, "_playing", False):
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
            if not getattr(self.engine, "current_audio_path", None) or self._user_scrubbing:
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
            from PySide6.QtWidgets import QColorDialog
            col = QColorDialog.getColor(
                self.view.color if hasattr(self.view, "color") else QColor("#FFFFFF"),
                self
            )
            if col.isValid():
                self.view.set_color(col)
        except Exception:
            pass

    def _get_state_snapshot(self):
        # Save only what we currently support
        try:
            bg = getattr(self.view, "_bg_cfg", BackgroundConfig())
        except Exception:
            bg = BackgroundConfig()
        try:
            sh = ShadowConfig(
                enabled=bool(getattr(self.view, "shadow_enabled", False)),
                opacity=float(getattr(self.view, "shadow_opacity", 0.0)),
                color=tuple(getattr(self.view, "shadow_color", (0, 0, 0))),
                blur_radius=float(getattr(self.view, "shadow_blur_radius", 0.0)),
                distance=float(getattr(self.view, "shadow_distance", 0.0)),
                angle_deg=float(getattr(self.view, "shadow_angle_deg", 0.0)),
                spread=float(getattr(self.view, "shadow_spread", 0.0)),
            )
        except Exception:
            sh = ShadowConfig()
        try:
            rf = RadialFillConfig(
                enabled=bool(getattr(self.view, "radial_fill_enabled", False)),
                color=tuple(getattr(self.view, "radial_fill_color", (255, 255, 255))),
                blend=float(getattr(self.view, "radial_fill_blend", 0.5)),
                threshold=float(getattr(self.view, "radial_fill_threshold", 0.15)),
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
            self.view.set_shadow_opacity(st.shadow.opacity)
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
            # device
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
                "output_device_index": getattr(self.engine, "output_device_index", None),
                "volume": int(self.vol_slider.value()),
            }
            save_audio_state(st)
        except Exception:
            pass

    def _collect_state_ini(self):
        def _color_hex() -> str:
            c = getattr(self.view, "color", (0.2, 0.8, 1.0))
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
            "sensitivity": float(getattr(self.view, "waveform_sensitivity", self.sens_slider.value() / 100.0)),
            "volume": float(self.vol_slider.value() / 100.0),
            "output_device_index": getattr(self.engine, "output_device_index", None),
            "color": _color_hex(),
            "fx_tab": int(getattr(self.fxTabs, "currentIndex", lambda: 0)()),

            "radial_rotation_deg": float(getattr(self.view, "radial_rotation_deg", float(self.rot_slider.value()))),
            "radial_mirror": bool(getattr(self.view, "radial_mirror", bool(self.mirror_check.isChecked()))),
            "radial_smooth_amount": int(self.smooth_amt.value() if self.smooth_amt is not None else 50),
            "center_motion": int(getattr(self.view, "center_motion", int(self.center_motion_slider.value()))),
            "center_image_zoom": int(getattr(self.view, "center_image_zoom", int(self.center_zoom_slider.value()))),
            "edge_waviness": int(getattr(self.view, "edge_waviness", int(self.edge_waviness_slider.value()))),
            "feather_audio_enabled": bool(getattr(self.view, "feather_audio_enabled", bool(self.feather_audio_check.isChecked()))),
            "feather_audio_amount": int(getattr(self.view, "feather_audio_amount", int(self.feather_audio_slider.value()))),
            "center_image_path": str(getattr(self.view, "center_image_path", "") or ""),
            "radial_waveform_smoothness": int(getattr(self.view, "radial_wave_smoothness", 50)),
            "radial_temporal_smoothing": int(float(getattr(self.view, "radial_temporal_alpha", 0.3)) * 100.0),

            "export_width": int(self.exp_w.value()),
            "export_height": int(self.exp_h.value()),
            "export_fps": int(self.exp_fps.value()),
            "export_gpu": bool(self.exp_gpu.isChecked()),
            "export_gpu_device": str(self.exp_gpu_device.currentData() or ""),

            "bg_path": str(getattr(self.view, "_bg_path", "") or ""),
            "bg_scale_mode": str(getattr(self.view, "_bg_scale_mode", "fill")),
            "bg_offset_x": int(getattr(self.view, "_bg_off", (0, 0))[0]),
            "bg_offset_y": int(getattr(self.view, "_bg_off", (0, 0))[1]),
            "bg_dim_percent": int(getattr(self.view, "_bg_dim", 0)),

            "grad_a": getattr(getattr(self.view, "_grad_a", None), "name", lambda: "")(),
            "grad_b": getattr(getattr(self.view, "_grad_b", None), "name", lambda: "")(),
            "grad_curve": str(getattr(self.view, "_grad_curve", "linear")),
            "grad_min": float(getattr(self.view, "_grad_min", 0.0)),
            "grad_max": float(getattr(self.view, "_grad_max", 1.0)),
            "grad_smoothing": float(getattr(self.view, "_amp_alpha", 0.2)),

            "shadow_enabled": bool(getattr(self.view, "_shadow_enabled", False)),
            "shadow_opacity": int(float(getattr(self.view, "_shadow_opacity", 0.0)) * 100.0),
            "shadow_blur_radius": int(getattr(self.view, "_shadow_blur", 16)),
            "shadow_distance": int(getattr(self.view, "_shadow_distance", 8)),
            "shadow_angle_deg": int(getattr(self.view, "_shadow_angle_deg", 45)),
            "shadow_spread": int(getattr(self.view, "_shadow_spread", 6)),

            "glow_enabled": bool(getattr(self.view, "_glow_enabled", False)),
            "glow_color": getattr(self.view, "_glow_color", QColor(80, 220, 255, 255)).name(QColor.HexArgb),
            "glow_radius": int(getattr(self.view, "_glow_radius", 22)),
            "glow_strength": int(float(getattr(self.view, "_glow_strength", 0.8)) * 100.0),

            "fill_enabled": bool(getattr(self.view, "_fill_enabled", False)),
            "fill_color": getattr(self.view, "_fill_color", QColor(255, 255, 255, 48)).name(QColor.HexArgb),
            "fill_blend": str(getattr(self.view, "_fill_blend", "normal")),
            "fill_threshold": float(getattr(self.view, "_fill_threshold", 0.1)),
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
            # Restore GPU device selection if still present.
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
            # Radial fill panel exposes sliders/combo; checkbox is stored as `chk`.
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
            # Don't start two exports at once.
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
        sens = float(getattr(self.view, "waveform_sensitivity", 1.0))

        # GPU export = hardware video encoder (when available)
        gpu_device = ""
        if bool(self.exp_gpu.isChecked()) and self.exp_gpu.isEnabled():
            gpu_device = str(self.exp_gpu_device.currentData() or "auto")

        view_state = {
            "background_path": getattr(self.view, "_bg_path", None),
            "background_scale_mode": getattr(self.view, "_bg_scale_mode", "fill"),
            "background_offset_x": getattr(self.view, "_bg_off", (0, 0))[0],
            "background_offset_y": getattr(self.view, "_bg_off", (0, 0))[1],
            "background_dim_percent": getattr(self.view, "_bg_dim", 0),

            "radial_rotation_deg": getattr(self.view, "radial_rotation_deg", 0.0),
            "radial_mirror": getattr(self.view, "radial_mirror", True),

            "feather_enabled": getattr(self.view, "feather_enabled", False),
            "center_motion": getattr(self.view, "center_motion", 0),
            "center_image_zoom": getattr(self.view, "center_image_zoom", 100),
            "center_image_path": (
                os.path.abspath(os.path.expanduser(getattr(self.view, "center_image_path", "")))
                if getattr(self.view, "center_image_path", None) else None
            ),
            "edge_waviness": getattr(self.view, "edge_waviness", getattr(self.view, "feather_noise", 0)),

            "feather_audio_enabled": bool(getattr(self.view, "feather_audio_enabled", False)),
            "feather_audio_amount": int(getattr(self.view, "feather_audio_amount", 40)),

            # Always-on radial/circular smoothing
            "radial_smooth_amount": int(self.smooth_amt.value() if self.smooth_amt is not None else 50),

            # Shadow
            "shadow_enabled": bool(getattr(self.view, "_shadow_enabled", False)),
            "shadow_opacity": int(float(getattr(self.view, "_shadow_opacity", 0.0)) * 100),
            "shadow_blur_radius": int(getattr(self.view, "_shadow_blur", 16)),
            "shadow_distance": int(getattr(self.view, "_shadow_distance", 8)),
            "shadow_angle_deg": int(getattr(self.view, "_shadow_angle_deg", 45)),
            "shadow_spread": int(getattr(self.view, "_shadow_spread", 6)),

            # Glow
            "glow_enabled": bool(getattr(self.view, "_glow_enabled", False)),
            "glow_color": getattr(self.view, "_glow_color", QColor(80, 220, 255, 255)).name(QColor.HexArgb),
            "glow_radius": int(getattr(self.view, "_glow_radius", 22)),
            "glow_strength": int(float(getattr(self.view, "_glow_strength", 0.8)) * 100),

            # Radial fill
            "radial_fill_enabled": bool(getattr(self.view, "_fill_enabled", False)),
            "radial_fill_color": getattr(self.view, "_fill_color", QColor(255, 255, 255, 48)).name(QColor.HexArgb),
            "radial_fill_blend": str(getattr(self.view, "_fill_blend", "normal")),
            "radial_fill_threshold": float(getattr(self.view, "_fill_threshold", 0.1)),
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
        # Qt passes `QProcess.ExitStatus` as second arg. We don't need it.
        err = str(getattr(self, "_export_worker_error", "") or "")
        done = bool(getattr(self, "_export_worker_done", False))

        # Cleanup temp config
        try:
            cfg = getattr(self, "_export_cfg_path", None)
            if cfg and os.path.exists(cfg):
                os.unlink(cfg)
        except Exception:
            pass
        self._export_cfg_path = None

        # Reset proc
        try:
            if getattr(self, "_export_proc", None) is not None:
                self._export_proc.deleteLater()
        except Exception:
            pass
        self._export_proc = None

        # Reset flags
        self._export_worker_error = None
        self._export_worker_done = False

        if err:
            self._on_export_failed(err)
            return

        if exit_code == 0 and done:
            self._on_export_done()
        elif exit_code == 0:
            # Some ffmpeg versions exit 0 without emitting JSON.
            self._on_export_done()
        else:
            stderr = str(getattr(self, "_export_stderr", "") or "")
            self._on_export_failed(stderr.strip() or f"Export failed (exit {exit_code})")

    def _try_add_extra_docks(self):
        # Optional extra docks for smoother spectrum and sensitivity split
        try:
            from PySide6.QtWidgets import QDockWidget, QFormLayout, QWidget, QSlider
            dock = QDockWidget("Radial Smoothing", self)
            dock.setAllowedAreas(Qt.RightDockWidgetArea)
            dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
            body = QWidget(dock)
            form = QFormLayout(body)

            self.radial_smoothness_slider = QSlider(Qt.Horizontal)
            self.radial_smoothness_slider.setRange(0, 100)
            self.radial_smoothness_slider.setValue(50)

            self.radial_temporal_slider = QSlider(Qt.Horizontal)
            self.radial_temporal_slider.setRange(0, 95)
            self.radial_temporal_slider.setValue(30)

            self.radial_smoothness_slider.valueChanged.connect(
                lambda v: hasattr(self.view, "set_radial_waveform_smoothness") and self.view.set_radial_waveform_smoothness(v)
            )
            self.radial_temporal_slider.valueChanged.connect(
                lambda v: hasattr(self.view, "set_radial_temporal_smoothing") and self.view.set_radial_temporal_smoothing(v)
            )

            form.addRow("Spatial", self.radial_smoothness_slider)
            form.addRow("Temporal", self.radial_temporal_slider)

            body.setLayout(form)
            dock.setWidget(body)
            self.addDockWidget(Qt.RightDockWidgetArea, dock)
        except Exception:
            pass
