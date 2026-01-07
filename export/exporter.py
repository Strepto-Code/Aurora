from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import moderngl
import numpy as np
import soundfile as sf
from PySide6.QtGui import QColor

from audio.analysis import Analyzer
from config.settings import BackgroundConfig
from visuals.renderers import ParticleRenderer, SpectrumRenderer, WaveformRenderer
from widgets.rt_widget import RTVisualizerWidget


class _Feeder:
    """Minimal audio-engine shim for the QPainter-based export path.

    RTVisualizerWidget expects an object with a `get_frame()` method returning
    (samples, spectrum, flux). During export we compute these elsewhere and
    simply hand them to the widget.
    """

    def __init__(self, block_size: int = 1024):
        self.block_size = int(max(1, block_size))
        self._samples = np.zeros(self.block_size, dtype=np.float32)
        self._spectrum: np.ndarray | None = None
        self._flux: float = 0.0

    def get_frame(self):
        samples = self._samples
        if samples is None or samples.shape[0] != self.block_size:
            samples = np.zeros(self.block_size, dtype=np.float32)
            self._samples = samples

        if self._spectrum is None:
            # Default to a spectrum matching Analyzer(fft_size=2048)
            self._spectrum = np.zeros(2048 // 2 + 1, dtype=np.float32)

        return samples, self._spectrum, float(self._flux)

class _FFmpegDecodedAudio:
    def __init__(self, path, target_sr=None):
        if shutil.which('ffmpeg') is None:
            raise RuntimeError('ffmpeg not found for decoding non-WAV formats.')
        self.samplerate = int(target_sr or 48000)
        cmd = [shutil.which('ffmpeg'), '-v','error','-i', path, '-f','f32le','-acodec','pcm_f32le','-ac','1','-ar', str(self.samplerate), '-']
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw = proc.stdout.read()
        proc.wait()
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else b'').decode('utf-8','ignore')
            raise RuntimeError(f'ffmpeg decode failed: {err}')
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

class OffscreenRenderer:
    def __init__(self, width, height, mode, color, sensitivity, fps, audio_path):
        self.width = width
        self.height = height
        self.mode = mode
        self.color = color
        self.sensitivity = sensitivity
        self.fps = fps
        self.ctx = moderngl.create_standalone_context()
        # FBO and PBOs
        self.fbo = self.ctx.simple_framebuffer((self.width, self.height))
        self.fbo.use()
        # Two PBOs for async readback (if backend supports)
        self._pbo_a = self.ctx.buffer(reserve=self.width * self.height * 3, dynamic=True)
        self._pbo_b = self.ctx.buffer(reserve=self.width * self.height * 3, dynamic=True)
        self._use_a = True

        # audio decode
        try:
            self.sf = sf.SoundFile(audio_path, 'r')
        except Exception:
            self.sf = _FFmpegDecodedAudio(audio_path)
        self.sample_rate = int(getattr(self.sf, 'samplerate', 48000))
        self.analyzer = Analyzer(sample_rate=self.sample_rate, fft_size=2048)
        self.duration = len(self.sf) / float(self.sample_rate)

        self._renderers = {
            "Waveform - Linear": WaveformRenderer(self.ctx),
            "Waveform - Circular": WaveformRenderer(self.ctx, circular=True),
            "Spectrum - Bars": SpectrumRenderer(self.ctx),
            "Spectrum - Radial": SpectrumRenderer(self.ctx, radial=True),
            "Particles": ParticleRenderer(self.ctx),
        }

    def _get_audio_chunk(self, t, seconds=1/60.0):
        start = int(t * self.sample_rate)
        n = int(seconds * self.sample_rate)
        self.sf.seek(min(start, len(self.sf)))
        data = self.sf.read(n, dtype='float32', always_2d=True)
        if data.shape[0] < n:
            pad = np.zeros((n, data.shape[1] if data.ndim==2 and data.shape[1]>0 else 1), dtype=np.float32)
            pad[:data.shape[0], :data.shape[1]] = data
            data = pad
        mono = data.mean(axis=1).astype(np.float32, copy=False)
        return mono

    def render_time(self, t):
        chunk = self._get_audio_chunk(t, seconds=1.0/self.fps)
        spectrum, flux = self.analyzer.compute(chunk)
        energy = float(np.clip(np.mean(np.abs(chunk)) * self.sensitivity, 0.0, 1.0))

        self.ctx.viewport = (0, 0, self.width, self.height)
        self.fbo.clear(0.05, 0.05, 0.07, 1.0)

        renderer = self._renderers[self.mode]
        renderer.render(self.width, self.height, chunk, spectrum, energy, flux, self.color, self.sensitivity)

        # Async readback using double PBO
        if self._use_a:
            pbo_write = self._pbo_a
            pbo_read = self._pbo_b
        else:
            pbo_write = self._pbo_b
            pbo_read = self._pbo_a
        self._use_a = not self._use_a

        # Kick async read
        self.fbo.read_into(pbo_write, components=3, alignment=1)
        # Map previous PBO to CPU (non-blocking if ready)
        data = pbo_read.read()
        if not data:
            # Fallback sync read (first frame)
            data = self.fbo.read(components=3, alignment=1)

        frame = np.frombuffer(data, dtype=np.uint8, count=self.width*self.height*3).reshape(self.height, self.width, 3)
        frame = np.flipud(frame)
        return frame

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
            if 'center_image_zoom' in view_state:
                self._view.set_center_image_zoom(int(view_state.get('center_image_zoom', 100)))

        except Exception:
            pass
        try:
            img_path = view_state.get('center_image_path') or None
            if img_path:
                import os
                p = os.path.abspath(os.path.expanduser(str(img_path)))
                if os.path.exists(p):
                    self._view.set_center_image(p)
                else:
                    # Fall back to whatever was provided
                    self._view.set_center_image(str(img_path))
        except Exception:
            pass

        try:
            # Radial/circular smoothing is always enabled. We only carry the amount.
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
            # Glow block
            self._view.set_glow_enabled(bool(view_state.get('glow_enabled', False)))
            col = view_state.get('glow_color', '#80A0FFFF')
            try:
                if hasattr(col, 'name'):
                    try:
                        self._view.set_glow_color(col.name(QColor.HexArgb))
                    except Exception:
                        self._view.set_glow_color(col.name())
                elif isinstance(col, str):
                    self._view.set_glow_color(col)
            except Exception:
                pass
            self._view.set_glow_radius(int(view_state.get('glow_radius', 22)))
            self._view.set_glow_strength(int(view_state.get('glow_strength', 80)))
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


class QPainterOpenGLOffscreenRenderer(QPainterOffscreenRenderer):
    """GPU-accelerated QPainter path via an offscreen OpenGL framebuffer.

    This renderer keeps the exact same drawing code as the realtime widget.
    The OpenGL paint device allows Qt to use a GPU-backed paint engine where
    available, while still producing frames on the CPU for ffmpeg piping.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from PySide6.QtCore import QSize
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext, QOpenGLFramebufferObject, QOpenGLPaintDevice, QSurfaceFormat

        fmt = QSurfaceFormat()
        try:
            # Reasonable defaults; allow the platform to pick best options.
            fmt.setDepthBufferSize(0)
            fmt.setStencilBufferSize(0)
            fmt.setSamples(0)
        except Exception:
            pass

        self._surface = QOffscreenSurface()
        try:
            self._surface.setFormat(fmt)
        except Exception:
            pass
        self._surface.create()

        self._ctx = QOpenGLContext()
        try:
            self._ctx.setFormat(self._surface.format())
        except Exception:
            pass
        if not self._ctx.create():
            raise RuntimeError("Unable to create an OpenGL context for GPU export")

        if not self._ctx.makeCurrent(self._surface):
            raise RuntimeError("Unable to make OpenGL context current for GPU export")

        self._fbo = QOpenGLFramebufferObject(self.width, self.height)
        self._pdev = QOpenGLPaintDevice(QSize(self.width, self.height))

    def render_frame(self, t):
        samples, spectrum = self._read_frame(t)

        self._ctx.makeCurrent(self._surface)
        self._fbo.bind()
        try:
            from PySide6.QtGui import QPainter, QImage

            p = QPainter(self._pdev)
            try:
                self._view.paint_frame(p, self.width, self.height, samples, spectrum)
            finally:
                try:
                    p.end()
                except Exception:
                    pass

            img = self._fbo.toImage()
            if img.format() != QImage.Format_RGB888:
                img = img.convertToFormat(QImage.Format_RGB888)
        finally:
            try:
                self._fbo.release()
            except Exception:
                pass

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

def _ffmpeg_bin() -> str:
    """
    Resolve the ffmpeg executable in a cross‑platform, bundle‑friendly way:
    Order:
    1) AURORA_FFMPEG env var (explicit override)
    2) IMAGEIO_FFMPEG_EXE env var (respected by imageio-ffmpeg)
    3) imageio_ffmpeg.get_ffmpeg_exe() (bundled via pip wheels)
    4) System PATH
    """
    import os
    # 1) explicit app override
    p = os.environ.get("AURORA_FFMPEG")
    if p and Path(p).exists():
        return p

    # 2) imageio override
    p = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if p and Path(p).exists():
        return p

    # 3) imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and Path(p).exists():
            return p
    except Exception:
        pass

    # 4) fallback to PATH
    bin_path = shutil.which("ffmpeg")
    if not bin_path:
        raise RuntimeError(
            "FFmpeg not found. Install ffmpeg or add dependency 'imageio-ffmpeg', "
            "or set AURORA_FFMPEG/IMAGEIO_FFMPEG_EXE to a valid ffmpeg path."
        )
    return bin_path


def _ffmpeg_encoders(ffmpeg: str) -> set[str]:
    """Return the set of encoder names supported by the ffmpeg binary."""
    try:
        out = subprocess.check_output(
            [ffmpeg, "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
            text=True,
            errors="ignore",
        )
    except Exception:
        return set()

    enc: set[str] = set()
    for line in out.splitlines():
        # Typical format:
        #  " V..... h264_videotoolbox  VideoToolbox H.264 Encoder"
        if not line.startswith(" V"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            enc.add(parts[1].strip())
    return enc


def list_gpu_export_devices() -> List[Dict[str, str]]:
    """Return a list of hardware encode "devices" suitable for a UI dropdown.

    Each entry is a dict:
      - id: stable identifier (stored in INI)
      - label: human friendly

    Notes:
    - This is *encoding* acceleration (e.g. VideoToolbox, NVENC, VAAPI).
    - Rendering remains CPU/QPainter so Visual FX match the UI.
    """
    ffmpeg = _ffmpeg_bin()
    enc = _ffmpeg_encoders(ffmpeg)
    sysname = platform.system()

    opts: List[Dict[str, str]] = []

    # macOS: VideoToolbox
    if sysname == "Darwin":
        if "h264_videotoolbox" in enc or "hevc_videotoolbox" in enc:
            opts.append({"id": "videotoolbox", "label": "Apple VideoToolbox"})
        return opts

    # Windows / Linux: NVENC (NVIDIA)
    if "h264_nvenc" in enc or "hevc_nvenc" in enc:
        gpus: List[str] = []
        smi = shutil.which("nvidia-smi")
        if smi:
            try:
                out = subprocess.check_output([smi, "-L"], text=True, errors="ignore")
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("GPU "):
                        gpus.append(line)
            except Exception:
                gpus = []

        if gpus:
            for i, line in enumerate(gpus):
                # "GPU 0: ..." -> friendly
                label = line
                opts.append({"id": f"nvenc:{i}", "label": f"NVIDIA NVENC ({label})"})
        else:
            # Still expose at least one option if encoder exists
            opts.append({"id": "nvenc:0", "label": "NVIDIA NVENC"})

    # Windows: AMF (AMD)
    if "h264_amf" in enc or "hevc_amf" in enc:
        opts.append({"id": "amf:auto", "label": "AMD AMF"})

    # Windows/Linux: Intel QSV
    if "h264_qsv" in enc or "hevc_qsv" in enc:
        opts.append({"id": "qsv:auto", "label": "Intel Quick Sync (QSV)"})

    # Linux: VAAPI
    if ("h264_vaapi" in enc or "hevc_vaapi" in enc) and sysname == "Linux":
        try:
            dri = Path("/dev/dri")
            if dri.exists():
                render_nodes = sorted([p for p in dri.glob("renderD*")])
                if render_nodes:
                    for p in render_nodes:
                        opts.append({"id": f"vaapi:{p}", "label": f"VAAPI ({p.name})"})
        except Exception:
            pass

    return opts


def _ensure_ext(path: str, default_ext: str) -> str:
    import os
    base, ext = os.path.splitext(path)
    return path if ext else base + default_ext

def build_ffmpeg_cmd(
    width: int,
    height: int,
    fps: int,
    out_path: str,
    audio_path: str,
    prefer_hevc: bool = False,
    gpu_device: Optional[str] = None,
) -> Tuple[List[str], str]:
    """Return (cmd, pix_fmt) for piping RGB frames to ffmpeg.

    If `gpu_device` is provided (or "auto"), this attempts to use a *hardware
    video encoder* (VideoToolbox, NVENC, VAAPI, QSV, AMF). This is what users
    typically expect to see as "GPU activity" during export.

    Rendering is still done via the same QPainter code path so that *all Visual
    FX match the UI*.
    """
    out_path = _ensure_ext(out_path, ".mp4")
    ffmpeg = _ffmpeg_bin()
    enc = _ffmpeg_encoders(ffmpeg)
    sysname = platform.system()

    # Choose a device if requested.
    dev = (gpu_device or "").strip()
    if dev.lower() in {"", "0", "false", "off", "no", "cpu"}:
        dev = ""
    if dev.lower() == "auto":
        opts = list_gpu_export_devices()
        dev = opts[0]["id"] if opts else ""

    # We'll pipe raw RGB; ffmpeg will convert as needed.
    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-r", str(int(fps)),
        "-i", "pipe:0",
        "-i", audio_path,
    ]

    use_hevc = bool(prefer_hevc)
    pix_out = "yuv420p"
    vflags: List[str] = []
    vf_chain: Optional[str] = None

    def _need(encoder: str) -> None:
        if encoder not in enc:
            raise RuntimeError(f"Requested hardware encoder not available in ffmpeg: {encoder}")

    # Hardware encoder selection
    if dev:
        if dev == "videotoolbox" and sysname == "Darwin":
            vcodec = "hevc_videotoolbox" if (use_hevc and "hevc_videotoolbox" in enc) else "h264_videotoolbox"
            _need(vcodec)
            vflags = ["-b:v", "12M", "-maxrate", "20M", "-bufsize", "40M"]
            pix_out = "yuv420p"
        elif dev.startswith("nvenc:") and ("h264_nvenc" in enc or "hevc_nvenc" in enc):
            idx = 0
            try:
                idx = int(dev.split(":", 1)[1])
            except Exception:
                idx = 0
            vcodec = "hevc_nvenc" if (use_hevc and "hevc_nvenc" in enc) else "h264_nvenc"
            _need(vcodec)
            # Select GPU where supported.
            vflags = [
                "-gpu", str(idx),
                "-preset", "p5",
                "-rc", "vbr",
                "-cq", "19",
                "-b:v", "0",
                "-maxrate", "0",
            ]
            pix_out = "yuv420p"
        elif dev.startswith("vaapi:") and sysname == "Linux" and ("h264_vaapi" in enc or "hevc_vaapi" in enc):
            device_path = dev.split(":", 1)[1]
            vcodec = "hevc_vaapi" if (use_hevc and "hevc_vaapi" in enc) else "h264_vaapi"
            _need(vcodec)
            cmd += ["-vaapi_device", device_path]
            vf_chain = "format=nv12,hwupload"
            vflags = ["-b:v", "12M", "-maxrate", "20M", "-bufsize", "40M"]
            pix_out = "nv12"
        elif dev.startswith("qsv:") and ("h264_qsv" in enc or "hevc_qsv" in enc):
            # QSV device selection is platform dependent; keep it simple.
            vcodec = "hevc_qsv" if (use_hevc and "hevc_qsv" in enc) else "h264_qsv"
            _need(vcodec)
            vflags = ["-global_quality", "23"]
            pix_out = "yuv420p"
        elif dev.startswith("amf:") and ("h264_amf" in enc or "hevc_amf" in enc):
            vcodec = "hevc_amf" if (use_hevc and "hevc_amf" in enc) else "h264_amf"
            _need(vcodec)
            vflags = ["-quality", "balanced"]
            pix_out = "yuv420p"
        else:
            # Unknown/unsupported device id for this platform.
            dev = ""

    # Software fallback
    if not dev:
        vcodec = "libx264"
        vflags = ["-preset", "veryfast", "-crf", "20"]
        pix_out = "yuv420p"

    if vf_chain:
        cmd += ["-vf", vf_chain]

    cmd += [
        "-c:v", vcodec,
        *vflags,
        "-pix_fmt", pix_out,
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_path,
    ]

    return cmd, "rgb24"

class Exporter:
    def __init__(
        self,
        audio_path,
        width=1920,
        height=1080,
        fps=60,
        mode="Waveform - Linear",
        color=(0.2, 0.8, 1.0),
        sensitivity=1.0,
        prefer_hevc: bool = False,
        view_state=None,
        gpu_rendering: bool = False,
        gpu_device: Optional[str] = None,
    ):
        self.audio_path = audio_path
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.mode = str(mode)
        self.color = tuple(color)
        self.sensitivity = float(sensitivity)
        self.prefer_hevc = bool(prefer_hevc)
        self.view_state = view_state or {}
        # Back-compat: older UI passed a boolean toggle. If True, use the best
        # available hardware encoder ("auto") unless an explicit device is set.
        self.gpu_rendering = bool(gpu_rendering)
        self.gpu_device = (gpu_device or ("auto" if self.gpu_rendering else ""))
        # passthrough for background/shadow/fill handled by RT widget exporter


    def render_to_file(self, out_path, progress_cb=None):
        # Always use the RT widget path so exports match the UI and respect all FX.
        renderer = QPainterOffscreenRenderer(
            self.width,
            self.height,
            self.mode,
            self.color,
            self.sensitivity,
            self.fps,
            self.audio_path,
            **self.view_state,
        )

        cmd, _ = build_ffmpeg_cmd(
            self.width,
            self.height,
            self.fps,
            out_path,
            self.audio_path,
            prefer_hevc=self.prefer_hevc,
            gpu_device=self.gpu_device,
        )

        return self._run_pipe(renderer, cmd, progress_cb=progress_cb)

    def _run_pipe(self, renderer: OffscreenRenderer, cmd, progress_cb=None):
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7)
        total_frames = int(np.ceil(getattr(renderer, 'duration', 0.0) * self.fps)) if hasattr(renderer, 'duration') else 0
        for i in range(total_frames):
            t = i / self.fps
            frame = renderer.render_time(t)  # H x W x 3 (uint8)
            frame = np.ascontiguousarray(frame)
            try:
                proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                break
            # progress at ~2Hz
            if progress_cb and (i % max(1, int(self.fps/2)) == 0):
                try:
                    pct = int(i * 100 / total_frames) if total_frames else 0
                    progress_cb(pct)
                except Exception:
                    pass
        try:
            if proc.stdin: proc.stdin.close()
        except Exception:
            pass
        try:
            stderr = proc.stderr.read().decode('utf-8', errors='ignore') if proc.stderr else ''
        except Exception:
            stderr = ''
        code = proc.wait()
        if progress_cb:
            try:
                progress_cb(100)
            except Exception:
                pass
        if code != 0:
            raise RuntimeError(f"ffmpeg failed (exit {code}):\n{stderr}")
