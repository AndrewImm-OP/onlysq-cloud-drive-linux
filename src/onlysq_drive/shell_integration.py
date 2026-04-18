"""File-manager context menu integration.

Installs "Copy public link" entries for all major Linux file managers:
- Nautilus/GNOME Files  (~/.local/share/nautilus/scripts/)
- Nemo/Cinnamon         (~/.local/share/nemo/actions/)
- Dolphin/KDE           (~/.local/share/kio/servicemenus/)
- Thunar/XFCE           (~/.local/share/thunar/uca.xml — via Thunar Custom Actions)
- Caja/MATE             (~/.config/caja/scripts/)

All entries are created unconditionally; if the FM is not installed the files
are simply ignored by the system.
"""
from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

# --- Paths -------------------------------------------------------------------

NAUTILUS_SCRIPT_DIR = Path.home() / ".local" / "share" / "nautilus" / "scripts"
CAJA_SCRIPT_DIR = Path.home() / ".config" / "caja" / "scripts"
NEMO_ACTION_DIR = Path.home() / ".local" / "share" / "nemo" / "actions"
KDE_SERVICE_MENU_DIR = Path.home() / ".local" / "share" / "kio" / "servicemenus"

SCRIPT_NAME = "OnlySQ - Copy public link"
NEMO_ACTION_FILE = "onlysq-copy-link.nemo_action"
KDE_SERVICE_MENU_FILE = "onlysq-copy-link.desktop"


def _find_executable() -> str:
    """Find the onlysq-drive executable."""
    exe = shutil.which("onlysq-drive")
    if exe:
        return exe
    return str(Path(sys.argv[0]).resolve())


# --- Content generators ------------------------------------------------------

def _nautilus_script_content(executable: str) -> str:
    """Nautilus and Caja both use the same bash-script format."""
    return f"""#!/bin/bash
# OnlySQ Drive: copy public link for the selected file
while IFS= read -r file; do
    "{executable}" shell-copy-link "$file"
    break  # only first file
done <<< "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"
"""


def _nemo_action_content(executable: str) -> str:
    return f"""[Nemo Action]
Name=OnlySQ: Copy public link
Comment=Copy OnlySQ Cloud public link to clipboard
Exec={executable} shell-copy-link %F
Icon-Name=edit-copy
Selection=s
Extensions=any;
"""


def _kde_service_menu_content(executable: str) -> str:
    return f"""[Desktop Entry]
Type=Service
X-KDE-ServiceTypes=KonqPopupMenu/Plugin
MimeType=all/allfiles;
Actions=onlysq_copy_link

[Desktop Action onlysq_copy_link]
Name=OnlySQ: Copy public link
Icon=edit-copy
Exec={executable} shell-copy-link %f
"""


# --- Install / Uninstall -----------------------------------------------------

def _install_script(directory: Path, name: str, content: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _remove_file(directory: Path, name: str) -> None:
    path = directory / name
    if path.exists():
        path.unlink()


def install_copy_link_menu(executable: str | None = None) -> None:
    exe = executable or _find_executable()

    # Nautilus script
    _install_script(NAUTILUS_SCRIPT_DIR, SCRIPT_NAME, _nautilus_script_content(exe))

    # Caja script (same format as Nautilus)
    _install_script(CAJA_SCRIPT_DIR, SCRIPT_NAME, _nautilus_script_content(exe))

    # Nemo action
    NEMO_ACTION_DIR.mkdir(parents=True, exist_ok=True)
    (NEMO_ACTION_DIR / NEMO_ACTION_FILE).write_text(
        _nemo_action_content(exe), encoding="utf-8",
    )

    # KDE / Dolphin service menu
    KDE_SERVICE_MENU_DIR.mkdir(parents=True, exist_ok=True)
    (KDE_SERVICE_MENU_DIR / KDE_SERVICE_MENU_FILE).write_text(
        _kde_service_menu_content(exe), encoding="utf-8",
    )


def uninstall_copy_link_menu() -> None:
    _remove_file(NAUTILUS_SCRIPT_DIR, SCRIPT_NAME)
    _remove_file(CAJA_SCRIPT_DIR, SCRIPT_NAME)
    _remove_file(NEMO_ACTION_DIR, NEMO_ACTION_FILE)
    _remove_file(KDE_SERVICE_MENU_DIR, KDE_SERVICE_MENU_FILE)
