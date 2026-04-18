from __future__ import annotations

import shutil
import subprocess


def copy_text(text: str) -> None:
    """Copy *text* to the system clipboard on Linux.

    Tries (in order): wl-copy (Wayland), xclip, xsel.
    Raises RuntimeError if none is available.
    """
    if shutil.which("wl-copy"):
        subprocess.run(
            ["wl-copy", "--", text],
            check=True,
            timeout=5,
        )
        return

    if shutil.which("xclip"):
        proc = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
        )
        proc.communicate(input=text.encode("utf-8"), timeout=5)
        if proc.returncode:
            raise RuntimeError(f"xclip exited with code {proc.returncode}")
        return

    if shutil.which("xsel"):
        proc = subprocess.Popen(
            ["xsel", "--clipboard", "--input"],
            stdin=subprocess.PIPE,
        )
        proc.communicate(input=text.encode("utf-8"), timeout=5)
        if proc.returncode:
            raise RuntimeError(f"xsel exited with code {proc.returncode}")
        return

    raise RuntimeError(
        "No clipboard tool found. Install one of: wl-copy (wl-clipboard), xclip, xsel"
    )
