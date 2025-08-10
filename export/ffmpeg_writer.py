
import platform
import subprocess
import shutil
from pathlib import Path
from typing import List, Tuple, Optional

def _ffmpeg_bin() -> str:
    """
    Resolve the ffmpeg executable in a cross‑platform, bundle‑friendly way.

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
) -> Tuple[List[str], str]:
    """
    Return (cmd, pix_fmt) for piping RGB frames to ffmpeg with hardware acceleration.
    - macOS -> h264_videotoolbox / hevc_videotoolbox
    - Linux -> h264_vaapi / hevc_vaapi (requires VAAPI configured)
    Fallback to libx264 if hw encoders unavailable.
    """
    out_path = _ensure_ext(out_path, ".mp4")
    sysname = platform.system()
    ffmpeg = _ffmpeg_bin()

    # We'll pipe raw RGB; ffmpeg can convert to the encoder's preferred pix_fmt.
    in_args = [
        ffmpeg,
        "-y",
        # input 0 = video frames over stdin
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "pipe:0",
        # input 1 = audio file
        "-i", audio_path,
    ]

    # Choose codec per platform
    use_hevc = bool(prefer_hevc)
    vcodec = None
    vflags: List[str] = []
    map_hwupload = []

    if sysname == "Darwin":
        # VideoToolbox
        vcodec = "hevc_videotoolbox" if use_hevc else "h264_videotoolbox"
        # Quality: use target bitrate and quality factor
        vflags = ["-b:v", "8M", "-maxrate", "12M", "-bufsize", "24M"]
        pix_out = "yuv420p"
    elif sysname == "Linux":
    # Prefer software encoding for compatibility; VAAPI often fails on headless/Wayland.
    # We'll default to libx264 and only use VAAPI if explicitly forced elsewhere.
        # VAAPI
        vcodec = "hevc_vaapi" if use_hevc else "h264_vaapi"
        # We must upload to HW and convert to NV12
        map_hwupload = ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload"]
        vflags = ["-b:v", "8M", "-maxrate", "12M", "-bufsize", "24M"]
        pix_out = "nv12"
    else:
        # Fallback (Windows/other) - software
        vcodec = "libx264"
        vflags = ["-preset", "veryfast", "-crf", "20"]
        pix_out = "yuv420p"

    # Assemble cmd; if hw encoder missing, ffmpeg will error – caller can catch and retry with libx264.
    out_args = [
        "-f", "mp4",
        "-c:v", vcodec,
        *vflags,
        "-pix_fmt", pix_out,
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_path,
    ]

    cmd = in_args + map_hwupload + out_args
    return cmd, "rgb24"
