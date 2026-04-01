from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any, Dict

from config.settings import CONFIG_DIR

STATE_INI_FILE = os.path.join(CONFIG_DIR, "state.ini")


def _ensure_dir() -> None:
    Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)


def _get(cp: configparser.ConfigParser, section: str, key: str, default: Any) -> Any:
    if not cp.has_section(section) or not cp.has_option(section, key):
        return default
    return cp.get(section, key, fallback=default)


def _get_int(cp, section, key, default):
    try:
        return int(_get(cp, section, key, str(default)))
    except Exception:
        return int(default)


def _get_float(cp, section, key, default):
    try:
        return float(_get(cp, section, key, str(default)))
    except Exception:
        return float(default)


def _get_bool(cp, section, key, default):
    v = str(_get(cp, section, key, "1" if default else "0")).strip().lower()
    return v in {"1", "true", "yes", "on"}


def load_state_ini() -> Dict[str, Any]:
    cp = configparser.ConfigParser()
    if not os.path.exists(STATE_INI_FILE):
        return {}
    try:
        cp.read(STATE_INI_FILE, encoding="utf-8")
    except Exception:
        return {}

    return {
        "mode": str(_get(cp, "ui", "mode", "")),
        "realtime_fps": _get_int(cp, "ui", "realtime_fps", 60),
        "sensitivity": _get_float(cp, "ui", "sensitivity", 1.0),
        "volume": _get_float(cp, "ui", "volume", 1.0),
        "output_device_index": _get_int(cp, "ui", "output_device_index", -1),
        "color": str(_get(cp, "ui", "color", "")),
        "fx_tab": _get_int(cp, "ui", "fx_tab", 0),

        "radial_rotation_deg": _get_float(cp, "radial", "rotation_deg", 0.0),
        "radial_mirror": _get_bool(cp, "radial", "mirror", True),
        "radial_smooth_amount": _get_int(cp, "radial", "smooth_amount", 50),
        "center_motion": _get_int(cp, "radial", "center_motion", 0),
        "center_image_zoom": _get_int(cp, "radial", "center_image_zoom", 100),
        "edge_waviness": _get_int(cp, "radial", "edge_waviness", 30),
        "feather_audio_enabled": _get_bool(cp, "radial", "feather_audio_enabled", False),
        "feather_audio_amount": _get_int(cp, "radial", "feather_audio_amount", 40),
        "center_image_path": str(_get(cp, "radial", "center_image_path", "")),
        "radial_waveform_smoothness": _get_int(cp, "radial", "radial_waveform_smoothness", 50),
        "radial_temporal_smoothing": _get_int(cp, "radial", "radial_temporal_smoothing", 30),

        "export_width": _get_int(cp, "export", "width", 1280),
        "export_height": _get_int(cp, "export", "height", 720),
        "export_fps": _get_int(cp, "export", "fps", 60),
        "export_gpu": _get_bool(cp, "export", "gpu", False),
        "export_gpu_device": str(_get(cp, "export", "gpu_device", "")),

        "bg_path": str(_get(cp, "fx_background", "path", "")),
        "bg_scale_mode": str(_get(cp, "fx_background", "scale_mode", "fill")),
        "bg_offset_x": _get_int(cp, "fx_background", "offset_x", 0),
        "bg_offset_y": _get_int(cp, "fx_background", "offset_y", 0),
        "bg_dim_percent": _get_int(cp, "fx_background", "dim_percent", 0),

        "grad_a": str(_get(cp, "gradient", "a", "")),
        "grad_b": str(_get(cp, "gradient", "b", "")),
        "grad_curve": str(_get(cp, "gradient", "curve", "linear")),
        "grad_min": _get_float(cp, "gradient", "clamp_min", 0.0),
        "grad_max": _get_float(cp, "gradient", "clamp_max", 1.0),
        "grad_smoothing": _get_float(cp, "gradient", "smoothing", 0.2),

        "shadow_enabled": _get_bool(cp, "fx_shadow", "enabled", False),
        "shadow_opacity": _get_int(cp, "fx_shadow", "opacity", 50),
        "shadow_blur_radius": _get_int(cp, "fx_shadow", "blur_radius", 16),
        "shadow_distance": _get_int(cp, "fx_shadow", "distance", 8),
        "shadow_angle_deg": _get_int(cp, "fx_shadow", "angle_deg", 45),
        "shadow_spread": _get_int(cp, "fx_shadow", "spread", 6),

        "glow_enabled": _get_bool(cp, "fx_glow", "enabled", False),
        "glow_color": str(_get(cp, "fx_glow", "color", "")),
        "glow_radius": _get_int(cp, "fx_glow", "radius", 22),
        "glow_strength": _get_int(cp, "fx_glow", "strength", 80),

        "fill_enabled": _get_bool(cp, "fx_radial_fill", "enabled", False),
        "fill_color": str(_get(cp, "fx_radial_fill", "color", "")),
        "fill_blend": str(_get(cp, "fx_radial_fill", "blend", "normal")),
        "fill_threshold": _get_float(cp, "fx_radial_fill", "threshold", 0.1),
    }


def save_state_ini(state: Dict[str, Any]) -> None:
    _ensure_dir()
    cp = configparser.ConfigParser()

    def s(section, key, value):
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, key, str(value))

    s("ui", "mode", state.get("mode", ""))
    s("ui", "realtime_fps", int(state.get("realtime_fps", 60)))
    s("ui", "sensitivity", float(state.get("sensitivity", 1.0)))
    s("ui", "volume", float(state.get("volume", 1.0)))
    odi = state.get("output_device_index", -1)
    s("ui", "output_device_index", int(odi) if odi is not None else -1)
    s("ui", "color", state.get("color", ""))
    s("ui", "fx_tab", int(state.get("fx_tab", 0)))

    s("radial", "rotation_deg", float(state.get("radial_rotation_deg", 0.0)))
    s("radial", "mirror", 1 if state.get("radial_mirror", True) else 0)
    s("radial", "smooth_amount", int(state.get("radial_smooth_amount", 50)))
    s("radial", "center_motion", int(state.get("center_motion", 0)))
    s("radial", "center_image_zoom", int(state.get("center_image_zoom", 100)))
    s("radial", "edge_waviness", int(state.get("edge_waviness", 30)))
    s("radial", "feather_audio_enabled", 1 if state.get("feather_audio_enabled", False) else 0)
    s("radial", "feather_audio_amount", int(state.get("feather_audio_amount", 40)))
    s("radial", "center_image_path", state.get("center_image_path", ""))
    s("radial", "radial_waveform_smoothness", int(state.get("radial_waveform_smoothness", 50)))
    s("radial", "radial_temporal_smoothing", int(state.get("radial_temporal_smoothing", 30)))

    s("export", "width", int(state.get("export_width", 1280)))
    s("export", "height", int(state.get("export_height", 720)))
    s("export", "fps", int(state.get("export_fps", 60)))
    s("export", "gpu", 1 if state.get("export_gpu", False) else 0)
    s("export", "gpu_device", state.get("export_gpu_device", ""))

    s("fx_background", "path", state.get("bg_path", ""))
    s("fx_background", "scale_mode", state.get("bg_scale_mode", "fill"))
    s("fx_background", "offset_x", int(state.get("bg_offset_x", 0)))
    s("fx_background", "offset_y", int(state.get("bg_offset_y", 0)))
    s("fx_background", "dim_percent", int(state.get("bg_dim_percent", 0)))

    s("gradient", "a", state.get("grad_a", ""))
    s("gradient", "b", state.get("grad_b", ""))
    s("gradient", "curve", state.get("grad_curve", "linear"))
    s("gradient", "clamp_min", float(state.get("grad_min", 0.0)))
    s("gradient", "clamp_max", float(state.get("grad_max", 1.0)))
    s("gradient", "smoothing", float(state.get("grad_smoothing", 0.2)))

    s("fx_shadow", "enabled", 1 if state.get("shadow_enabled", False) else 0)
    s("fx_shadow", "opacity", int(state.get("shadow_opacity", 50)))
    s("fx_shadow", "blur_radius", int(state.get("shadow_blur_radius", 16)))
    s("fx_shadow", "distance", int(state.get("shadow_distance", 8)))
    s("fx_shadow", "angle_deg", int(state.get("shadow_angle_deg", 45)))
    s("fx_shadow", "spread", int(state.get("shadow_spread", 6)))

    s("fx_glow", "enabled", 1 if state.get("glow_enabled", False) else 0)
    s("fx_glow", "color", state.get("glow_color", ""))
    s("fx_glow", "radius", int(state.get("glow_radius", 22)))
    s("fx_glow", "strength", int(state.get("glow_strength", 80)))

    s("fx_radial_fill", "enabled", 1 if state.get("fill_enabled", False) else 0)
    s("fx_radial_fill", "color", state.get("fill_color", ""))
    s("fx_radial_fill", "blend", state.get("fill_blend", "normal"))
    s("fx_radial_fill", "threshold", float(state.get("fill_threshold", 0.1)))

    try:
        with open(STATE_INI_FILE, "w", encoding="utf-8") as f:
            cp.write(f)
    except Exception:
        pass
