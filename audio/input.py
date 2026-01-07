import platform
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
from .analysis import Analyzer


class AudioEngine:
    def test_output_device(self, seconds: float = 0.5, freq: float = 440.0):
        """Plays a short tone on the current output device to verify sound."""
        try:
            import numpy as _np, sounddevice as _sd
            sr = int(getattr(self, 'sample_rate', 48000))
            t = _np.linspace(0, seconds, int(sr * seconds), endpoint=False)
            y = (0.2 * _np.sin(2 * _np.pi * freq * t)).astype('float32')
            _sd.play(y, samplerate=sr, device=self.output_device_index)
            _sd.wait()
        except Exception:
            pass

    """
    Callback-driven engine with:
      - load_file(path), play(), pause(), seek_seconds(t), jump_to_start()
      - get_frame() -> (samples, spectrum, flux)
      - get_duration_seconds(), get_position_seconds()
      - set_output_device_by_index(idx), list_output_devices(), output_device_index
      - set_volume(vol), set_latency_seconds(sec), set_block_size(frames)
      - close()
    """

    def __init__(self, sample_rate=48000, block_size=1024):
        self.sample_rate = int(sample_rate)  # preferred; may be overridden to match file
        self.block_size = int(block_size)
        self._volume = 1.0

        # Prefer stereo everywhere, but gracefully fall back to mono if the
        # selected device only supports a single output channel.
        self.out_channels = 2
        try:
            d = sd.default.device
            self._device_out = int(d[1]) if isinstance(d, (list, tuple)) else int(d)
        except Exception:
            self._device_out = None

        # Now that we know the default output device, select a compatible
        # channel count.
        self.out_channels = self._pick_output_channels(self._device_out)

        try:
            if platform.system() == "Linux":
                sd.default.latency = (None, 0.08)  # slightly higher default for stability
            else:
                sd.default.latency = (None, "high")
        except Exception:
            pass

        # Audio file / transport
        self.current_audio_path = None
        self._sf = None
        self._sf_sr = None
        self._playing = False
        self._eof = False
        self._tmp_wav_path = None  # holds decoded MP3 -> WAV path (fallback)

        # Analysis
        self.analyzer = Analyzer(sample_rate=self.sample_rate, fft_size=2048)

        # Output stream / callback
        self.out_stream = None

        # Ring buffer for visuals (mono)
        self._rb_size = max(self.block_size * 16, 8192)
        self._ring = np.zeros(self._rb_size, dtype=np.float32)
        self._rb_write = 0
        self._lock = threading.Lock()

        # Track the current stream samplerate for safe callback fallbacks
        self._stream_sr = int(sample_rate)

        # Open default stream at preferred rate
        self._open_output_stream(self.sample_rate)

    # ---------- device handling ----------
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

        # Recompute channel count for the new device.
        self.out_channels = self._pick_output_channels(self._device_out)

        # Prefer the audio file's samplerate to avoid pitch/speed changes
        target_sr = int(self._sf_sr) if getattr(self, '_sf', None) is not None and getattr(self, '_sf_sr',
                                                                                           None) else int(
            self.sample_rate)
        if not target_sr:
            target_sr = 48000
        self._open_output_stream(target_sr)

    # ---------- stream / callback ----------

    def _pick_output_channels(self, device_index: int | None) -> int:
        """Return 2 if the device supports stereo, otherwise 1."""
        try:
            if device_index is None:
                d = sd.default.device
                device_index = int(d[1]) if isinstance(d, (list, tuple)) else int(d)
            info = sd.query_devices(device_index)
            max_out = int(info.get("max_output_channels", 0) or 0)
            return 2 if max_out >= 2 else 1
        except Exception:
            # Best-effort: assume stereo.
            return 2

    def _audio_callback(self, outdata, frames, time, status):
        import numpy as np

        if status:
            self._last_status = str(status)

        # Determine channel count from the buffer PortAudio gives us.
        # This prevents mismatches if the stream fell back to mono.
        ch = int(outdata.shape[1]) if getattr(outdata, "ndim", 1) == 2 else 1

        # Prefer the stream's samplerate if known, then instance sample_rate, then 48k
        sr_stream = int(
            (getattr(self, "_stream_sr", None)
             or getattr(self, "sample_rate", None)
             or 48000)
        )

        # Source samplerate: if no file yet, fall back to stream samplerate
        _sf_sr = getattr(self, "_sf_sr", None)
        sr_src = int(_sf_sr) if _sf_sr is not None else sr_stream

        # Prepare output buffer (stereo or desired channel count)
        stereo = np.zeros((frames, ch), dtype=np.float32)

        if getattr(self, "_playing", False) and getattr(self, "_sf", None) is not None:
            # Determine how many source frames to read (account for resampling)
            need_src = frames if sr_stream == sr_src else max(1, int(np.ceil(frames * sr_src / float(sr_stream))))

            raw = self._sf.read(need_src, dtype='float32', always_2d=True)  # shape: (n, src_ch)
            if raw.size == 0:
                self._eof = True
                self._playing = False
            else:
                # Channel mapping: trim or upmix to 'ch'
                if raw.shape[1] >= ch:
                    src = raw[:, :ch]
                else:
                    reps = int(np.ceil(ch / raw.shape[1]))
                    src = np.tile(raw, (1, reps))[:, :ch]  # (n, ch)

                # Resample (simple linear) if stream SR != source SR
                if sr_stream != sr_src:
                    n_src = src.shape[0]
                    if n_src > 1:
                        x_src = np.linspace(0.0, float(n_src - 1), num=n_src, dtype=np.float32)
                        x_tgt = np.linspace(0.0, float(n_src - 1), num=frames, dtype=np.float32)
                        stereo = np.stack(
                            [np.interp(x_tgt, x_src, src[:, c]) for c in range(ch)],
                            axis=1
                        ).astype(np.float32, copy=False)
                else:
                    stereo = src

                # Pad or trim to exactly 'frames'
                if stereo.shape[0] < frames:
                    pad = np.zeros((frames, ch), dtype=np.float32)
                    pad[:stereo.shape[0], :] = stereo
                    stereo = pad
                elif stereo.shape[0] > frames:
                    stereo = stereo[:frames, :]

        # Apply volume
        vol = float(getattr(self, "_volume", 1.0))
        if vol != 1.0:
            stereo *= vol

        # Write mono preview for visuals or ring buffer
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
            # Visuals are best effort
            pass

        # Final write to device
        outdata[:, :ch] = stereo
        if outdata.shape[1] > ch:
            outdata[:, ch:] = 0.0

    def _open_output_stream(self, sr):
        # Close existing
        try:
            if self.out_stream is not None:
                self.out_stream.abort(ignore_errors=True)
                self.out_stream.close()
        except Exception:
            pass

        # Re-evaluate channels for the currently selected output device.
        channels = int(self._pick_output_channels(self._device_out))
        self.out_channels = channels
        dtype = "float32"

        # Validate samplerate and open
        try:
            sd.check_output_settings(
                device=self._device_out if self._device_out is not None else None,
                samplerate=sr,
                channels=channels,
                dtype=dtype,
            )
        except Exception:
            try:
                info = sd.query_devices(self._device_out if self._device_out is not None else sd.default.device[1])
                sr = int(info.get("default_samplerate", 48000) or 48000)
            except Exception:
                sr = 48000

            # If the channel count was the problem, fall back to mono.
            try:
                sd.check_output_settings(
                    device=self._device_out if self._device_out is not None else None,
                    samplerate=sr,
                    channels=channels,
                    dtype=dtype,
                )
            except Exception:
                channels = 1
                self.out_channels = 1

        # Update analyzer to stream rate
        self.sample_rate = sr
        self._stream_sr = sr  # expose current stream rate for callback
        self.analyzer = Analyzer(sample_rate=sr, fft_size=2048)

        # Open callback stream
        print(f"Opening output stream: device={self._device_out} sr={sr} ch={channels}")
        self.out_stream = sd.OutputStream(
            device=self._device_out if self._device_out is not None else None,
            channels=channels,
            dtype=dtype,
            samplerate=sr,
            blocksize=self.block_size,
            callback=self._audio_callback,
        )
        self.out_stream.start()

    # ---------- transport ----------
    def load_file(self, path: str):
        # close previous
        if self._sf is not None:
            try:
                self._sf.close()
            except Exception:
                pass
        # cleanup old temp
        if getattr(self, "_tmp_wav_path", None):
            try:
                import os as _os
                _os.unlink(self._tmp_wav_path)
            except Exception:
                pass
            self._tmp_wav_path = None

        self.current_audio_path = path

        # Try native via soundfile
        try:
            self._sf = sf.SoundFile(path, mode="r")
        except Exception:
            # MP3 or unsupported types: decode via audioread to temp WAV
            import numpy as _np, tempfile as _temp
            try:
                import audioread
            except Exception as e:
                raise RuntimeError("MP3 support requires 'audioread' (pip install audioread)") from e

            with audioread.audio_open(path) as ar:
                _sr = int(ar.samplerate)
                _ch = int(ar.channels)
                _pcm = b"".join(b for b in ar)  # 16-bit LE PCM frames

            x = _np.frombuffer(_pcm, dtype="<i2").astype(_np.float32) / 32768.0
            if _ch > 1:
                x = x.reshape(-1, _ch)
            else:
                x = x.reshape(-1, 1)

            tmp = _temp.NamedTemporaryFile(prefix="aurora_", suffix=".wav", delete=False)
            tmp_path = tmp.name
            tmp.close()
            sf.write(tmp_path, x, _sr, subtype="PCM_16")
            self._tmp_wav_path = tmp_path
            self._sf = sf.SoundFile(tmp_path, mode="r")

        self._sf_sr = int(self._sf.samplerate)
        self._eof = False
        self._playing = False
        self._sf.seek(0)

        # reopen stream to match file samplerate to avoid resampling overhead
        self._open_output_stream(self._sf_sr)

        # reset ring buffer
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
        frame = max(0, int(seconds * self._sf_sr))
        frame = min(frame, len(self._sf))
        self._sf.seek(frame)
        self._eof = False
        # Clear ring so visuals snap to the new position
        with self._lock:
            self._ring.fill(0.0)
            self._rb_write = 0

    # ---------- frame for visuals ----------
    def get_frame(self):
        # Pull last block_size mono samples from ring
        with self._lock:
            n = self.block_size
            end = self._rb_write % self._rb_size
            start = (end - n) % self._rb_size
            if start < end:
                samples = self._ring[start:end].copy()
            else:
                samples = np.concatenate((self._ring[start:], self._ring[:end])).copy()

        # In pause with empty ring, return zeros sized correctly
        if samples.shape[0] != self.block_size:
            tmp = np.zeros(self.block_size, dtype=np.float32)
            ncopy = min(len(samples), self.block_size)
            tmp[-ncopy:] = samples[-ncopy:]
            samples = tmp

        spectrum, flux = self.analyzer.compute(samples)
        return samples, spectrum, flux

    # ---------- info ----------
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

    # ---------- config ----------
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
        # reopen stream at current rate
        sr = self.out_stream.samplerate if self.out_stream is not None else self.sample_rate
        self._open_output_stream(sr)

    def set_block_size(self, frames: int):
        try:
            self.block_size = int(frames)
        except Exception:
            return
        # resize ring
        with self._lock:
            self._rb_size = max(self.block_size * 16, 8192)
            self._ring = np.zeros(self._rb_size, dtype=np.float32)
            self._rb_write = 0
        # reopen to apply new block size
        sr = self.out_stream.samplerate if self.out_stream is not None else self.sample_rate
        self._open_output_stream(sr)

    # alias for older UI code
    def seek(self, seconds: float):
        self.seek_seconds(seconds)

    # ---------- lifecycle ----------
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
        # remove temp decoded file if any
        try:
            if getattr(self, "_tmp_wav_path", None):
                import os as _os
                _os.unlink(self._tmp_wav_path)
                self._tmp_wav_path = None
        except Exception:
            pass
