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
    if not cp.has_section(section):
        return default
    if not cp.has_option(section, key):
        return default
    return cp.get(section, key, fallback=default)


def _get_int(cp: configparser.ConfigParser, section: str, key: str, default: int) -> int:
    try:
        return int(_get(cp, section, key, str(default)))
    except Exception:
        return int(default)


def _get_float(cp: configparser.ConfigParser, section: str, key: str, default: float) -> float:
    try:
        return float(_get(cp, section, key, str(default)))
    except Exception:
        return float(default)


def _get_bool(cp: configparser.ConfigParser, section: str, key: str, default: bool) -> bool:
    v = str(_get(cp, section, key, "1" if default else "0")).strip().lower()
    return v in {"1", "true", "yes", "on"}


def load_state_ini() -> Dict[str, Any]:
    """Load persisted UI state from an INI file.

    Returns a flat dict of typed values. Missing keys are omitted.
    """
    cp = configparser.ConfigParser()
    if not os.path.exists(STATE_INI_FILE):
        return {}
    try:
        cp.read(STATE_INI_FILE, encoding="utf-8")
    except Exception:
        return {}

    st: Dict[str, Any] = {}

    st["mode"] = str(_get(cp, "ui", "mode", ""))
    st["realtime_fps"] = _get_int(cp, "ui", "realtime_fps", 60)
    st["sensitivity"] = _get_float(cp, "ui", "sensitivity", 1.0)
    st["volume"] = _get_float(cp, "ui", "volume", 1.0)
    st["output_device_index"] = _get_int(cp, "ui", "output_device_index", -1)
    st["color"] = str(_get(cp, "ui", "color", ""))

    st["radial_rotation_deg"] = _get_float(cp, "radial", "rotation_deg", 0.0)
    st["radial_mirror"] = _get_bool(cp, "radial", "mirror", True)
    st["radial_smooth_amount"] = _get_int(cp, "radial", "smooth_amount", 50)
    st["center_motion"] = _get_int(cp, "radial", "center_motion", 0)
    st["center_image_zoom"] = _get_int(cp, "radial", "center_image_zoom", 100)
    st["edge_waviness"] = _get_int(cp, "radial", "edge_waviness", 30)
    st["feather_audio_enabled"] = _get_bool(cp, "radial", "feather_audio_enabled", False)
    st["feather_audio_amount"] = _get_int(cp, "radial", "feather_audio_amount", 40)
    st["center_image_path"] = str(_get(cp, "radial", "center_image_path", ""))
    st["radial_waveform_smoothness"] = _get_int(cp, "radial", "radial_waveform_smoothness", 50)
    st["radial_temporal_smoothing"] = _get_int(cp, "radial", "radial_temporal_smoothing", 30)

    st["export_width"] = _get_int(cp, "export", "width", 1280)
    st["export_height"] = _get_int(cp, "export", "height", 720)
    st["export_fps"] = _get_int(cp, "export", "fps", 60)
    st["export_gpu"] = _get_bool(cp, "export", "gpu", False)
    st["export_gpu_device"] = str(_get(cp, "export", "gpu_device", ""))

    st["bg_path"] = str(_get(cp, "fx_background", "path", ""))
    st["bg_scale_mode"] = str(_get(cp, "fx_background", "scale_mode", "fill"))
    st["bg_offset_x"] = _get_int(cp, "fx_background", "offset_x", 0)
    st["bg_offset_y"] = _get_int(cp, "fx_background", "offset_y", 0)
    st["bg_dim_percent"] = _get_int(cp, "fx_background", "dim_percent", 0)

    st["grad_a"] = str(_get(cp, "gradient", "a", ""))
    st["grad_b"] = str(_get(cp, "gradient", "b", ""))
    st["grad_curve"] = str(_get(cp, "gradient", "curve", "linear"))
    st["grad_min"] = _get_float(cp, "gradient", "clamp_min", 0.0)
    st["grad_max"] = _get_float(cp, "gradient", "clamp_max", 1.0)
    st["grad_smoothing"] = _get_float(cp, "gradient", "smoothing", 0.2)

    st["shadow_enabled"] = _get_bool(cp, "fx_shadow", "enabled", False)
    st["shadow_opacity"] = _get_int(cp, "fx_shadow", "opacity", 50)
    st["shadow_blur_radius"] = _get_int(cp, "fx_shadow", "blur_radius", 16)
    st["shadow_distance"] = _get_int(cp, "fx_shadow", "distance", 8)
    st["shadow_angle_deg"] = _get_int(cp, "fx_shadow", "angle_deg", 45)
    st["shadow_spread"] = _get_int(cp, "fx_shadow", "spread", 6)

    st["glow_enabled"] = _get_bool(cp, "fx_glow", "enabled", False)
    st["glow_color"] = str(_get(cp, "fx_glow", "color", ""))
    st["glow_radius"] = _get_int(cp, "fx_glow", "radius", 22)
    st["glow_strength"] = _get_int(cp, "fx_glow", "strength", 80)

    st["fill_enabled"] = _get_bool(cp, "fx_radial_fill", "enabled", False)
    st["fill_color"] = str(_get(cp, "fx_radial_fill", "color", ""))
    st["fill_blend"] = str(_get(cp, "fx_radial_fill", "blend", "normal"))
    st["fill_threshold"] = _get_float(cp, "fx_radial_fill", "threshold", 0.1)

    st["fx_tab"] = _get_int(cp, "ui", "fx_tab", 0)

    return st


def save_state_ini(state: Dict[str, Any]) -> None:
    _ensure_dir()
    cp = configparser.ConfigParser()

    def setv(section: str, key: str, value: Any) -> None:
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, key, str(value))

    setv("ui", "mode", state.get("mode", ""))
    setv("ui", "realtime_fps", int(state.get("realtime_fps", 60)))
    setv("ui", "sensitivity", float(state.get("sensitivity", 1.0)))
    setv("ui", "volume", float(state.get("volume", 1.0)))
    odi = state.get("output_device_index", -1)
    setv("ui", "output_device_index", int(odi) if odi is not None else -1)
    setv("ui", "color", state.get("color", ""))
    setv("ui", "fx_tab", int(state.get("fx_tab", 0)))

    setv("radial", "rotation_deg", float(state.get("radial_rotation_deg", 0.0)))
    setv("radial", "mirror", 1 if bool(state.get("radial_mirror", True)) else 0)
    setv("radial", "smooth_amount", int(state.get("radial_smooth_amount", 50)))
    setv("radial", "center_motion", int(state.get("center_motion", 0)))
    setv("radial", "center_image_zoom", int(state.get("center_image_zoom", 100)))
    setv("radial", "edge_waviness", int(state.get("edge_waviness", 30)))
    setv("radial", "feather_audio_enabled", 1 if bool(state.get("feather_audio_enabled", False)) else 0)
    setv("radial", "feather_audio_amount", int(state.get("feather_audio_amount", 40)))
    setv("radial", "center_image_path", state.get("center_image_path", ""))
    setv("radial", "radial_waveform_smoothness", int(state.get("radial_waveform_smoothness", 50)))
    setv("radial", "radial_temporal_smoothing", int(state.get("radial_temporal_smoothing", 30)))

    setv("export", "width", int(state.get("export_width", 1280)))
    setv("export", "height", int(state.get("export_height", 720)))
    setv("export", "fps", int(state.get("export_fps", 60)))
    setv("export", "gpu", 1 if bool(state.get("export_gpu", False)) else 0)
    setv("export", "gpu_device", state.get("export_gpu_device", ""))

    setv("fx_background", "path", state.get("bg_path", ""))
    setv("fx_background", "scale_mode", state.get("bg_scale_mode", "fill"))
    setv("fx_background", "offset_x", int(state.get("bg_offset_x", 0)))
    setv("fx_background", "offset_y", int(state.get("bg_offset_y", 0)))
    setv("fx_background", "dim_percent", int(state.get("bg_dim_percent", 0)))

    setv("gradient", "a", state.get("grad_a", ""))
    setv("gradient", "b", state.get("grad_b", ""))
    setv("gradient", "curve", state.get("grad_curve", "linear"))
    setv("gradient", "clamp_min", float(state.get("grad_min", 0.0)))
    setv("gradient", "clamp_max", float(state.get("grad_max", 1.0)))
    setv("gradient", "smoothing", float(state.get("grad_smoothing", 0.2)))

    setv("fx_shadow", "enabled", 1 if bool(state.get("shadow_enabled", False)) else 0)
    setv("fx_shadow", "opacity", int(state.get("shadow_opacity", 50)))
    setv("fx_shadow", "blur_radius", int(state.get("shadow_blur_radius", 16)))
    setv("fx_shadow", "distance", int(state.get("shadow_distance", 8)))
    setv("fx_shadow", "angle_deg", int(state.get("shadow_angle_deg", 45)))
    setv("fx_shadow", "spread", int(state.get("shadow_spread", 6)))

    setv("fx_glow", "enabled", 1 if bool(state.get("glow_enabled", False)) else 0)
    setv("fx_glow", "color", state.get("glow_color", ""))
    setv("fx_glow", "radius", int(state.get("glow_radius", 22)))
    setv("fx_glow", "strength", int(state.get("glow_strength", 80)))

    setv("fx_radial_fill", "enabled", 1 if bool(state.get("fill_enabled", False)) else 0)
    setv("fx_radial_fill", "color", state.get("fill_color", ""))
    setv("fx_radial_fill", "blend", state.get("fill_blend", "normal"))
    setv("fx_radial_fill", "threshold", float(state.get("fill_threshold", 0.1)))

    try:
        with open(STATE_INI_FILE, "w", encoding="utf-8") as f:
            cp.write(f)
    except Exception:
        pass
