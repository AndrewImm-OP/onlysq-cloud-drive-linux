from __future__ import annotations

import logging

from .config import AppConfig

# Linux does not have a concept of per-drive-letter icons in a file manager.
# These functions are no-ops to keep the CLI interface consistent.


def install_drive_icon(icon_path: str, label: str | None = None, *, config: AppConfig | None = None) -> None:
    logging.info(
        "install_drive_icon: no-op on Linux (icon_path=%s, label=%s)",
        icon_path, label,
    )


def uninstall_drive_icon(*, config: AppConfig | None = None) -> None:
    logging.info("uninstall_drive_icon: no-op on Linux")
