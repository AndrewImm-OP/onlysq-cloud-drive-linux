from __future__ import annotations

import logging
import os
import subprocess
import sys
import trio

import pyfuse3

from .cloud_client import CloudClient
from .config import AppConfig
from .fs_ops import OnlySQFuseOperations
from .index_db import IndexDB


def _resolve_mountpoint_str(raw: str) -> str:
    """Resolve ~ without touching the filesystem (no stat/resolve).

    We avoid Path.resolve() because if the mountpoint is already a FUSE
    mount that is stuck/busy, resolve() will hang trying to stat it.
    """
    return os.path.normpath(os.path.expanduser(raw))


def _is_mountpoint_busy(path: str) -> bool:
    """Check if *path* is already a mountpoint by reading /proc/mounts.

    Does not touch the filesystem at all — reads a kernel pseudo-file.
    """
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == path:
                    return True
    except OSError:
        pass
    return False


def _try_unmount_stale(path: str) -> bool:
    """Try to unmount a stale FUSE mount. Returns True if successful.

    Escalation order:
    1. fusermount3 -u          (normal unmount)
    2. fusermount3 -uz         (lazy unmount — detaches immediately)
    3. sudo umount -l          (lazy unmount as root — last resort)
    """
    for cmd in [
        ["fusermount3", "-u", path],
        ["fusermount3", "-uz", path],
        ["sudo", "umount", "-l", path],
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=5)
            if result.returncode == 0:
                return True
        except Exception:
            continue
    return False


class MountedDrive:
    def __init__(self, config: AppConfig, mountpoint_str: str) -> None:
        self.config = config
        self.db = IndexDB()
        self.cloud = CloudClient(config)
        self.operations = OnlySQFuseOperations(self.db, self.cloud, config.volume_label)

        if not os.path.isdir(mountpoint_str):
            raise RuntimeError(
                f"Mountpoint directory does not exist: {mountpoint_str}\n"
                f"Run 'onlysq-drive setup' first to create it."
            )

        fuse_options = set(pyfuse3.default_options)
        fuse_options.add("fsname=onlysq-drive")
        # subtype=rclone makes the fs type "fuse.rclone" in /proc/mounts,
        # which KDE Solid recognises and shows in Dolphin's sidebar
        # under "Network" (alongside sshfs and rclone mounts).
        fuse_options.add("subtype=rclone")
        fuse_options.add("x-gvfs-show")
        fuse_options.discard("default_permissions")
        if config.debug:
            fuse_options.add("debug")

        pyfuse3.init(self.operations, mountpoint_str, fuse_options)

    def close(self) -> None:
        pyfuse3.close(unmount=True)
        self.db.close()


def run_mount(config: AppConfig) -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    mountpoint_str = _resolve_mountpoint_str(config.mountpoint)

    if _is_mountpoint_busy(mountpoint_str):
        # Try to clean up stale mount automatically
        logging.info("Mountpoint %s is busy, attempting fusermount3 -u ...", mountpoint_str)
        if _try_unmount_stale(mountpoint_str):
            logging.info("Stale mount cleaned up successfully.")
        else:
            # Still busy — another live instance is running
            print(
                f"Error: {mountpoint_str} is already mounted and cannot be unmounted.\n"
                f"\n"
                f"Likely the systemd service is already running:\n"
                f"  systemctl --user status onlysq-drive\n"
                f"\n"
                f"To stop it and mount manually:\n"
                f"  systemctl --user stop onlysq-drive\n"
                f"  onlysq-drive mount\n"
                f"\n"
                f"To force-unmount:\n"
                f"  fusermount3 -u {mountpoint_str}",
                file=sys.stderr,
            )
            return 1

    mounted = MountedDrive(config, mountpoint_str)
    print(f"Mounted {mountpoint_str} as {config.volume_label}. Press Ctrl+C to stop.")

    async def _main() -> None:
        await pyfuse3.main()

    try:
        trio.run(_main)
    except KeyboardInterrupt:
        pass
    finally:
        mounted.close()
        print("Drive unmounted.")
    return 0
