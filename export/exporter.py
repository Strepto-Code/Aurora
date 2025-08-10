import os
import platform
import subprocess
import numpy as np
from .offscreen_renderer import OffscreenRenderer
from .qpainter_offscreen import QPainterOffscreenRenderer
from .ffmpeg_writer import build_ffmpeg_cmd

class Exporter:
    def __init__(self, audio_path, width=1920, height=1080, fps=60,
                 mode="Waveform - Linear", color=(0.2,0.8,1.0), sensitivity=1.0,
                 prefer_hevc=False, view_state=None):
        self.audio_path = audio_path
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.mode = str(mode)
        self.color = tuple(color)
        self.sensitivity = float(sensitivity)
        self.prefer_hevc = bool(prefer_hevc)
        self.view_state = view_state or {}
        # passthrough for background/shadow/fill handled by RT widget exporter


    def render_to_file(self, out_path, progress_cb=None):
        # Choose renderer
        # Choose renderer: use QPainter if radial spectrum OR if we need background/center image
        use_qp = ('Spectrum - Radial' in self.mode)
        try:
            if self.view_state.get('shadow_enabled'):
                use_qp = True
        except Exception:
            pass
        try:
            if self.view_state.get('center_image_path') or self.view_state.get('background_path'):
                use_qp = True
        except Exception:
            pass
        if use_qp:
            renderer = QPainterOffscreenRenderer(self.width, self.height, self.mode, self.color, self.sensitivity, self.fps, self.audio_path, **self.view_state)
        else:
            renderer = OffscreenRenderer(self.width, self.height, self.mode, self.color, self.sensitivity, self.fps, self.audio_path)
            # Pass particle params for GL path (best-effort)
            try:
                renderer.particle_density = self.view_state.get('particle_density', 200)
                renderer.particle_speed = self.view_state.get('particle_speed', 1.0)
                renderer.particle_glow = self.view_state.get('particle_glow', 0.6)
            except Exception:
                pass

        # Build ffmpeg command
        cmd, _ = build_ffmpeg_cmd(self.width, self.height, self.fps, out_path, self.audio_path, self.prefer_hevc)

        # Try command, fallback to software if hwaccel fails
        if not cmd:
            sw_cmd = ['ffmpeg', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                      '-s', f'{self.width}x{self.height}', '-r', str(self.fps),
                      '-i', 'pipe:0', '-i', self.audio_path,
                      '-c:v', 'libx264', '-crf', '20', '-pix_fmt', 'yuv420p',
                      '-c:a', 'aac', '-b:a', '192k', '-shortest', out_path]
            return self._run_pipe(renderer, sw_cmd, progress_cb=progress_cb)
        else:
            try:
                return self._run_pipe(renderer, cmd, progress_cb=progress_cb)
            except RuntimeError:
                sw_cmd = ['ffmpeg', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                          '-s', f'{self.width}x{self.height}', '-r', str(self.fps),
                          '-i', 'pipe:0', '-i', self.audio_path,
                          '-c:v', 'libx264', '-crf', '20', '-pix_fmt', 'yuv420p',
                          '-c:a', 'aac', '-b:a', '192k', '-shortest', out_path]
                return self._run_pipe(renderer, sw_cmd, progress_cb=progress_cb)

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
