from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
from PySide6.QtGui import QColor

from audio.analysis import Analyzer
from config.settings import BackgroundConfig
from widgets.rt_widget import RTVisualizerWidget

logger = logging.getLogger(__name__)


def _color_hex(col, fallback="#FFFFFFFF") -> str:
    if isinstance(col, str):
        return col
    if hasattr(col, 'name'):
        try:
            return col.name(QColor.HexArgb)
        except Exception:
            return col.name()
    return fallback


class _Feeder:
    """get_frame() shim for headless RTVisualizerWidget during export."""

    def __init__(self, block_size: int = 1024):
        self.block_size = int(max(1, block_size))
        self._samples = np.zeros(self.block_size, dtype=np.float32)
        self._spectrum = np.zeros(2048 // 2 + 1, dtype=np.float32)
        self._flux = 0.0

    def get_frame(self):
        return self._samples, self._spectrum, float(self._flux)


class _FFmpegDecodedAudio:
    """Decode non-WAV audio to raw float32 via ffmpeg subprocess."""

    def __init__(self, path, target_sr=None):
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg is None:
            raise RuntimeError('ffmpeg not found for decoding non-WAV formats.')
        self.samplerate = int(target_sr or 48000)
        cmd = [
            ffmpeg, '-v', 'error', '-i', path,
            '-f', 'f32le', '-acodec', 'pcm_f32le',
            '-ac', '1', '-ar', str(self.samplerate), '-',
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw = proc.stdout.read()
        proc.wait()
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else b'').decode('utf-8', 'ignore')
            raise RuntimeError(f'ffmpeg decode failed: {err}')
        self._data = np.frombuffer(raw, dtype=np.float32)
        self._pos = 0

    def __len__(self):
        return int(self._data.shape[0])

    def seek(self, frame):
        self._pos = int(max(0, min(len(self._data), frame)))

    def read(self, n, dtype='float32', always_2d=False):
        n = int(max(0, n))
        end = min(len(self._data), self._pos + n)
        y = self._data[self._pos:end].astype(np.float32, copy=False)
        self._pos = end
        if always_2d:
            y = y.reshape(-1, 1)
        return y


def _qimage_to_numpy(img):
    w, h = img.width(), img.height()
    ptr = img.bits()
    try:
        buf = memoryview(ptr)
    except TypeError:
        byte_count = img.sizeInBytes() if hasattr(img, 'sizeInBytes') else img.byteCount()
        if hasattr(ptr, 'setsize'):
            ptr.setsize(byte_count)
        buf = ptr.asstring(byte_count)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, img.bytesPerLine()))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


def _qimage_to_rgb_bytes(img, width, height, out=None):
    """Return packed RGB888 bytes from a QImage. Reuses `out` if supplied
    and large enough. Strips any row padding."""
    bpl = img.bytesPerLine()
    row_bytes = width * 3
    needed = row_bytes * height
    ptr = img.bits()
    try:
        mv = memoryview(ptr).cast("B")
    except TypeError:
        byte_count = img.sizeInBytes() if hasattr(img, 'sizeInBytes') else img.byteCount()
        if hasattr(ptr, 'setsize'):
            ptr.setsize(byte_count)
        mv = memoryview(ptr.asstring(byte_count))
    if bpl == row_bytes:
        return bytes(mv[:needed])
    if out is None or len(out) < needed:
        out = bytearray(needed)
    for y in range(height):
        src = y * bpl
        dst = y * row_bytes
        out[dst:dst + row_bytes] = mv[src:src + row_bytes]
    return bytes(out) if not isinstance(out, (bytes, bytearray)) else out


class QPainterOffscreenRenderer:
    def __init__(self, width, height, mode, color, sensitivity, fps, audio_path, **view_state):
        self.width = int(width)
        self.height = int(height)
        self.mode = mode
        self.color = color
        self.sensitivity = float(sensitivity or 1.0)
        self.fps = int(fps)
        self.audio_path = audio_path

        self._sf = None
        try:
            self._sf = sf.SoundFile(self.audio_path, 'r')
        except Exception:
            self._sf = _FFmpegDecodedAudio(self.audio_path)

        # Pre-load full audio: avoids per-frame sf.seek + small read.
        self._sample_rate = int(self._sf.samplerate)
        self._sf.seek(0)
        try:
            audio = self._sf.read(dtype='float32', always_2d=True)
        except TypeError:
            audio = self._sf.read(len(self._sf), dtype='float32', always_2d=True)
        if audio.ndim > 1 and audio.shape[1] > 1:
            audio = audio.mean(axis=1)
        else:
            audio = audio.reshape(-1)
        self._audio = np.ascontiguousarray(audio, dtype=np.float32)
        self._total_samples = int(self._audio.shape[0])
        self.duration = float(self._total_samples / self._sample_rate)
        self._an = Analyzer(sample_rate=self._sample_rate, fft_size=2048)

        self._feeder = _Feeder()
        self._view = RTVisualizerWidget(audio_engine=self._feeder, start_timer=False)
        self._view._offscreen_paint = True
        self._view._offscreen_size = (self.width, self.height)

        self._apply_base_config(mode, color, sensitivity)
        self._apply_view_state(view_state)

    def _apply_base_config(self, mode, color, sensitivity):
        try:
            self._view.set_mode(mode)
        except Exception:
            pass
        try:
            self._view.set_color(color)
        except Exception:
            pass
        try:
            self._view.set_sensitivity(sensitivity)
        except Exception:
            pass

    def _apply_view_state(self, vs):
        v = self._view

        try:
            bcfg = BackgroundConfig(
                path=vs.get('background_path'),
                scale_mode=vs.get('background_scale_mode', 'fill'),
                offset_x=int(vs.get('background_offset_x', 0) or 0),
                offset_y=int(vs.get('background_offset_y', 0) or 0),
                dim_percent=int(vs.get('background_dim_percent', 0) or 0),
            )
            v.set_background_config(bcfg)
        except Exception:
            pass

        try:
            v.set_radial_rotation_deg(vs.get('radial_rotation_deg', 0.0))
            v.set_radial_mirror(bool(vs.get('radial_mirror', False)))
        except Exception:
            pass

        try:
            v.set_feather_enabled(bool(vs.get('feather_enabled', False)))
            v.set_edge_waviness(int(vs.get('edge_waviness', 0)))
            v.set_feather_audio_enabled(bool(vs.get('feather_audio_enabled', False)))
            v.set_feather_audio_amount(int(vs.get('feather_audio_amount', 0)))
            v.set_center_motion(int(vs.get('center_motion', 0)))
            if 'center_image_zoom' in vs:
                v.set_center_image_zoom(int(vs.get('center_image_zoom', 100)))
        except Exception:
            pass

        try:
            img_path = vs.get('center_image_path')
            if img_path:
                p = os.path.abspath(os.path.expanduser(str(img_path)))
                if os.path.exists(p):
                    v.set_center_image(p)
                else:
                    v.set_center_image(str(img_path))
        except Exception:
            pass

        try:
            v.set_radial_smooth(True)
            v.set_radial_smooth_amount(int(vs.get('radial_smooth_amount', 50)))
        except Exception:
            pass

        try:
            v.set_radial_fill_enabled(bool(vs.get('radial_fill_enabled', False)))
            v.set_radial_fill_color(_color_hex(vs.get('radial_fill_color', '#80FFFFFF')))
            v.set_radial_fill_blend(vs.get('radial_fill_blend', 'normal'))
            v.set_radial_fill_threshold(float(vs.get('radial_fill_threshold', 0.1)))
        except Exception:
            pass

        try:
            v.set_glow_enabled(bool(vs.get('glow_enabled', False)))
            v.set_glow_color(_color_hex(vs.get('glow_color', '#80A0FFFF')))
            v.set_glow_radius(int(vs.get('glow_radius', 22)))
            v.set_glow_strength(int(vs.get('glow_strength', 80)))
        except Exception:
            pass

        try:
            if 'radial_waveform_smoothness' in vs:
                v.set_radial_waveform_smoothness(int(vs['radial_waveform_smoothness']))
            if 'radial_temporal_smoothing' in vs:
                v.set_radial_temporal_smoothing(int(vs['radial_temporal_smoothing']))
        except Exception:
            pass

        try:
            grad_a = vs.get('grad_a')
            grad_b = vs.get('grad_b')
            if grad_a or grad_b:
                v.set_gradient_colors(grad_a, grad_b)
            if 'grad_curve' in vs:
                v.set_gradient_curve(str(vs['grad_curve']))
            if 'grad_min' in vs and 'grad_max' in vs:
                v.set_gradient_clamp(float(vs['grad_min']), float(vs['grad_max']))
            if 'grad_smoothing' in vs:
                v.set_gradient_smoothing(float(vs['grad_smoothing']))
        except Exception:
            pass

        try:
            if 'shadow_enabled' in vs:
                v.set_shadow_enabled(bool(vs['shadow_enabled']))
            if 'shadow_opacity' in vs:
                v.set_shadow_opacity(int(vs['shadow_opacity']))
            if 'shadow_blur_radius' in vs:
                v.set_shadow_blur_radius(int(vs['shadow_blur_radius']))
            if 'shadow_distance' in vs:
                v.set_shadow_distance(int(vs['shadow_distance']))
            if 'shadow_angle_deg' in vs:
                v.set_shadow_angle_deg(int(vs['shadow_angle_deg']))
            if 'shadow_spread' in vs:
                v.set_shadow_spread(int(vs['shadow_spread']))
        except Exception:
            pass

    def _read_frame(self, t):
        sr = self._sample_rate
        hop = max(1, int(sr / self.fps))
        pos = int(max(0, min(self._total_samples - 1, t * sr)))
        end = min(self._total_samples, pos + hop)
        y = self._audio[pos:end]
        samples = np.zeros(1024, dtype=np.float32)
        n = min(len(y), 1024)
        samples[:n] = y[:n]
        spec, flux = self._an.compute(samples)
        self._feeder._samples[:] = samples
        if self._feeder._spectrum.shape[0] != spec.shape[0]:
            self._feeder._spectrum = np.zeros_like(spec)
        self._feeder._spectrum[:] = spec
        self._feeder._flux = float(flux)
        return samples, spec

    def render_frame(self, t):
        samples, spectrum = self._read_frame(t)
        img = self._view.render_frame_to_qimage(self.width, self.height, samples, spectrum)
        return _qimage_to_numpy(img)

    def render_bytes(self, t):
        samples, spectrum = self._read_frame(t)
        img = self._view.render_frame_to_qimage(self.width, self.height, samples, spectrum)
        return _qimage_to_rgb_bytes(img, self.width, self.height)

    def render_time(self, t):
        return self.render_frame(t)


class QPainterOpenGLOffscreenRenderer(QPainterOffscreenRenderer):
    """GPU-backed QPainter via offscreen OpenGL FBO. Falls back to CPU if
    GL context creation fails (caller catches the exception)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from PySide6.QtCore import QSize
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
        try:
            from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLPaintDevice
        except ImportError:
            from PySide6.QtGui import QOpenGLFramebufferObject, QOpenGLPaintDevice

        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(0)
        # Stencil 8 required for QPainter clip paths.
        fmt.setStencilBufferSize(8)
        fmt.setSamples(0)

        self._surface = QOffscreenSurface()
        self._surface.setFormat(fmt)
        self._surface.create()

        self._ctx = QOpenGLContext()
        self._ctx.setFormat(self._surface.format())
        if not self._ctx.create():
            raise RuntimeError("Unable to create OpenGL context for GPU export")
        if not self._ctx.makeCurrent(self._surface):
            raise RuntimeError("Unable to make OpenGL context current for GPU export")

        self._fbo = QOpenGLFramebufferObject(self.width, self.height)
        self._pdev = QOpenGLPaintDevice(QSize(self.width, self.height))

    def render_frame(self, t):
        from PySide6.QtGui import QPainter, QImage

        samples, spectrum = self._read_frame(t)

        self._ctx.makeCurrent(self._surface)
        self._fbo.bind()
        try:
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

        return _qimage_to_numpy(img)

    def render_bytes(self, t):
        from PySide6.QtGui import QPainter, QImage

        samples, spectrum = self._read_frame(t)

        self._ctx.makeCurrent(self._surface)
        self._fbo.bind()
        try:
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

        return _qimage_to_rgb_bytes(img, self.width, self.height)


# -- ffmpeg utilities --

def _ffmpeg_bin() -> str:
    """Resolve ffmpeg: AURORA_FFMPEG env, IMAGEIO_FFMPEG_EXE env,
    imageio_ffmpeg package, then system PATH."""
    for env_var in ("AURORA_FFMPEG", "IMAGEIO_FFMPEG_EXE"):
        p = os.environ.get(env_var)
        if p and Path(p).exists():
            return p

    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and Path(p).exists():
            return p
    except Exception:
        pass

    bin_path = shutil.which("ffmpeg")
    if not bin_path:
        raise RuntimeError(
            "FFmpeg not found. Install ffmpeg or pip install imageio-ffmpeg, "
            "or set AURORA_FFMPEG/IMAGEIO_FFMPEG_EXE."
        )
    return bin_path


def _ffmpeg_encoders(ffmpeg: str) -> set[str]:
    try:
        out = subprocess.check_output(
            [ffmpeg, "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT, text=True, errors="ignore",
        )
    except Exception:
        return set()

    enc: set[str] = set()
    for line in out.splitlines():
        if not line.startswith(" V"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            enc.add(parts[1].strip())
    return enc


def _ensure_ext(path: str, default_ext: str) -> str:
    base, ext = os.path.splitext(path)
    return path if ext else base + default_ext


def list_gpu_export_devices() -> List[Dict[str, str]]:
    """Detect available hardware video encoders for the export UI dropdown."""
    ffmpeg = _ffmpeg_bin()
    enc = _ffmpeg_encoders(ffmpeg)
    sysname = platform.system()
    opts: List[Dict[str, str]] = []

    if sysname == "Darwin":
        if "h264_videotoolbox" in enc or "hevc_videotoolbox" in enc:
            opts.append({"id": "videotoolbox", "label": "Apple VideoToolbox"})
        return opts

    if "h264_nvenc" in enc or "hevc_nvenc" in enc:
        gpus: List[str] = []
        smi = shutil.which("nvidia-smi")
        if smi:
            try:
                out = subprocess.check_output([smi, "-L"], text=True, errors="ignore")
                gpus = [l.strip() for l in out.splitlines() if l.strip().startswith("GPU ")]
            except Exception:
                pass
        if gpus:
            for i, line in enumerate(gpus):
                opts.append({"id": f"nvenc:{i}", "label": f"NVIDIA NVENC ({line})"})
        else:
            opts.append({"id": "nvenc:0", "label": "NVIDIA NVENC"})

    if "h264_amf" in enc or "hevc_amf" in enc:
        opts.append({"id": "amf:auto", "label": "AMD AMF"})

    if "h264_qsv" in enc or "hevc_qsv" in enc:
        opts.append({"id": "qsv:auto", "label": "Intel Quick Sync (QSV)"})

    if ("h264_vaapi" in enc or "hevc_vaapi" in enc) and sysname == "Linux":
        try:
            dri = Path("/dev/dri")
            if dri.exists():
                for p in sorted(dri.glob("renderD*")):
                    opts.append({"id": f"vaapi:{p}", "label": f"VAAPI ({p.name})"})
        except Exception:
            pass

    return opts


def build_ffmpeg_cmd(
    width: int, height: int, fps: int, out_path: str, audio_path: str,
    prefer_hevc: bool = False, gpu_device: Optional[str] = None,
) -> Tuple[List[str], str]:
    out_path = _ensure_ext(out_path, ".mp4")
    ffmpeg = _ffmpeg_bin()
    enc = _ffmpeg_encoders(ffmpeg)
    sysname = platform.system()

    dev = (gpu_device or "").strip()
    if dev.lower() in {"", "0", "false", "off", "no", "cpu"}:
        dev = ""
    if dev.lower() == "auto":
        opts = list_gpu_export_devices()
        dev = opts[0]["id"] if opts else ""

    cmd: List[str] = [
        ffmpeg, "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(int(fps)),
        "-i", "pipe:0", "-i", audio_path,
    ]

    use_hevc = bool(prefer_hevc)
    pix_out = "yuv420p"
    vflags: List[str] = []
    vf_chain: Optional[str] = None

    def _need(encoder):
        if encoder not in enc:
            raise RuntimeError(f"Hardware encoder not available: {encoder}")

    if dev:
        if dev == "videotoolbox" and sysname == "Darwin":
            vcodec = "hevc_videotoolbox" if (use_hevc and "hevc_videotoolbox" in enc) else "h264_videotoolbox"
            _need(vcodec)
            # VideoToolbox uses -q:v (0-100) for VBR; -crf is ignored.
            # -realtime 0 selects the higher-quality non-realtime encoder.
            quality = "65" if use_hevc else "60"
            vflags = [
                "-q:v", quality,
                "-allow_sw", "1",
                "-realtime", "0",
                "-g", str(int(fps) * 2),
            ]
        elif dev.startswith("nvenc:") and ("h264_nvenc" in enc or "hevc_nvenc" in enc):
            idx = 0
            try:
                idx = int(dev.split(":", 1)[1])
            except Exception:
                pass
            vcodec = "hevc_nvenc" if (use_hevc and "hevc_nvenc" in enc) else "h264_nvenc"
            _need(vcodec)
            vflags = [
                "-gpu", str(idx),
                "-preset", "p5",
                "-rc", "vbr",
                "-cq", "23",
                "-b:v", "0",
                "-g", str(int(fps) * 2),
            ]
        elif dev.startswith("vaapi:") and sysname == "Linux" and ("h264_vaapi" in enc or "hevc_vaapi" in enc):
            device_path = dev.split(":", 1)[1]
            vcodec = "hevc_vaapi" if (use_hevc and "hevc_vaapi" in enc) else "h264_vaapi"
            _need(vcodec)
            cmd += ["-vaapi_device", device_path]
            vf_chain = "format=nv12,hwupload"
            vflags = ["-qp", "23", "-g", str(int(fps) * 2)]
            pix_out = "nv12"
        elif dev.startswith("qsv:") and ("h264_qsv" in enc or "hevc_qsv" in enc):
            vcodec = "hevc_qsv" if (use_hevc and "hevc_qsv" in enc) else "h264_qsv"
            _need(vcodec)
            vflags = ["-global_quality", "23", "-g", str(int(fps) * 2)]
        elif dev.startswith("amf:") and ("h264_amf" in enc or "hevc_amf" in enc):
            vcodec = "hevc_amf" if (use_hevc and "hevc_amf" in enc) else "h264_amf"
            _need(vcodec)
            vflags = ["-quality", "balanced", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24", "-g", str(int(fps) * 2)]
        else:
            dev = ""

    if not dev:
        vcodec = "libx264"
        vflags = ["-preset", "veryfast", "-crf", "20", "-g", str(int(fps) * 2)]

    if vf_chain:
        cmd += ["-vf", vf_chain]

    cmd += [
        "-c:v", vcodec, *vflags,
        "-pix_fmt", pix_out,
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", out_path,
    ]
    return cmd, "rgb24"


class Exporter:
    def __init__(
        self, audio_path, width=1920, height=1080, fps=60,
        mode="Waveform - Linear", color=(0.2, 0.8, 1.0), sensitivity=1.0,
        prefer_hevc=False, view_state=None,
        gpu_rendering=False, gpu_device=None,
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
        self.gpu_rendering = bool(gpu_rendering)
        self.gpu_device = gpu_device or ("auto" if self.gpu_rendering else "")

    def render_to_file(self, out_path, progress_cb=None):
        renderer_args = (
            self.width, self.height, self.mode, self.color,
            self.sensitivity, self.fps, self.audio_path,
        )
        # macOS: skip GL path. glReadPixels readback dominates frame time;
        # CPU raster is faster for this workload.
        prefer_gl = platform.system() != "Darwin"

        cmd, _ = build_ffmpeg_cmd(
            self.width, self.height, self.fps, out_path, self.audio_path,
            prefer_hevc=self.prefer_hevc, gpu_device=self.gpu_device,
        )

        if not prefer_gl:
            try:
                from export.parallel import ParallelExporter
                pex = ParallelExporter(self)
                logger.info("Export using parallel CPU raster QPainter")
                return pex.run(cmd, progress_cb=progress_cb)
            except Exception as exc:
                logger.info("Parallel export failed (%s), falling back to sequential", exc)

        renderer = None
        if prefer_gl:
            try:
                renderer = QPainterOpenGLOffscreenRenderer(*renderer_args, **self.view_state)
                logger.info("Export using GPU-backed QPainter (OpenGL offscreen)")
            except Exception as exc:
                logger.info("GPU-backed export unavailable (%s), using CPU raster", exc)
        if renderer is None:
            renderer = QPainterOffscreenRenderer(*renderer_args, **self.view_state)
            if not prefer_gl:
                logger.info("Export using CPU raster QPainter (sequential)")

        return self._run_pipe_sequential_with_renderer(renderer, cmd, progress_cb=progress_cb)

    def _run_pipe_sequential(self, cmd, progress_cb=None):
        """Sequential fallback used by ParallelExporter when total_frames is
        too small to benefit from worker startup overhead."""
        renderer_args = (
            self.width, self.height, self.mode, self.color,
            self.sensitivity, self.fps, self.audio_path,
        )
        renderer = QPainterOffscreenRenderer(*renderer_args, **self.view_state)
        return self._run_pipe_sequential_with_renderer(renderer, cmd, progress_cb=progress_cb)

    def _run_pipe_sequential_with_renderer(self, renderer, cmd, progress_cb=None):
        return self._run_pipe(renderer, cmd, progress_cb=progress_cb)

    def _run_pipe(self, renderer, cmd, progress_cb=None):
        import time as _time
        import threading
        import queue

        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=10 ** 7,
        )
        total_frames = int(np.ceil(renderer.duration * self.fps)) if hasattr(renderer, 'duration') else 0

        # Writer thread overlaps encoding with rendering. Bounded queue
        # backpressures the renderer when the encoder lags.
        write_queue: queue.Queue = queue.Queue(maxsize=8)
        write_error = {"err": None}

        def _writer():
            try:
                while True:
                    item = write_queue.get()
                    if item is None:
                        break
                    try:
                        proc.stdin.write(item)
                    except BrokenPipeError as e:
                        write_error["err"] = e
                        break
            except Exception as e:
                write_error["err"] = e

        writer_thread = threading.Thread(target=_writer, daemon=True)
        writer_thread.start()

        t_start = _time.monotonic()
        last_emit = 0.0
        emit_interval = 0.1

        try:
            for i in range(total_frames):
                if write_error["err"] is not None:
                    break
                t = i / self.fps
                buf = renderer.render_bytes(t)
                write_queue.put(buf)
                if progress_cb:
                    now = _time.monotonic()
                    if now - last_emit >= emit_interval or i == total_frames - 1:
                        last_emit = now
                        elapsed = now - t_start
                        fps = (i + 1) / elapsed if elapsed > 0 else 0.0
                        pct = int((i + 1) * 100 / total_frames) if total_frames else 0
                        try:
                            progress_cb({
                                "pct": pct, "frame": i + 1, "total": total_frames,
                                "fps": fps, "elapsed": elapsed,
                            })
                        except Exception:
                            pass
        finally:
            write_queue.put(None)
            writer_thread.join()

        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            stderr = proc.stderr.read().decode('utf-8', errors='ignore') if proc.stderr else ''
        except Exception:
            stderr = ''
        code = proc.wait()
        if progress_cb:
            try:
                progress_cb({"pct": 100, "frame": total_frames, "total": total_frames,
                             "fps": 0.0, "elapsed": _time.monotonic() - t_start})
            except Exception:
                pass
        if code != 0:
            raise RuntimeError(f"ffmpeg failed (exit {code}):\n{stderr}")
