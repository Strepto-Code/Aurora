import logging
import os
import platform
import tempfile
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

from .analysis import Analyzer

logger = logging.getLogger(__name__)


class AudioEngine:
    """Callback-driven audio engine providing playback, transport, and
    per-frame analysis data for the visualizer."""

    def __init__(self, sample_rate=48000, block_size=1024):
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        self._volume = 1.0

        self.out_channels = 2
        try:
            d = sd.default.device
            self._device_out = int(d[1]) if isinstance(d, (list, tuple)) else int(d)
        except Exception:
            self._device_out = None
        self.out_channels = self._pick_output_channels(self._device_out)

        try:
            if platform.system() == "Linux":
                sd.default.latency = (None, 0.08)
            else:
                sd.default.latency = (None, "high")
        except Exception:
            pass

        self.current_audio_path = None
        self._sf = None
        self._sf_sr = None
        self._playing = False
        self._eof = False
        self._tmp_wav_path = None

        self.analyzer = Analyzer(sample_rate=self.sample_rate, fft_size=2048)
        self.out_stream = None

        self._rb_size = max(self.block_size * 16, 8192)
        self._ring = np.zeros(self._rb_size, dtype=np.float32)
        self._rb_write = 0
        self._lock = threading.Lock()
        self._stream_sr = int(sample_rate)

        self._open_output_stream(self.sample_rate)

    # -- Device handling --

    @property
    def output_device_index(self):
        try:
            if self._device_out is not None:
                return int(self._device_out)
            d = sd.default.device
            return int(d[1]) if isinstance(d, (list, tuple)) else int(d)
        except Exception:
            return None

    def list_output_devices(self):
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_output_channels", 0) > 0:
                out.append((i, d["name"]))
        return out

    def set_output_device_by_index(self, index: int):
        self._device_out = int(index)
        self.out_channels = self._pick_output_channels(self._device_out)
        target_sr = int(self._sf_sr) if self._sf is not None and self._sf_sr else self.sample_rate
        self._open_output_stream(target_sr or 48000)

    def _pick_output_channels(self, device_index: int | None) -> int:
        try:
            if device_index is None:
                d = sd.default.device
                device_index = int(d[1]) if isinstance(d, (list, tuple)) else int(d)
            info = sd.query_devices(device_index)
            return 2 if int(info.get("max_output_channels", 0) or 0) >= 2 else 1
        except Exception:
            return 2

    # -- Stream / callback --

    def _audio_callback(self, outdata, frames, time, status):
        if status:
            self._last_status = str(status)

        ch = int(outdata.shape[1]) if outdata.ndim == 2 else 1
        sr_stream = int(self._stream_sr or self.sample_rate or 48000)
        sr_src = int(self._sf_sr) if self._sf_sr is not None else sr_stream

        stereo = np.zeros((frames, ch), dtype=np.float32)

        if self._playing and self._sf is not None:
            need_src = frames if sr_stream == sr_src else max(1, int(np.ceil(frames * sr_src / float(sr_stream))))
            raw = self._sf.read(need_src, dtype='float32', always_2d=True)

            if raw.size == 0:
                self._eof = True
                self._playing = False
            else:
                if raw.shape[1] >= ch:
                    src = raw[:, :ch]
                else:
                    reps = int(np.ceil(ch / raw.shape[1]))
                    src = np.tile(raw, (1, reps))[:, :ch]

                if sr_stream != sr_src:
                    n_src = src.shape[0]
                    if n_src > 1:
                        x_src = np.linspace(0.0, float(n_src - 1), num=n_src, dtype=np.float32)
                        x_tgt = np.linspace(0.0, float(n_src - 1), num=frames, dtype=np.float32)
                        stereo = np.stack(
                            [np.interp(x_tgt, x_src, src[:, c]) for c in range(ch)],
                            axis=1,
                        ).astype(np.float32, copy=False)
                    else:
                        stereo = np.broadcast_to(src[:1, :], (frames, ch)).copy()
                else:
                    stereo = src

                if stereo.shape[0] < frames:
                    pad = np.zeros((frames, ch), dtype=np.float32)
                    pad[:stereo.shape[0], :] = stereo
                    stereo = pad
                elif stereo.shape[0] > frames:
                    stereo = stereo[:frames, :]

        if self._volume != 1.0:
            stereo *= self._volume

        try:
            mono_vis = stereo.mean(axis=1).astype(np.float32, copy=False)
            with self._lock:
                idx = self._rb_write % self._rb_size
                n = min(frames, self._rb_size)
                end = idx + n
                if end <= self._rb_size:
                    self._ring[idx:end] = mono_vis[:n]
                else:
                    first = self._rb_size - idx
                    self._ring[idx:] = mono_vis[:first]
                    self._ring[:n - first] = mono_vis[first:n]
                self._rb_write = (self._rb_write + n) % (1 << 30)
        except Exception:
            pass

        outdata[:, :ch] = stereo
        if outdata.shape[1] > ch:
            outdata[:, ch:] = 0.0

    def _open_output_stream(self, sr):
        try:
            if self.out_stream is not None:
                self.out_stream.abort(ignore_errors=True)
                self.out_stream.close()
        except Exception:
            pass

        channels = int(self._pick_output_channels(self._device_out))
        self.out_channels = channels
        dtype = "float32"
        device = self._device_out if self._device_out is not None else None

        try:
            sd.check_output_settings(device=device, samplerate=sr, channels=channels, dtype=dtype)
        except Exception:
            try:
                info = sd.query_devices(device if device is not None else sd.default.device[1])
                sr = int(info.get("default_samplerate", 48000) or 48000)
            except Exception:
                sr = 48000
            try:
                sd.check_output_settings(device=device, samplerate=sr, channels=channels, dtype=dtype)
            except Exception:
                channels = 1
                self.out_channels = 1

        self.sample_rate = sr
        self._stream_sr = sr
        self.analyzer = Analyzer(sample_rate=sr, fft_size=2048)

        logger.info("Opening output stream: device=%s sr=%s ch=%s", self._device_out, sr, channels)
        self.out_stream = sd.OutputStream(
            device=device, channels=channels, dtype=dtype,
            samplerate=sr, blocksize=self.block_size, callback=self._audio_callback,
        )
        self.out_stream.start()

    # -- Transport --

    def load_file(self, path: str):
        if self._sf is not None:
            try:
                self._sf.close()
            except Exception:
                pass
        if self._tmp_wav_path:
            try:
                os.unlink(self._tmp_wav_path)
            except Exception:
                pass
            self._tmp_wav_path = None

        self.current_audio_path = path

        try:
            self._sf = sf.SoundFile(path, mode="r")
        except Exception:
            try:
                import audioread
            except ImportError as e:
                raise RuntimeError("MP3 support requires 'audioread' (pip install audioread)") from e

            with audioread.audio_open(path) as ar:
                _sr = int(ar.samplerate)
                _ch = int(ar.channels)
                _pcm = b"".join(b for b in ar)

            x = np.frombuffer(_pcm, dtype="<i2").astype(np.float32) / 32768.0
            x = x.reshape(-1, _ch) if _ch > 1 else x.reshape(-1, 1)

            tmp = tempfile.NamedTemporaryFile(prefix="aurora_", suffix=".wav", delete=False)
            tmp_path = tmp.name
            tmp.close()
            sf.write(tmp_path, x, _sr, subtype="PCM_16")
            self._tmp_wav_path = tmp_path
            self._sf = sf.SoundFile(tmp_path, mode="r")

        self._sf_sr = int(self._sf.samplerate)
        self._eof = False
        self._playing = False
        self._sf.seek(0)
        self._open_output_stream(self._sf_sr)

        with self._lock:
            self._ring.fill(0.0)
            self._rb_write = 0

    def play(self):
        if self._sf is not None:
            self._playing = True

    def pause(self):
        self._playing = False

    def jump_to_start(self):
        self.seek_seconds(0.0)

    def seek_seconds(self, seconds: float):
        if self._sf is None:
            return
        frame = max(0, min(int(seconds * self._sf_sr), len(self._sf)))
        self._sf.seek(frame)
        self._eof = False
        with self._lock:
            self._ring.fill(0.0)
            self._rb_write = 0

    seek = seek_seconds

    # -- Visuals frame --

    def get_frame(self):
        with self._lock:
            n = self.block_size
            end = self._rb_write % self._rb_size
            start = (end - n) % self._rb_size
            if start < end:
                samples = self._ring[start:end].copy()
            else:
                samples = np.concatenate((self._ring[start:], self._ring[:end])).copy()

        if samples.shape[0] != self.block_size:
            tmp = np.zeros(self.block_size, dtype=np.float32)
            ncopy = min(len(samples), self.block_size)
            tmp[-ncopy:] = samples[-ncopy:]
            samples = tmp

        spectrum, flux = self.analyzer.compute(samples)
        return samples, spectrum, flux

    # -- Info --

    def get_duration_seconds(self):
        if self._sf is None:
            return 0.0
        try:
            return float(len(self._sf)) / float(self._sf_sr)
        except Exception:
            return 0.0

    def get_position_seconds(self):
        if self._sf is None:
            return 0.0
        try:
            return float(self._sf.tell()) / float(self._sf_sr)
        except Exception:
            return 0.0

    # -- Config --

    def set_volume(self, vol):
        try:
            self._volume = float(vol)
        except Exception:
            self._volume = 1.0

    def set_latency_seconds(self, seconds: float):
        try:
            sd.default.latency = (None, float(seconds))
        except Exception:
            pass
        sr = self.out_stream.samplerate if self.out_stream is not None else self.sample_rate
        self._open_output_stream(sr)

    def set_block_size(self, frames: int):
        try:
            self.block_size = int(frames)
        except Exception:
            return
        with self._lock:
            self._rb_size = max(self.block_size * 16, 8192)
            self._ring = np.zeros(self._rb_size, dtype=np.float32)
            self._rb_write = 0
        sr = self.out_stream.samplerate if self.out_stream is not None else self.sample_rate
        self._open_output_stream(sr)

    def test_output_device(self, seconds: float = 0.5, freq: float = 440.0):
        try:
            sr = self.sample_rate
            t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
            y = (0.2 * np.sin(2 * np.pi * freq * t)).astype('float32')
            sd.play(y, samplerate=sr, device=self.output_device_index)
            sd.wait()
        except Exception:
            pass

    # -- Lifecycle --

    def close(self):
        try:
            if self.out_stream is not None:
                self.out_stream.abort(ignore_errors=True)
                self.out_stream.close()
        except Exception:
            pass
        try:
            if self._sf is not None:
                self._sf.close()
        except Exception:
            pass
        try:
            if self._tmp_wav_path:
                os.unlink(self._tmp_wav_path)
                self._tmp_wav_path = None
        except Exception:
            pass
