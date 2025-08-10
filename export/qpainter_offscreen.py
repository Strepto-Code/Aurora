import numpy as np
import soundfile as sf
from PySide6.QtGui import QImage, QColor
from audio.analysis import Analyzer
from widgets.rt_widget import RTVisualizerWidget
from config.settings import BackgroundConfig

import subprocess, shutil, struct

class _FFmpegDecodedAudio:
    """Minimal SoundFile-like wrapper backed by ffmpeg-decoded float32 mono PCM in memory.
    Provides __len__, samplerate, seek(pos), read(n, dtype='float32', always_2d=False).
    """
    def __init__(self, path, target_sr=None):
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found for decoding non-WAV formats.")
        # Probe sample rate if not provided
        self.samplerate = int(target_sr or 48000)
        # Decode to f32le mono at samplerate
        cmd = [
            shutil.which("ffmpeg"), "-v", "error", "-i", path,
            "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(self.samplerate), "-"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw = proc.stdout.read()
        proc.wait()
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "ignore")
            raise RuntimeError(f"ffmpeg decode failed: {err}")
        import numpy as _np
        self._data = _np.frombuffer(raw, dtype=_np.float32)
        self._pos = 0
    def __len__(self):
        return int(self._data.shape[0])
    def seek(self, frame):
        self._pos = int(max(0, min(len(self._data), frame)))
    def read(self, n, dtype='float32', always_2d=False):
        import numpy as _np
        n = int(max(0, n))
        end = min(len(self._data), self._pos + n)
        y = self._data[self._pos:end].astype(_np.float32, copy=False)
        self._pos = end
        if always_2d:
            y = y.reshape(-1, 1)
        return y

class _Feeder:
    def __init__(self):
        self._samples = np.zeros(1024, dtype=np.float32)
        self._spectrum = np.zeros(1024, dtype=np.float32)
        self._flux = 0.0
    def get_frame(self):
        return self._samples, self._spectrum, self._flux

class QPainterOffscreenRenderer:
    def __init__(self, width, height, mode, color, sensitivity, fps, audio_path, **view_state):
        self.width = int(width)
        self.height = int(height)
        self.mode = mode
        self.color = color
        self.sensitivity = float(sensitivity or 1.0)
        self.fps = int(fps)
        self.audio_path = audio_path
        # Load audio first (need sample rate for analyzer)
        self._sf = None
        try:
            self._sf = sf.SoundFile(self.audio_path, 'r')
        except Exception:
            # Fallback for MP3/FLAC/OGG via ffmpeg
            self._sf = _FFmpegDecodedAudio(self.audio_path)
        self.duration = float(len(self._sf) / self._sf.samplerate)
        self._an = Analyzer(sample_rate=int(self._sf.samplerate), fft_size=2048)

        # Build a headless RT widget
        self._feeder = _Feeder()
        self._view = RTVisualizerWidget(audio_engine=self._feeder, start_timer=False)
        self._view._offscreen_paint = True  # ensure QImage path is used
        self._view._offscreen_size = (self.width, self.height)
        try:
            self._view.set_mode(mode)
        except Exception:
            pass
        try:
            self._view.set_color(self.color)
        except Exception:
            pass
        try:
            self._view.set_sensitivity(self.sensitivity)
        except Exception:
            pass

        # Apply view config (background, radial, feather, shadow, radial fill)
        try:
            bcfg = BackgroundConfig(
                path = view_state.get('background_path'),
                scale_mode = view_state.get('background_scale_mode', 'fill'),
                offset_x = int(view_state.get('background_offset_x', 0) or 0),
                offset_y = int(view_state.get('background_offset_y', 0) or 0),
                dim_percent = int(view_state.get('background_dim_percent', 0) or 0),
            )
            self._view.set_background_config(bcfg)
        except Exception:
            pass
        try:
            self._view.set_radial_rotation_deg(view_state.get('radial_rotation_deg', 0.0))
            self._view.set_radial_mirror(bool(view_state.get('radial_mirror', False)))
        except Exception:
            pass
        try:
            self._view.set_feather_enabled(bool(view_state.get('feather_enabled', False)))
            self._view.set_edge_waviness(int(view_state.get('edge_waviness', 0)))
            self._view.set_feather_audio_enabled(bool(view_state.get('feather_audio_enabled', False)))
            self._view.set_feather_audio_amount(int(view_state.get('feather_audio_amount', 0)))
            cm = int(view_state.get('center_motion', 0))
            self._view.set_center_motion(cm)
        except Exception:
            pass
        try:
            img_path = view_state.get('center_image_path') or None
            if img_path:
                self._view.set_center_image(img_path)
        except Exception:
            pass
        try:
            # Radial smoothing sliders from UI
            if view_state.get('radial_smooth', False):
                self._view.set_radial_smooth(True)
                self._view.set_radial_smooth_amount(int(view_state.get('radial_smooth_amount', 50)))
        except Exception:
            pass
        try:
            # Radial fill block
            self._view.set_radial_fill_enabled(bool(view_state.get('radial_fill_enabled', False)))
            col = view_state.get('radial_fill_color', '#80FFFFFF')
            try:
                if hasattr(col, 'name'):
                    # QColor -> hex with alpha
                    try:
                        self._view.set_radial_fill_color(col.name(QColor.HexArgb))
                    except Exception:
                        self._view.set_radial_fill_color(col.name())
                elif isinstance(col, str):
                    self._view.set_radial_fill_color(col)
            except Exception:
                # Fallback to default if anything goes wrong
                self._view.set_radial_fill_color('#80FFFFFF')
            self._view.set_radial_fill_blend(view_state.get('radial_fill_blend', 'normal'))
            self._view.set_radial_fill_threshold(float(view_state.get('radial_fill_threshold', 0.1)))
        except Exception:
            pass

        try:
            # New smoothing controls
            if 'radial_waveform_smoothness' in view_state:
                self._view.set_radial_waveform_smoothness(int(view_state.get('radial_waveform_smoothness', 0)))
            if 'radial_temporal_smoothing' in view_state:
                self._view.set_radial_temporal_smoothing(int(view_state.get('radial_temporal_smoothing', 0)))
        except Exception:
            pass
        try:
            # Shadow controls
            if 'shadow_enabled' in view_state:
                self._view.set_shadow_enabled(bool(view_state.get('shadow_enabled', False)))
            if 'shadow_opacity' in view_state:
                self._view.set_shadow_opacity(int(view_state.get('shadow_opacity', 60)))
            if 'shadow_blur_radius' in view_state:
                self._view.set_shadow_blur_radius(int(view_state.get('shadow_blur_radius', 16)))
            if 'shadow_distance' in view_state:
                self._view.set_shadow_distance(int(view_state.get('shadow_distance', 8)))
            if 'shadow_angle_deg' in view_state:
                self._view.set_shadow_angle_deg(int(view_state.get('shadow_angle_deg', 45)))
            if 'shadow_spread' in view_state:
                self._view.set_shadow_spread(int(view_state.get('shadow_spread', 6)))
        except Exception:
            pass

    def _read_frame(self, t):
        # Pull a window around time t and compute spectrum/flux
        sr = self._sf.samplerate
        hop = max(1, int(sr / self.fps))
        pos = int(max(0, min(len(self._sf)-1, t * sr)))
        self._sf.seek(pos)
        y = self._sf.read(hop, dtype='float32', always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
        samples = np.zeros(1024, dtype=np.float32)
        n = min(len(y), 1024)
        samples[:n] = y[:n]
        spec, flux = self._an.compute(samples)
        self._feeder._samples[:] = samples
        import numpy as _np
        if getattr(self._feeder, '_spectrum', None) is None or self._feeder._spectrum.shape[0] != spec.shape[0]:
            self._feeder._spectrum = _np.zeros_like(spec)
        self._feeder._spectrum[:] = spec
        self._feeder._flux = float(flux)
        return samples, spec

    def render_frame(self, t):
        samples, spectrum = self._read_frame(t)
        img = self._view.render_frame_to_qimage(self.width, self.height, samples, spectrum)
        # Convert QImage to numpy HxWx3 (uint8)
        w = img.width(); h = img.height()
        ptr = img.bits()
        try:
            buf = memoryview(ptr)
        except TypeError:
            byte_count = img.sizeInBytes() if hasattr(img, 'sizeInBytes') else img.byteCount()
            if hasattr(ptr, 'setsize'):
                ptr.setsize(byte_count)
            buf = ptr.asstring(byte_count)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, img.bytesPerLine()))[:, : w*3].reshape((h, w, 3))
        return arr.copy()

    def render_time(self, t):
        return self.render_frame(t)
