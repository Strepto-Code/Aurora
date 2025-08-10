
import numpy as np
import soundfile as sf
import moderngl
from visuals.waveform import WaveformRenderer
from visuals.spectrum import SpectrumRenderer
from visuals.particles import ParticleRenderer
from audio.analysis import Analyzer
import subprocess, shutil

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
