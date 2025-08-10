
import json, os
from dataclasses import dataclass
from typing import Dict, Any

THEMES_DIR = os.path.join(os.path.dirname(__file__), "..", "themes")

@dataclass
class Theme:
    name: str
    palette: Dict[str, str]
    defaults: Dict[str, Any]  # visual defaults: gradientA/B, radial_fill, etc.

def load_themes():
    out = {}
    for fn in os.listdir(THEMES_DIR):
        if fn.endswith(".json"):
            path = os.path.join(THEMES_DIR, fn)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            t = Theme(name=data["name"], palette=data.get("palette", {}), defaults=data.get("defaults", {}))
            out[t.name] = t
    return out
