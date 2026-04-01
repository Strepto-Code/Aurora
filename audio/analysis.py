import numpy as np
from scipy.signal import get_window


class Analyzer:
    def __init__(self, sample_rate=48000, fft_size=2048):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.window = get_window('hann', self.fft_size, fftbins=True).astype(np.float32)
        self.prev_mag = np.zeros(self.fft_size // 2 + 1, dtype=np.float32)
        self.flux_smooth = 0.0
        self.flux_alpha = 0.9
        self.hop = self.fft_size // 2
        self.buffer = np.zeros(self.fft_size, dtype=np.float32)

    def compute(self, x):
        n = len(x)
        if n >= self.hop:
            self.buffer[:-self.hop] = self.buffer[self.hop:]
            self.buffer[-self.hop:] = x[-self.hop:]
        else:
            self.buffer = np.roll(self.buffer, -n)
            self.buffer[-n:] = x

        windowed = self.buffer * self.window
        mag = np.abs(np.fft.rfft(windowed, n=self.fft_size)).astype(np.float32)

        diff = np.maximum(0.0, mag - self.prev_mag)
        flux = float(np.sum(diff)) / len(mag)
        self.flux_smooth = self.flux_alpha * self.flux_smooth + (1 - self.flux_alpha) * flux

        self.prev_mag = mag
        return np.log1p(mag), self.flux_smooth
