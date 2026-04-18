from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from .paths import ensure_base_dirs, logs_dir

DEFAULT_TASK_NAME = "OnlySQ Drive"
SERVICE_NAME = "onlysq-drive"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_FILE = SYSTEMD_USER_DIR / f"{SERVICE_NAME}.service"

_DEVNULL = subprocess.DEVNULL


def _python_executable() -> str:
    return sys.executable


def _service_unit_content() -> str:
    python = _python_executable()
    return textwrap.dedent(f"""\
        [Unit]
        Description=OnlySQ Cloud Drive (FUSE mount)
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={python} -m onlysq_drive.launcher mount-hidden
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=default.target
    """)


def _systemctl(*args: str, quiet: bool = False, must_succeed: bool = False) -> int:
    """Run a systemctl --user command.

    quiet=True  suppresses both stdout and stderr (used for stop/disable
                where the service may already be gone).
    must_succeed=True  raises on non-zero exit.
    """
    cmd = ["systemctl", "--user", *args]
    stdout = _DEVNULL if quiet else None
    stderr = _DEVNULL if quiet else None
    result = subprocess.run(cmd, stdout=stdout, stderr=stderr)
    if must_succeed and result.returncode != 0:
        raise RuntimeError(f"Command failed (exit {result.returncode}): {' '.join(cmd)}")
    return result.returncode


def install_autostart_task(task_name: str = DEFAULT_TASK_NAME) -> None:
    """Create and enable a systemd user service for autostart."""
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE_FILE.write_text(_service_unit_content(), encoding="utf-8")
    _systemctl("daemon-reload", must_succeed=True)
    _systemctl("enable", SERVICE_NAME, must_succeed=True)
    _systemctl("start", SERVICE_NAME, quiet=True)


def uninstall_autostart_task(task_name: str = DEFAULT_TASK_NAME) -> None:
    """Disable and remove the systemd user service."""
    _systemctl("stop", SERVICE_NAME, quiet=True)
    _systemctl("disable", SERVICE_NAME, quiet=True)
    if SERVICE_FILE.exists():
        SERVICE_FILE.unlink()
    _systemctl("daemon-reload", quiet=True)


def is_autostart_task_installed(task_name: str = DEFAULT_TASK_NAME) -> bool:
    """Check whether the systemd user service is enabled."""
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", SERVICE_NAME],
        capture_output=True,
    )
    return result.returncode == 0


def autostart_log_path() -> Path:
    ensure_base_dirs()
    return logs_dir() / "autostart.log"


def write_launch_log(message: str) -> None:
    ensure_base_dirs()
    path = autostart_log_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(message)
        if not message.endswith("\n"):
            f.write("\n")
