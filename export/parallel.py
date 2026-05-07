"""Parallel multi-process rendering for offline export.

Renders frames across multiple worker processes to overlap CPU-bound
QPainter work with itself, plus the encoder. Each worker is a clean
process with its own QApplication, view widget, and pre-loaded audio
copy. The master computes per-frame deterministic state vectors
(_phase, _amp_ema, _radial_prev) and dispatches them along with frame
indices, so workers don't need to replay the full frame sequence to
catch up on stateful EMAs.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import platform
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _build_state_vectors(
    audio: np.ndarray, sample_rate: int, total_frames: int, fps: int,
    waveform_sensitivity: float, amp_alpha: float, radial_temporal_alpha: float,
    radial_mirror: bool = True, radial_wave_smoothness: int = 50,
    spectrum_len_hint: int = 1025,
) -> Dict[str, np.ndarray]:
    """Walk the audio once to produce per-frame energy, phase, amp_ema,
    spectrum, and radial_prev arrays. Workers read these instead of
    accumulating state themselves, so frames render in any order with
    output identical to the sequential path."""
    from audio.analysis import Analyzer
    from widgets.rt_widget import RTVisualizerWidget

    hop = max(1, int(sample_rate / fps))
    analyzer = Analyzer(sample_rate=sample_rate, fft_size=2048)
    total_samples = audio.shape[0]

    energies = np.zeros(total_frames, dtype=np.float32)
    spectrums = None

    for i in range(total_frames):
        pos = int(max(0, min(total_samples - 1, (i / fps) * sample_rate)))
        end = min(total_samples, pos + hop)
        y = audio[pos:end]
        samples = np.zeros(1024, dtype=np.float32)
        n = min(len(y), 1024)
        samples[:n] = y[:n]
        spec, _flux = analyzer.compute(samples)
        if spectrums is None:
            spectrums = np.zeros((total_frames, spec.shape[0]), dtype=np.float32)
        spectrums[i, :] = spec
        energies[i] = float(np.clip(np.mean(np.abs(samples)) * waveform_sensitivity, 0.0, 1.0))

    phase_increments = 0.04 * (0.5 + energies)
    phases = np.cumsum(phase_increments).astype(np.float32) % (2.0 * np.pi)

    amp_emas = np.zeros(total_frames, dtype=np.float32)
    ema = 0.0
    for i in range(total_frames):
        ema = amp_alpha * ema + (1.0 - amp_alpha) * float(energies[i])
        amp_emas[i] = ema

    # Precompute radial_prev: replay the same EMA the widget does, but
    # over the spec_draw arrays (normalized + optionally mirrored +
    # spatially smoothed). For each frame N we record the EMA value
    # *before* frame N is rendered (i.e. the EMA after frame N-1), so
    # workers can inject it as `_radial_prev` and have the widget's
    # paint produce identical output to the sequential path.
    radial_prevs_in = None
    if radial_temporal_alpha > 0.0:
        from widgets.rt_widget import RTVisualizerWidget
        helper = RTVisualizerWidget(audio_engine=None, start_timer=False)
        helper.radial_wave_smoothness = int(radial_wave_smoothness)

        prev = None
        N = 80
        radial_prev_in_list: List[Optional[np.ndarray]] = []
        for i in range(total_frames):
            # Snapshot the EMA state *before* this frame's mutation - this
            # is what the worker injects so the widget's paint reproduces
            # the same result as sequential.
            radial_prev_in_list.append(None if prev is None else prev.copy().astype(np.float32))

            spec = spectrums[i, :N]
            mx = float(np.max(spec)) if np.max(spec) > 0 else 1.0
            spec_n = spec / mx
            spec_draw = np.concatenate([spec_n, spec_n[::-1][1:-1]]) if radial_mirror else spec_n
            if len(spec_draw) > 3 and radial_wave_smoothness > 0:
                spec_draw = helper._smooth_closed(spec_draw, int(radial_wave_smoothness))
            if prev is None or len(prev) != len(spec_draw):
                prev = spec_draw.copy()
            else:
                prev = radial_temporal_alpha * prev + (1.0 - radial_temporal_alpha) * spec_draw

        radial_prevs_in = radial_prev_in_list

    return {
        "energies": energies,
        "phases": phases,
        "amp_emas": amp_emas,
        "spectrums": spectrums,
        "radial_prevs_in": radial_prevs_in,
    }


def _worker_main(
    worker_id: int,
    job_q: mp.Queue,
    result_q: mp.Queue,
    init_payload: Dict[str, Any],
):
    """Worker process: render frames assigned via job_q, push (idx, bytes)
    to result_q. Designed to be quiet on errors so the master can detect
    and shut down cleanly."""
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from export.exporter import QPainterOffscreenRenderer

        # Build the renderer with the same args as the single-process path,
        # then patch its preloaded audio array from the init payload to
        # avoid each worker decoding the full audio file itself.
        renderer = QPainterOffscreenRenderer(
            init_payload["width"], init_payload["height"],
            init_payload["mode"], tuple(init_payload["color"]),
            init_payload["sensitivity"], init_payload["fps"],
            init_payload["audio_path"], **(init_payload.get("view_state") or {}),
        )

        spectrums = init_payload["spectrums"]
        phases = init_payload["phases"]
        amp_emas = init_payload["amp_emas"]
        energies = init_payload["energies"]
        radial_prevs_in = init_payload.get("radial_prevs_in")
        sample_rate = renderer._sample_rate
        hop = max(1, int(sample_rate / renderer.fps))
        total_samples = renderer._total_samples
        audio = renderer._audio
        view = renderer._view
        feeder = renderer._feeder

        view.radial_temporal_alpha = float(init_payload.get("radial_temporal_alpha", view.radial_temporal_alpha))

        from PySide6.QtGui import QPainter, QImage
        from export.exporter import _qimage_to_rgb_bytes

        out_buf = bytearray(renderer.width * renderer.height * 3)

        while True:
            job = job_q.get()
            if job is None:
                break
            idx = int(job)
            t = idx / renderer.fps

            # Pull samples for this exact frame (no FFT - master already did it).
            pos = int(max(0, min(total_samples - 1, t * sample_rate)))
            end = min(total_samples, pos + hop)
            y = audio[pos:end]
            samples = np.zeros(1024, dtype=np.float32)
            n = min(len(y), 1024)
            samples[:n] = y[:n]
            spectrum = spectrums[idx]

            feeder._samples[:] = samples
            if feeder._spectrum.shape != spectrum.shape:
                feeder._spectrum = np.zeros_like(spectrum)
            feeder._spectrum[:] = spectrum
            feeder._flux = 0.0

            # Inject deterministic state for this frame so the visual
            # exactly matches what a sequential renderer would produce.
            view._phase = float(phases[idx])
            view._amp_ema = float(amp_emas[idx])
            if radial_prevs_in is not None:
                rp = radial_prevs_in[idx]
                view._radial_prev = None if rp is None else rp.copy()
            else:
                view._radial_prev = None

            # Render directly through paint_frame with state advance disabled.
            # The widget's render_frame_to_qimage path always calls
            # paint_frame with the default advance, which would re-mutate
            # phase / amp_ema. Bypass it.
            from PySide6.QtGui import QPainter, QImage as QImg
            img = view._img
            w_, h_ = renderer.width, renderer.height
            if img is None or img.width() != w_ or img.height() != h_ or img.format() != QImg.Format_RGB888:
                img = QImg(w_, h_, QImg.Format_RGB888)
                view._img = img
            painter = QPainter(img)
            try:
                view.paint_frame(painter, w_, h_, samples, spectrum, advance_state=False)
            finally:
                try:
                    painter.end()
                except Exception:
                    pass
            buf = _qimage_to_rgb_bytes(img, w_, h_, out=out_buf)

            result_q.put((idx, bytes(buf) if not isinstance(buf, bytes) else buf))
    except Exception as e:
        try:
            result_q.put(("__error__", f"worker {worker_id}: {e}"))
        except Exception:
            pass


def _patch_paint_frame_for_static_state(view):
    """Patch the view's paint_frame to skip phase advance during parallel
    rendering. The master pre-injects _phase per frame."""
    original = view.paint_frame
    def patched(p, w, h, samples, spectrum):
        return original(p, w, h, samples, spectrum, advance_state=False)
    view.paint_frame = patched


class ParallelExporter:
    """Drop-in replacement for Exporter._run_pipe single-frame loop using
    a worker pool. Falls back to sequential if num_workers <= 1."""

    def __init__(self, exporter, num_workers: Optional[int] = None):
        self.exporter = exporter
        if num_workers is None:
            try:
                num_workers = max(1, (os.cpu_count() or 4) - 1)
            except Exception:
                num_workers = 1
        self.num_workers = max(1, int(num_workers))

    def run(self, cmd, progress_cb=None):
        import subprocess
        import threading
        import queue as queue_mod
        import time as _time

        e = self.exporter

        # Build a probe renderer in the master to get duration, sample rate,
        # and audio array. We then dispose of it; workers will rebuild their
        # own. Doing it twice is cheap (audio is mmapped or loaded once).
        from export.exporter import QPainterOffscreenRenderer

        probe = QPainterOffscreenRenderer(
            e.width, e.height, e.mode, e.color,
            e.sensitivity, e.fps, e.audio_path, **(e.view_state or {}),
        )
        total_frames = int(np.ceil(probe.duration * e.fps))

        # Parallel rendering pays off when render time exceeds worker
        # startup overhead (~3s per worker for Qt init + audio load).
        # Use a frame-count threshold scaled by worker count: at 30 fps
        # render rate, ~30 * 3 * num_workers frames would just break even
        # against a single-process render.
        breakeven = 30 * 3 * self.num_workers
        if total_frames < breakeven or self.num_workers == 1:
            del probe
            return e._run_pipe_sequential(cmd, progress_cb=progress_cb)

        logger.info("Parallel export: %d frames across %d workers", total_frames, self.num_workers)

        # Precompute per-frame state vectors (cheap, single pass).
        state = _build_state_vectors(
            audio=probe._audio,
            sample_rate=probe._sample_rate,
            total_frames=total_frames,
            fps=e.fps,
            waveform_sensitivity=float(probe._view.waveform_sensitivity),
            amp_alpha=float(probe._view._amp_alpha),
            radial_temporal_alpha=float(probe._view.radial_temporal_alpha),
            radial_mirror=bool(probe._view.radial_mirror),
            radial_wave_smoothness=int(probe._view.radial_wave_smoothness),
        )
        radial_temporal_alpha = float(probe._view.radial_temporal_alpha)
        del probe

        init_payload = {
            "width": e.width, "height": e.height, "mode": e.mode,
            "color": list(e.color), "sensitivity": e.sensitivity,
            "fps": e.fps, "audio_path": e.audio_path,
            "view_state": e.view_state,
            "phases": state["phases"], "amp_emas": state["amp_emas"],
            "energies": state["energies"], "spectrums": state["spectrums"],
            "radial_prevs_in": state["radial_prevs_in"],
            "radial_temporal_alpha": radial_temporal_alpha,
        }

        ctx = mp.get_context("spawn")
        # job_q must be small so a stalled worker doesn't queue all frames
        # before failure surfaces. result_q can be larger to absorb burst
        # output without throttling fast workers.
        job_q: mp.Queue = ctx.Queue(maxsize=self.num_workers * 2)
        result_q: mp.Queue = ctx.Queue(maxsize=self.num_workers * 4)

        workers = []
        for wid in range(self.num_workers):
            wp = ctx.Process(
                target=_worker_main,
                args=(wid, job_q, result_q, init_payload),
                daemon=True,
            )
            wp.start()
            workers.append(wp)

        # ffmpeg pipe + writer thread
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=10 ** 7,
        )
        write_q: queue_mod.Queue = queue_mod.Queue(maxsize=16)
        write_err = {"err": None}

        def _writer():
            try:
                while True:
                    item = write_q.get()
                    if item is None:
                        break
                    try:
                        proc.stdin.write(item)
                    except BrokenPipeError as ex:
                        write_err["err"] = ex
                        break
            except Exception as ex:
                write_err["err"] = ex

        writer_t = threading.Thread(target=_writer, daemon=True)
        writer_t.start()

        # Job dispatcher thread feeds the job queue
        def _dispatch():
            for i in range(total_frames):
                job_q.put(i)
            for _ in workers:
                job_q.put(None)

        dispatch_t = threading.Thread(target=_dispatch, daemon=True)
        dispatch_t.start()

        # Master: drain results in order via a re-order buffer
        pending: Dict[int, bytes] = {}
        next_idx = 0
        t_start = _time.monotonic()
        last_emit = 0.0
        emit_interval = 0.1
        worker_error: Optional[str] = None

        try:
            while next_idx < total_frames:
                if write_err["err"] is not None:
                    break
                try:
                    msg = result_q.get(timeout=30.0)
                except Exception:
                    worker_error = "worker timeout (no frames produced for 30s)"
                    break
                if isinstance(msg, tuple) and msg and msg[0] == "__error__":
                    worker_error = str(msg[1])
                    break
                idx, buf = msg
                pending[idx] = buf
                # Drain in order
                while next_idx in pending:
                    write_q.put(pending.pop(next_idx))
                    next_idx += 1
                    if progress_cb:
                        now = _time.monotonic()
                        if now - last_emit >= emit_interval or next_idx == total_frames:
                            last_emit = now
                            elapsed = now - t_start
                            fps = next_idx / elapsed if elapsed > 0 else 0.0
                            pct = int(next_idx * 100 / total_frames) if total_frames else 0
                            try:
                                progress_cb({
                                    "pct": pct, "frame": next_idx, "total": total_frames,
                                    "fps": fps, "elapsed": elapsed,
                                })
                            except Exception:
                                pass
        finally:
            write_q.put(None)
            writer_t.join()
            for wp in workers:
                if wp.is_alive():
                    try:
                        wp.terminate()
                    except Exception:
                        pass
            for wp in workers:
                try:
                    wp.join(timeout=2)
                except Exception:
                    pass

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
        if worker_error:
            raise RuntimeError(f"export worker failed: {worker_error}")
        if code != 0:
            raise RuntimeError(f"ffmpeg failed (exit {code}):\n{stderr}")
