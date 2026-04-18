from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import config_path, ensure_base_dirs


def _default_mountpoint() -> str:
    """Pick a sensible default mountpoint.

    Prefers /run/media/$USER/OnlySQCloud (where udisks2 mounts real drives),
    falls back to ~/OnlySQCloud if /run/media/$USER does not exist.
    """
    user = os.environ.get("USER") or os.getlogin()
    media = f"/run/media/{user}"
    if os.path.isdir(media):
        return f"{media}/OnlySQCloud"
    return "~/OnlySQCloud"


@dataclass(slots=True)
class AppConfig:
    upload_url: str = "https://cloud.onlysq.ru/upload"
    file_base_url: str = "https://cloud.onlysq.ru/file"
    delete_base_url: str = "https://cloud.onlysq.ru/file"
    delete_method: str = "DELETE"
    delete_auth_header: str = "Authorization"
    request_timeout: int = 120
    chunk_size: int = 1024 * 1024
    mountpoint: str = ""
    volume_label: str = "OnlySQ Cloud"
    debug: bool = False

    def __post_init__(self) -> None:
        if not self.mountpoint:
            self.mountpoint = _default_mountpoint()

    @classmethod
    def load(cls) -> "AppConfig":
        ensure_base_dirs()
        path = config_path()
        if not path.exists():
            cfg = cls()
            cfg.save()
            return cfg
        data = json.loads(path.read_text(encoding="utf-8"))
        known: dict[str, Any] = {}
        for field_name in cls.__dataclass_fields__.keys():
            if field_name in data:
                known[field_name] = data[field_name]
        cfg = cls(**known)
        return cfg

    def save(self) -> Path:
        ensure_base_dirs()
        path = config_path()
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def set(self, key: str, value: Any) -> None:
        if key not in self.__dataclass_fields__:
            raise KeyError(f"Unknown config key: {key}")
        current = getattr(self, key)
        if isinstance(current, bool):
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                value = bool(value)
        elif isinstance(current, int):
            value = int(value)
        else:
            value = str(value)
        setattr(self, key, value)

    @property
    def mount_path(self) -> Path:
        """Return the absolute mountpoint directory.

        Uses expanduser() + absolute normalization without resolve(),
        because resolve() calls stat() which will hang if the path
        is already a stuck FUSE mount.
        """
        p = Path(self.mountpoint).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return Path(os.path.normpath(str(p)))
