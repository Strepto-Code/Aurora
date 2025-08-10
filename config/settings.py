
from __future__ import annotations
import json, os, dataclasses
from dataclasses import dataclass
from typing import Optional, Literal, Dict, Any

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "AudioVis")
PRESET_DIR = os.path.join(CONFIG_DIR, "presets")
os.makedirs(PRESET_DIR, exist_ok=True)

AUDIO_STATE_FILE = os.path.join(CONFIG_DIR, 'audio.json')

def load_audio_state() -> Dict[str, Any]:
    try:
        with open(AUDIO_STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {'device_id': None, 'sample_rate': 48000}
        return {'device_id': data.get('device_id'), 'sample_rate': int(data.get('sample_rate', 48000))}
    except Exception:
        return {'device_id': None, 'sample_rate': 48000}

def save_audio_state(device_id: int|None, sample_rate: int|None):
    try:
        data = {'device_id': int(device_id) if device_id is not None else None,
                'sample_rate': int(sample_rate) if sample_rate else 48000}
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(AUDIO_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception:
        pass

@dataclass
class AudioConfig:
    device_id: Optional[int]
    sample_rate: int

@dataclass
class ExportConfig:
    width: int; height: int; fps: int
    codec: Literal["h264"] = "h264"
    bitrate_kbps: int = 12000
    alpha: bool = False
    format: Literal["mp4","png_sequence"] = "mp4"

@dataclass
class HotkeyConfig:
    start_stop: str = "Space"
    next_preset: str = "Ctrl+Right"
    prev_preset: str = "Ctrl+Left"
    screenshot: str = "Ctrl+S"
    toggle_safe_mode: str = "Ctrl+M"

@dataclass
class BackgroundConfig:
    path: Optional[str] = None
    scale_mode: Literal["fill","fit","stretch","center","tile"] = "fill"
    offset_x: int = 0; offset_y: int = 0
    dim_percent: int = 0  # 0-100

@dataclass
class ShadowConfig:
    enabled: bool = False
    opacity_percent: int = 50  # 0-100
    blur_radius: int = 8  # optional

@dataclass
class RadialFillConfig:
    enabled: bool = False
    color: str = "#80FFFFFF"  # RGBA hex
    blend: Literal["normal","add","multiply"] = "normal"
    threshold: float = 0.1  # 0.0-1.0

@dataclass
class AppState:
    version: int
    theme: str
    audio: AudioConfig
    visualizer: str
    params: Dict[str, Any]
    export: ExportConfig
    hotkeys: HotkeyConfig
    background: BackgroundConfig
    shadow: ShadowConfig
    radial_fill: RadialFillConfig

DEFAULT_STATE = AppState(
    version=1,
    theme="Neon Grid",
    audio=AudioConfig(device_id=None, sample_rate=48000),
    visualizer="Waveform - Linear",
    params={},
    export=ExportConfig(width=1920, height=1080, fps=60),
    hotkeys=HotkeyConfig(),
    background=BackgroundConfig(),
    shadow=ShadowConfig(),
    radial_fill=RadialFillConfig(),
)

def dataclass_to_dict(dc):
    return dataclasses.asdict(dc)

def dict_to_dataclass(cls, d):
    if isinstance(d, cls):
        return d
    # filter unknown keys
    fields = {f.name for f in dataclasses.fields(cls)}
    filtered = {k:v for k,v in (d or {}).items() if k in fields}
    return cls(**filtered)

def migrate_state(d: dict) -> dict:
    # Placeholder migration logic as versions evolve
    ver = int(d.get("version", 1))
    if ver < 1:
        d["version"] = 1
    return d

class PresetStore:
    def __init__(self, dir_path: str = PRESET_DIR):
        self.dir = dir_path
        os.makedirs(self.dir, exist_ok=True)

    def list_presets(self):
        out = []
        for name in os.listdir(self.dir):
            if name.endswith(".json"):
                out.append(name[:-5])
        return sorted(out)

    def save(self, name: str, state: AppState):
        path = os.path.join(self.dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataclass_to_dict(state), f, indent=2)

    def load(self, name: str) -> AppState:
        path = os.path.join(self.dir, f"{name}.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = migrate_state(data)
        return AppState(
            version=data.get("version", 1),
            theme=data.get("theme", DEFAULT_STATE.theme),
            audio=dict_to_dataclass(AudioConfig, data.get("audio", {})),
            visualizer=data.get("visualizer", DEFAULT_STATE.visualizer),
            params=data.get("params", {}),
            export=dict_to_dataclass(ExportConfig, data.get("export", {})),
            hotkeys=dict_to_dataclass(HotkeyConfig, data.get("hotkeys", {})),
            background=dict_to_dataclass(BackgroundConfig, data.get("background", {})),
            shadow=dict_to_dataclass(ShadowConfig, data.get("shadow", {})),
            radial_fill=dict_to_dataclass(RadialFillConfig, data.get("radial_fill", {})),
        )
