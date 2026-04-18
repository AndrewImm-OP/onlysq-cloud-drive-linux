"""File-manager sidebar integration.

Ensures that the OnlySQ Cloud mount appears in the sidebar of all
major Linux file managers:

- **KDE Dolphin**: Handled automatically via ``subtype=rclone`` in FUSE
  mount options (mount.py).  KDE Solid recognises ``fuse.rclone`` and
  shows it in "Network" section of Dolphin's sidebar.

- **GTK file managers** (Nautilus, Thunar, Nemo, Caja, PCManFM):
  Handled via ``x-gvfs-show`` FUSE mount option (mount.py) — makes the
  mount appear in the "Devices" section.  We also add a GTK bookmark
  as a fallback for file managers that don't honour x-gvfs-show.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote as urlquote


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mountpoint_uri(mountpoint: str) -> str:
    """Return a ``file:///`` URI for *mountpoint* (handles non-ASCII)."""
    mp = os.path.normpath(os.path.expanduser(mountpoint))
    return "file://" + urlquote(mp, safe="/")


# ---------------------------------------------------------------------------
# GTK bookmarks  (~/.config/gtk-3.0/bookmarks)
# Supported by: Nautilus, Thunar, Nemo, Caja, PCManFM
# Format: one line per entry  "file:///path Label"
# ---------------------------------------------------------------------------

_GTK_BOOKMARKS = Path.home() / ".config" / "gtk-3.0" / "bookmarks"


def _read_gtk_bookmarks() -> list[str]:
    if not _GTK_BOOKMARKS.exists():
        return []
    return _GTK_BOOKMARKS.read_text(encoding="utf-8").splitlines()


def _write_gtk_bookmarks(lines: list[str]) -> None:
    _GTK_BOOKMARKS.parent.mkdir(parents=True, exist_ok=True)
    _GTK_BOOKMARKS.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _install_gtk_bookmark(mountpoint: str, label: str = "OnlySQ Cloud") -> None:
    uri = _mountpoint_uri(mountpoint)
    entry = f"{uri} {label}"
    lines = _read_gtk_bookmarks()
    # Remove any existing OnlySQ entry (by URI prefix match)
    lines = [ln for ln in lines if not ln.startswith(uri)]
    lines.append(entry)
    _write_gtk_bookmarks(lines)


def _uninstall_gtk_bookmark(mountpoint: str) -> None:
    uri = _mountpoint_uri(mountpoint)
    lines = _read_gtk_bookmarks()
    filtered = [ln for ln in lines if not ln.startswith(uri)]
    if len(filtered) != len(lines):
        _write_gtk_bookmarks(filtered)


# ---------------------------------------------------------------------------
# KDE user-places.xbel cleanup
# Remove any previously-added xbel bookmark (from earlier versions).
# ---------------------------------------------------------------------------

_KDE_PLACES = Path.home() / ".local" / "share" / "user-places.xbel"
_BOOKMARK_ID_PREFIX = "onlysq-drive/"


def _cleanup_kde_places(mountpoint: str) -> None:
    """Remove any OnlySQ bookmark that was previously added to xbel.

    Dolphin sidebar is now handled by the ``fuse.rclone`` subtype, so
    the xbel bookmark is no longer needed and would cause a duplicate.
    """
    if not _KDE_PLACES.exists():
        return
    try:
        import xml.etree.ElementTree as ET
        ET.register_namespace("bookmark", "http://www.freedesktop.org/standards/desktop-bookmarks")
        ET.register_namespace("kdepriv", "http://www.kde.org/kdepriv")
        ET.register_namespace("mime", "http://www.freedesktop.org/standards/shared-mime-info")

        tree = ET.parse(_KDE_PLACES)
        root = tree.getroot()
        uri = _mountpoint_uri(mountpoint)

        to_remove = []
        for bm in root.findall("bookmark"):
            href = bm.get("href", "")
            if href == uri:
                to_remove.append(bm)
                continue
            for meta in bm.iter("ID"):
                if meta.text and meta.text.startswith(_BOOKMARK_ID_PREFIX):
                    to_remove.append(bm)
                    break

        if not to_remove:
            return

        for bm in to_remove:
            root.remove(bm)

        tree.write(str(_KDE_PLACES), encoding="unicode", xml_declaration=True)

        # Re-add DOCTYPE that ElementTree drops
        content = _KDE_PLACES.read_text(encoding="utf-8")
        if "<!DOCTYPE xbel>" not in content:
            content = content.replace("?>", '?>\n<!DOCTYPE xbel>', 1)
        if not content.endswith("\n"):
            content += "\n"
        _KDE_PLACES.write_text(content, encoding="utf-8")
    except Exception:
        pass  # Don't break setup over xbel cleanup


# ---------------------------------------------------------------------------
# Public API  (used by cli.py setup / purge)
# ---------------------------------------------------------------------------

def install_sidebar_entry(mountpoint: str, label: str = "OnlySQ Cloud") -> None:
    """Add OnlySQ Cloud to sidebar of all supported file managers."""
    _install_gtk_bookmark(mountpoint, label)
    _cleanup_kde_places(mountpoint)  # remove stale xbel if any


def uninstall_sidebar_entry(mountpoint: str) -> None:
    """Remove OnlySQ Cloud from sidebar of all supported file managers."""
    _uninstall_gtk_bookmark(mountpoint)
    _cleanup_kde_places(mountpoint)
