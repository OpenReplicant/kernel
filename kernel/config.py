"""The startup configuration (docs/CONFIG.md): config/kernel.yaml, or the file in $KERNEL_CONFIG.

load() returns the file's settings over the defaults below. `${python}` in a string is the
running interpreter and `${root}` the repository, so a config can name the repo's own tools.
"""
import copy
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "config" / "kernel.yaml"

DEFAULTS = {
    "limits": {"max_depth": 8, "max_fanout": 1000, "max_attempts": 5},
    "sandbox": {
        "enabled": True,
        "backend": "docker",
        "server": {"command": ["${python}", "-m", "sandbox.server"]},
        "engine": "docker",
        "images": {},
        "defaults": {"timeout_s": 10, "cpus": 1.0, "memory": "256m", "pids": 64, "max_output": 1 << 20},
        "max": {"timeout_s": 300, "cpus": 2.0, "memory": "1g", "pids": 256, "max_output": 8 << 20},
    },
    "agents": {"max_turns": 10, "timeout_s": 600},
}


def path() -> Path:
    return Path(os.environ.get("KERNEL_CONFIG") or DEFAULT_FILE)


def load(file: str | Path | None = None) -> dict:
    f = Path(file) if file else path()
    if not f.exists() and (file is not None or "KERNEL_CONFIG" in os.environ):
        raise FileNotFoundError(f"config file {f} not found")
    data = yaml.safe_load(f.read_text()) if f.exists() else {}
    cfg = _merge(copy.deepcopy(DEFAULTS), data or {})
    cfg["file"] = str(f)
    return _expand(cfg)


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        base[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


def _expand(v):
    if isinstance(v, str):
        return v.replace("${python}", sys.executable).replace("${root}", str(ROOT))
    if isinstance(v, list):
        return [_expand(x) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    return v
