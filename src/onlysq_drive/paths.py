from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "onlysq-drive"


def config_dir() -> Path:
    """XDG_CONFIG_HOME / onlysq-drive  (~/.config/onlysq-drive)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def data_dir() -> Path:
    """XDG_DATA_HOME / onlysq-drive  (~/.local/share/onlysq-drive)."""
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def cache_root() -> Path:
    """XDG_CACHE_HOME / onlysq-drive  (~/.cache/onlysq-drive)."""
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".cache" / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "index.sqlite3"


def cache_dir() -> Path:
    return cache_root() / "files"


def logs_dir() -> Path:
    return data_dir() / "logs"


def ensure_base_dirs() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    data_dir().mkdir(parents=True, exist_ok=True)
    cache_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
