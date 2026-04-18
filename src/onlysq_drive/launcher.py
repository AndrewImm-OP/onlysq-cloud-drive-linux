from __future__ import annotations

import datetime as dt
import sys
import traceback

from .config import AppConfig
from .mount import run_mount
from .paths import ensure_base_dirs
from .autostart import autostart_log_path


def _redirect_stdio() -> None:
    ensure_base_dirs()
    log_path = autostart_log_path()
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    if argv and argv[0] == "mount-hidden":
        _redirect_stdio()
        print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] onlysq-drive autostart launching")
        try:
            cfg = AppConfig.load()
            return run_mount(cfg)
        except Exception:
            traceback.print_exc()
            raise
    print("Usage: python -m onlysq_drive.launcher mount-hidden")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
