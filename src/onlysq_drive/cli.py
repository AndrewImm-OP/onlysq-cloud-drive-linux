from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .autostart import DEFAULT_TASK_NAME, autostart_log_path, install_autostart_task, uninstall_autostart_task
from .clipboard import copy_text
from .cloud_client import CloudClient
from .config import AppConfig
from .drive_icon import install_drive_icon, uninstall_drive_icon
from .index_db import IndexDB
from .mount import run_mount, _is_mountpoint_busy, _try_unmount_stale
from .paths import cache_dir, config_dir, config_path, data_dir, db_path, ensure_base_dirs, logs_dir
from .shell_integration import install_copy_link_menu, uninstall_copy_link_menu
from .sidebar import install_sidebar_entry, uninstall_sidebar_entry
from .vpaths import normalize_virtual_path


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="onlysq-drive")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Create config and local data directories")
    init_p.add_argument("--mount", default=None, help="Mountpoint directory, e.g. ~/OnlySQCloud")
    init_p.add_argument("--label", default=None, help="Volume label")

    sub.add_parser("doctor", help="Check local prerequisites")
    sub.add_parser("mount", help="Mount the configured FUSE filesystem")
    sub.add_parser("stats", help="Show local index statistics")

    ls_p = sub.add_parser("ls", help="List indexed files")
    ls_p.add_argument("path", nargs="?", default="/", help="Virtual path")

    info_p = sub.add_parser("info", help="Show details for one file or folder")
    info_p.add_argument("path", help="Virtual path")

    copy_p = sub.add_parser("copy-link", help="Copy the public URL of a file to clipboard")
    copy_p.add_argument("path", help="Virtual path")

    shell_copy = sub.add_parser("shell-copy-link", help=argparse.SUPPRESS)
    shell_copy.add_argument("path")

    pull_p = sub.add_parser("pull", help="Download one indexed file to a local destination")
    pull_p.add_argument("path", help="Virtual path")
    pull_p.add_argument("destination", help="Local destination file")

    rm_p = sub.add_parser("rm", help="Delete one indexed file or empty directory")
    rm_p.add_argument("path", help="Virtual path")

    cfg_p = sub.add_parser("config", help="Show or change configuration")
    cfg_sub = cfg_p.add_subparsers(dest="config_command", required=True)
    cfg_sub.add_parser("show", help="Print current config as JSON")
    cfg_set = cfg_sub.add_parser("set", help="Set one config key")
    cfg_set.add_argument("key")
    cfg_set.add_argument("value")

    ctx_install = sub.add_parser("install-context-menu", help="Add file-manager context menu entry (Dolphin/Nautilus/Nemo/Caja)")
    ctx_install.add_argument("--exe", default=None, help="Path to onlysq-drive if auto-detect is wrong")
    sub.add_parser("uninstall-context-menu", help="Remove file-manager context menu entry")

    purge = sub.add_parser("purge", help="Delete local config/index/cache")
    purge.add_argument("--yes", action="store_true", help="Do not ask for confirmation")

    autostart_install = sub.add_parser("install-autostart", help="Create a systemd user service for autostart")
    autostart_install.add_argument("--task-name", default=DEFAULT_TASK_NAME, help="Service display name (informational)")

    autostart_uninstall = sub.add_parser("uninstall-autostart", help="Remove the autostart service")
    autostart_uninstall.add_argument("--task-name", default=DEFAULT_TASK_NAME, help="Service display name (informational)")

    icon_install = sub.add_parser("install-drive-icon", help="(No-op on Linux) Assign a custom icon")
    icon_install.add_argument("icon_path", help="Path to icon file")
    icon_install.add_argument("--label", default=None, help="Optional custom label")

    sub.add_parser("uninstall-drive-icon", help="(No-op on Linux) Remove the custom icon")

    setup = sub.add_parser("setup", help="One-time setup: init, context menu, autostart")
    setup.add_argument("--mount", default=None, help="Mountpoint directory, e.g. ~/OnlySQCloud")
    setup.add_argument("--label", default=None, help="Volume label")
    setup.add_argument("--icon", dest="icon_path", default=None, help="(Ignored on Linux) Path to icon file")
    setup.add_argument("--task-name", default=DEFAULT_TASK_NAME, help="Service display name")

    bootstrap = sub.add_parser("bootstrap", help="Try to install libfuse3-dev and pyfuse3 via system package manager")
    bootstrap.add_argument("--non-interactive", action="store_true")

    return parser.parse_args(argv)


def _load_config() -> AppConfig:
    ensure_base_dirs()
    return AppConfig.load()


def _resolve_virtual_path(raw_path: str, config: AppConfig) -> str:
    """Convert a user-supplied path to a normalized virtual path.

    If the path starts with the configured mountpoint directory,
    strip that prefix and normalize the rest.
    """
    path = str(raw_path).strip().strip('"')
    mount_str = str(config.mount_path)
    if path.startswith(mount_str):
        path = path[len(mount_str):]
    return normalize_virtual_path(path)


def _ensure_mountpoint(mountpoint: str) -> None:
    """Create the mountpoint directory, using sudo if needed (interactive).

    Handles several tricky situations:
    1. /run/media/$USER owned by root with restrictive ACL (udisks2) — needs sudo.
    2. Stale FUSE mount — os.path.isdir() returns False and stat/mkdir hang or
       fail with EACCES because the dead FUSE process no longer answers.
       We escalate: fusermount3 -u → fusermount3 -uz → sudo umount -l.
    3. Normal case (~/OnlySQCloud) — os.makedirs() just works.
    """
    import time

    mp = os.path.normpath(os.path.expanduser(mountpoint))
    if os.path.isdir(mp):
        return

    # If the path is listed in /proc/mounts it is a (possibly stale) mount.
    if _is_mountpoint_busy(mp):
        print(f"Stale mount detected at {mp}, cleaning up...")
        if _try_unmount_stale(mp):
            # Lazy unmount may need a brief moment to release the inode.
            time.sleep(0.3)
        if os.path.isdir(mp):
            return

    # Fast path: user can create it directly (e.g. ~/OnlySQCloud)
    try:
        os.makedirs(mp, exist_ok=True)
        return
    except FileExistsError:
        # Inode still exists (dead mount entry).  Try unmount, then
        # remove the dead directory entry and recreate.
        _try_unmount_stale(mp)
        time.sleep(0.3)
        if os.path.isdir(mp):
            return
        # Last resort: sudo remove the stale entry and recreate.
        subprocess.call(f'sudo rm -d "{mp}" 2>/dev/null; sudo mkdir -p "{mp}" && sudo chown {os.getuid()}:{os.getgid()} "{mp}"', shell=True)
        if os.path.isdir(mp):
            return
    except PermissionError:
        pass

    # Need sudo to create the directory (e.g. inside /run/media/$USER/).
    uid = os.getuid()
    gid = os.getgid()
    print(f"Creating {mp} (requires sudo)...")
    rc = subprocess.call(
        f'sudo mkdir -p "{mp}" && sudo chown {uid}:{gid} "{mp}"',
        shell=True,
    )
    if rc != 0:
        raise SystemExit(
            f"\nCannot create {mp} — sudo failed.\n"
            f"Create it manually:\n"
            f'  sudo mkdir -p "{mp}" && sudo chown {uid}:{gid} "{mp}"\n'
            f"Then re-run setup."
        )


def cmd_init(args: argparse.Namespace) -> int:
    cfg = _load_config()
    changed = False
    if args.mount:
        cfg.mountpoint = args.mount
        changed = True
    if args.label:
        cfg.volume_label = args.label
        changed = True
    if changed:
        cfg.save()
    _ensure_mountpoint(cfg.mountpoint)
    IndexDB().close()
    print(f"Config: {config_path()}")
    print(f"Index DB: {db_path()}")
    print(f"Cache dir: {cache_dir()}")
    print(f"Mountpoint: {os.path.normpath(os.path.expanduser(cfg.mountpoint))}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _load_config()
    pyver = sys.version.split()[0]
    print(f"Python: {pyver}")
    print(f"Platform: {sys.platform}")
    print(f"Config: {config_path()}")
    print(f"Mountpoint: {cfg.mount_path}")
    fuse_ok = False
    try:
        import pyfuse3
        fuse_ok = True
    except Exception as exc:
        print(f"pyfuse3 import: FAIL ({exc})")
    else:
        print("pyfuse3 import: OK")
    # Check libfuse3
    fuse3_present = shutil.which("fusermount3") is not None
    print(f"fusermount3 available: {'YES' if fuse3_present else 'NO'}")
    if not fuse_ok or not fuse3_present:
        print("Hint: run `onlysq-drive bootstrap` or install libfuse3-dev and pyfuse3 manually.")
    return 0


def cmd_mount(args: argparse.Namespace) -> int:
    cfg = _load_config()
    return run_mount(cfg)


def cmd_stats(args: argparse.Namespace) -> int:
    db = IndexDB()
    try:
        entries = list(db.iter_entries())
        files = [e for e in entries if e.kind == "file"]
        dirs = [e for e in entries if e.kind == "dir"]
        dirty = [e for e in files if e.dirty]
        print(f"Files: {len(files)}")
        print(f"Directories: {max(0, len(dirs) - 1)}")
        print(f"Total size: {db.total_file_size()} bytes")
        print(f"Dirty files: {len(dirty)}")
    finally:
        db.close()
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    cfg = _load_config()
    db = IndexDB()
    try:
        target = _resolve_virtual_path(args.path, cfg)
        for item in db.list_children(target):
            marker = "d" if item.kind == "dir" else "f"
            print(f"{marker}\t{item.size}\t{item.path}")
    finally:
        db.close()
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    cfg = _load_config()
    db = IndexDB()
    try:
        target = _resolve_virtual_path(args.path, cfg)
        item = db.get_entry(target)
        if not item:
            raise SystemExit(f"Not found in local index: {target}")
        from dataclasses import asdict
        print(json.dumps(asdict(item), indent=2, ensure_ascii=False))
    finally:
        db.close()
    return 0


def cmd_copy_link(args: argparse.Namespace) -> int:
    cfg = _load_config()
    db = IndexDB()
    try:
        target = _resolve_virtual_path(args.path, cfg)
        item = db.get_entry(target)
        if not item:
            raise SystemExit(f"Not found in local index: {target}")
        if item.kind != "file":
            raise SystemExit("Only files have public links")
        if not item.public_url:
            raise SystemExit("File has no public URL yet. Save/sync it first.")
        copy_text(item.public_url)
        print(item.public_url)
    finally:
        db.close()
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    cfg = _load_config()
    db = IndexDB()
    try:
        target = _resolve_virtual_path(args.path, cfg)
        item = db.get_entry(target)
        if not item:
            raise SystemExit(f"Not found in local index: {target}")
        if item.kind != "file":
            raise SystemExit("Only files can be pulled")
        destination = Path(args.destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        cache_path = db.get_cache_abs_path(item.cache_relpath) if item.cache_relpath else None
        if cache_path and cache_path.exists():
            shutil.copy2(cache_path, destination)
        elif item.remote_id:
            CloudClient(cfg).download(item.remote_id, destination)
        else:
            raise SystemExit("File exists in index but has neither cache nor remote id")
        print(f"Saved to {destination}")
    finally:
        db.close()
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    cfg = _load_config()
    db = IndexDB()
    try:
        target = _resolve_virtual_path(args.path, cfg)
        item = db.get_entry(target)
        if not item:
            raise SystemExit(f"Not found in local index: {target}")
        if item.kind == "dir":
            children = db.list_children(target)
            if children:
                raise SystemExit("Directory is not empty")
            db.delete_path(target)
            print(f"Deleted directory {target}")
            return 0
        if item.remote_id and item.owner_key:
            CloudClient(cfg).delete(item.remote_id, item.owner_key)
        if item.cache_relpath:
            db.get_cache_abs_path(item.cache_relpath).unlink(missing_ok=True)
        db.delete_path(target)
        print(f"Deleted file {target}")
        return 0
    finally:
        db.close()


def cmd_config(args: argparse.Namespace) -> int:
    cfg = _load_config()
    if args.config_command == "show":
        from dataclasses import asdict
        print(json.dumps(asdict(cfg), indent=2, ensure_ascii=False))
        return 0
    if args.config_command == "set":
        cfg.set(args.key, args.value)
        cfg.save()
        print(f"Updated {args.key}")
        return 0
    raise SystemExit("Unknown config command")


def cmd_install_context_menu(args: argparse.Namespace) -> int:
    install_copy_link_menu(args.exe)
    print("Installed file-manager context menu entry (Dolphin/Nautilus/Nemo/Caja).")
    return 0


def cmd_uninstall_context_menu(args: argparse.Namespace) -> int:
    uninstall_copy_link_menu()
    print("Removed file-manager context menu entry.")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    if not args.yes:
        raise SystemExit("Refusing to purge without --yes")
    try:
        cfg = _load_config()
        uninstall_sidebar_entry(cfg.mountpoint)
    except Exception:
        pass
    try:
        uninstall_copy_link_menu()
    except Exception:
        pass
    try:
        uninstall_autostart_task(DEFAULT_TASK_NAME)
    except Exception:
        pass
    for d in [config_dir(), data_dir()]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    from .paths import cache_root
    cr = cache_root()
    if cr.exists():
        shutil.rmtree(cr, ignore_errors=True)
    print("Removed local config/index/cache.")
    return 0


def cmd_install_autostart(args: argparse.Namespace) -> int:
    install_autostart_task(args.task_name)
    print(f"Installed systemd user service: onlysq-drive")
    print(f"Autostart log: {autostart_log_path()}")
    return 0


def cmd_uninstall_autostart(args: argparse.Namespace) -> int:
    uninstall_autostart_task(args.task_name)
    print("Removed systemd user service: onlysq-drive")
    return 0


def cmd_install_drive_icon(args: argparse.Namespace) -> int:
    cfg = _load_config()
    install_drive_icon(args.icon_path, args.label, config=cfg)
    print("Note: drive icons are not supported on Linux (no-op).")
    return 0


def cmd_uninstall_drive_icon(args: argparse.Namespace) -> int:
    cfg = _load_config()
    uninstall_drive_icon(config=cfg)
    print("Note: drive icons are not supported on Linux (no-op).")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = _load_config()
    changed = False
    if args.mount:
        cfg.mountpoint = args.mount
        changed = True
    if args.label:
        cfg.volume_label = args.label
        changed = True
    if changed:
        cfg.save()
    _ensure_mountpoint(cfg.mountpoint)
    install_copy_link_menu(None)
    install_sidebar_entry(cfg.mountpoint, cfg.volume_label)
    if args.icon_path:
        install_drive_icon(args.icon_path, args.label, config=cfg)
    install_autostart_task(args.task_name)
    mountpoint = cfg.mount_path
    print(f"Setup complete for {mountpoint} ({cfg.volume_label}).")
    print("Features enabled: context menu, sidebar bookmark, systemd autostart")
    print(f"Autostart log: {autostart_log_path()}")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Install FUSE3 development libraries and pyfuse3 via system package manager."""
    # Try apt (Debian/Ubuntu)
    if shutil.which("apt-get"):
        cmd = ["sudo", "apt-get", "install", "-y", "libfuse3-dev", "fuse3", "pkg-config"]
        print("Running:", " ".join(cmd))
        rc = subprocess.call(cmd)
        if rc != 0:
            return rc
        print("\nInstalling pyfuse3 Python package...")
        return subprocess.call([sys.executable, "-m", "pip", "install", "pyfuse3", "trio"])

    # Try dnf (Fedora/RHEL)
    if shutil.which("dnf"):
        cmd = ["sudo", "dnf", "install", "-y", "fuse3-devel", "fuse3", "pkgconf-pkg-config"]
        print("Running:", " ".join(cmd))
        rc = subprocess.call(cmd)
        if rc != 0:
            return rc
        print("\nInstalling pyfuse3 Python package...")
        return subprocess.call([sys.executable, "-m", "pip", "install", "pyfuse3", "trio"])

    # Try pacman (Arch)
    if shutil.which("pacman"):
        cmd = ["sudo", "pacman", "-S", "--noconfirm", "fuse3", "pkgconf"]
        print("Running:", " ".join(cmd))
        rc = subprocess.call(cmd)
        if rc != 0:
            return rc
        print("\nInstalling pyfuse3 Python package...")
        return subprocess.call([sys.executable, "-m", "pip", "install", "pyfuse3", "trio"])

    raise SystemExit(
        "Could not detect a supported package manager (apt-get, dnf, pacman).\n"
        "Install libfuse3-dev (or fuse3-devel) and pyfuse3 manually:\n"
        "  pip install pyfuse3 trio"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command
    if command == "init":
        return cmd_init(args)
    if command == "doctor":
        return cmd_doctor(args)
    if command == "mount":
        return cmd_mount(args)
    if command == "stats":
        return cmd_stats(args)
    if command == "ls":
        return cmd_ls(args)
    if command == "info":
        return cmd_info(args)
    if command in {"copy-link", "shell-copy-link"}:
        return cmd_copy_link(args)
    if command == "pull":
        return cmd_pull(args)
    if command == "rm":
        return cmd_rm(args)
    if command == "config":
        return cmd_config(args)
    if command == "install-autostart":
        return cmd_install_autostart(args)
    if command == "uninstall-autostart":
        return cmd_uninstall_autostart(args)
    if command == "install-drive-icon":
        return cmd_install_drive_icon(args)
    if command == "uninstall-drive-icon":
        return cmd_uninstall_drive_icon(args)
    if command == "setup":
        return cmd_setup(args)
    if command == "install-context-menu":
        return cmd_install_context_menu(args)
    if command == "uninstall-context-menu":
        return cmd_uninstall_context_menu(args)
    if command == "purge":
        return cmd_purge(args)
    if command == "bootstrap":
        return cmd_bootstrap(args)
    raise SystemExit(f"Unknown command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
