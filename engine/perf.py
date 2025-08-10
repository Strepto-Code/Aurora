
import time
from dataclasses import dataclass

@dataclass
class PerfFlags:
    safe_mode: bool = False
    fps_cap: int = 60
    hud_enabled: bool = True

class FrameLimiter:
    def __init__(self, fps: int):
        self.set_fps(fps)
        self._last = time.monotonic()

    def set_fps(self, fps: int):
        self.fps = max(1, int(fps))
        self._frame_dt = 1.0 / float(self.fps)

    def wait(self):
        now = time.monotonic()
        next_time = self._last + self._frame_dt
        delay = next_time - now
        if delay > 0:
            time.sleep(delay)
            now = time.monotonic()
        self._last = now
