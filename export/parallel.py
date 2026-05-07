"""Parallel multi-process rendering for offline export.

Workers render frames in parallel. The master precomputes per-frame
state vectors (_phase, _amp_ema, _radial_prev) so workers can render
in any order with output identical to the sequential pipeline.
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
    """Compute per-frame energy, phase, amp_ema, spectrum, and radial_prev
    arrays in a single pass over the audio. Workers consume these instead
    of accumulating state themselves."""
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

    # Per-frame radial_prev: snapshot the EMA state *before* frame N
    # mutates it. Workers inject this so paint reproduces sequential output.
    radial_prevs_in = None
    if radial_temporal_alpha > 0.0:
        from widgets.rt_widget import RTVisualizerWidget
        helper = RTVisualizerWidget(audio_engine=None, start_timer=False)
        helper.radial_wave_smoothness = int(radial_wave_smoothness)

        prev = None
        N = 80
        radial_prev_in_list: List[Optional[np.ndarray]] = []
        for i in range(total_frames):
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
    """Worker entry point. Pulls frame indices from job_q, returns
    (idx, bytes) on result_q. Errors are reported as ("__error__", msg)."""
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from export.exporter import QPainterOffscreenRenderer

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

            # Master pre-computed the FFT; worker only needs the raw samples.
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

            view._phase = float(phases[idx])
            view._amp_ema = float(amp_emas[idx])
            if radial_prevs_in is not None:
                rp = radial_prevs_in[idx]
                view._radial_prev = None if rp is None else rp.copy()
            else:
                view._radial_prev = None

            # Bypass render_frame_to_qimage so paint_frame can be called with
            # advance_state=False (avoids re-mutating injected phase/amp_ema).
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
    """Patch paint_frame to skip phase advance; master pre-injects _phase."""
    original = view.paint_frame
    def patched(p, w, h, samples, spectrum):
        return original(p, w, h, samples, spectrum, advance_state=False)
    view.paint_frame = patched


class ParallelExporter:
    """Worker-pool replacement for the sequential _run_pipe loop."""

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

        # Probe renderer to get duration and audio. Discarded after use;
        # workers each construct their own renderer.
        from export.exporter import QPainterOffscreenRenderer

        probe = QPainterOffscreenRenderer(
            e.width, e.height, e.mode, e.color,
            e.sensitivity, e.fps, e.audio_path, **(e.view_state or {}),
        )
        total_frames = int(np.ceil(probe.duration * e.fps))

        # Threshold below which worker startup overhead exceeds parallel gain.
        breakeven = 30 * 3 * self.num_workers
        if total_frames < breakeven or self.num_workers == 1:
            del probe
            return e._run_pipe_sequential(cmd, progress_cb=progress_cb)

        logger.info("Parallel export: %d frames across %d workers", total_frames, self.num_workers)

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
        # Small job_q so worker failures surface quickly; larger result_q
        # absorbs bursts without throttling fast workers.
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

        def _dispatch():
            for i in range(total_frames):
                job_q.put(i)
            for _ in workers:
                job_q.put(None)

        dispatch_t = threading.Thread(target=_dispatch, daemon=True)
        dispatch_t.start()

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
