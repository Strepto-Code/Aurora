from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _emit(msg: dict) -> None:
    try:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _emit({"type": "error", "message": "Missing config path"})
        return 2

    cfg_path = Path(argv[1]).expanduser().resolve()
    if not cfg_path.exists():
        _emit({"type": "error", "message": f"Config not found: {cfg_path}"})
        return 2

    # Lower priority so the UI stays responsive.
    try:
        if hasattr(os, "nice"):
            os.nice(8)
    except Exception:
        pass

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        _emit({"type": "error", "message": f"Invalid config JSON: {e}"})
        return 2

    # Ensure we can import project modules when launched from a different CWD.
    root = cfg.get("project_root")
    if root:
        sys.path.insert(0, str(Path(root).resolve()))
    else:
        sys.path.insert(0, str(cfg_path.parent.parent.resolve()))

    # Headless Qt for offscreen rendering.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PySide6.QtWidgets import QApplication
        from export.exporter import Exporter
    except Exception as e:
        _emit({"type": "error", "message": f"Failed to import dependencies: {e}"})
        return 2

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    try:
        exporter = Exporter(
            audio_path=cfg["audio_path"],
            width=int(cfg["width"]),
            height=int(cfg["height"]),
            fps=int(cfg["fps"]),
            mode=str(cfg["mode"]),
            color=tuple(cfg["color"]),
            sensitivity=float(cfg["sensitivity"]),
            prefer_hevc=bool(cfg.get("prefer_hevc", False)),
            view_state=cfg.get("view_state") or {},
            gpu_device=cfg.get("gpu_device") or "",
        )

        out_path = cfg["out_path"]

        def on_progress(pct: int) -> None:
            _emit({"type": "progress", "pct": int(pct)})

        exporter.render_to_file(out_path, progress_cb=on_progress)
        _emit({"type": "done"})
        return 0
    except Exception as e:
        _emit({"type": "error", "message": str(e)})
        return 1
    finally:
        try:
            app.quit()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
