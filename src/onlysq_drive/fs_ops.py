from __future__ import annotations

import errno
import logging
import os
import stat
import threading
import time
from pathlib import Path

import pyfuse3

from .cloud_client import CloudClient
from .index_db import EntryRecord, IndexDB
from .vpaths import basename, join_virtual_path, parent_path


# ── helpers ──────────────────────────────────────────────────────────────

def _time_ns() -> int:
    """Current time in nanoseconds (used by pyfuse3 for timestamps)."""
    return int(time.time() * 1e9)


_next_inode_lock = threading.Lock()
_next_inode_counter = pyfuse3.ROOT_INODE + 1


def _alloc_inode() -> int:
    global _next_inode_counter
    with _next_inode_lock:
        ino = _next_inode_counter
        _next_inode_counter += 1
    return ino


# ── in-memory objects ────────────────────────────────────────────────────

class BaseEntryObj:
    __slots__ = (
        "path", "inode", "is_dir", "file_size",
        "cache_path", "remote_id", "owner_key", "public_url",
        "dirty", "ctime_ns", "atime_ns", "mtime_ns",
    )

    def __init__(
        self,
        path: str,
        inode: int,
        is_dir: bool,
        file_size: int = 0,
        cache_path: Path | None = None,
        remote_id: str | None = None,
        owner_key: str | None = None,
        public_url: str | None = None,
        dirty: bool = False,
        ctime_ns: int | None = None,
        atime_ns: int | None = None,
        mtime_ns: int | None = None,
    ) -> None:
        now = _time_ns()
        self.path = path
        self.inode = inode
        self.is_dir = is_dir
        self.file_size = file_size
        self.cache_path = cache_path
        self.remote_id = remote_id
        self.owner_key = owner_key
        self.public_url = public_url
        self.dirty = dirty
        self.ctime_ns = ctime_ns or now
        self.atime_ns = atime_ns or now
        self.mtime_ns = mtime_ns or now

    @property
    def name(self) -> str:
        return basename(self.path)

    def entry_attributes(self) -> pyfuse3.EntryAttributes:
        attr = pyfuse3.EntryAttributes()
        attr.st_ino = self.inode
        if self.is_dir:
            attr.st_mode = stat.S_IFDIR | 0o755
            attr.st_nlink = 2
            attr.st_size = 0
        else:
            attr.st_mode = stat.S_IFREG | 0o644
            attr.st_nlink = 1
            attr.st_size = self.file_size
        attr.st_uid = os.getuid()
        attr.st_gid = os.getgid()
        attr.st_atime_ns = self.atime_ns
        attr.st_mtime_ns = self.mtime_ns
        attr.st_ctime_ns = self.ctime_ns
        attr.st_blksize = 4096
        attr.st_blocks = (self.file_size + 511) // 512 if not self.is_dir else 0
        attr.attr_timeout = 1
        attr.entry_timeout = 1
        return attr

    # ── file-specific helpers (no-op on dirs) ────────────────────────────

    def ensure_cache(self, cloud: CloudClient) -> None:
        if self.is_dir or self.cache_path is None:
            return
        if self.cache_path.exists():
            self.file_size = self.cache_path.stat().st_size
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if self.remote_id:
            cloud.download(self.remote_id, self.cache_path)
        else:
            self.cache_path.touch()
        self.file_size = self.cache_path.stat().st_size


class OpenedFile:
    """Wraps an open file handle for pyfuse3."""
    __slots__ = ("entry", "fh")

    def __init__(self, entry: BaseEntryObj, fh: int) -> None:
        self.entry = entry
        self.fh = fh


# ── FUSE operations ─────────────────────────────────────────────────────

class OnlySQFuseOperations(pyfuse3.Operations):
    def __init__(self, db: IndexDB, cloud: CloudClient, volume_label: str) -> None:
        super().__init__()
        self.db = db
        self.cloud = cloud
        self.volume_label = volume_label
        self._lock = threading.Lock()

        # path -> entry
        self._entries: dict[str, BaseEntryObj] = {}
        # inode -> path
        self._inode_to_path: dict[int, str] = {}
        # fh -> OpenedFile
        self._open_files: dict[int, OpenedFile] = {}
        self._next_fh = 0

        self._ensure_root()
        self._load_from_db()

    # ── bootstrap ────────────────────────────────────────────────────────

    def _ensure_root(self) -> None:
        now = _time_ns()
        self.db.ensure_root(
            ctime=now,
            atime=now,
            mtime=now,
            change_time=now,
            file_attributes=0,
        )

    def _load_from_db(self) -> None:
        self._entries.clear()
        self._inode_to_path.clear()
        for record in self.db.iter_entries():
            obj = self._record_to_obj(record)
            self._entries[record.path] = obj
            self._inode_to_path[obj.inode] = record.path

    def _record_to_obj(self, record: EntryRecord) -> BaseEntryObj:
        is_dir = record.kind == "dir"
        cache_path = self.db.get_cache_abs_path(record.cache_relpath) if record.cache_relpath else None
        inode = pyfuse3.ROOT_INODE if record.path == "/" else _alloc_inode()
        return BaseEntryObj(
            path=record.path,
            inode=inode,
            is_dir=is_dir,
            file_size=record.size if not is_dir else 0,
            cache_path=cache_path,
            remote_id=record.remote_id,
            owner_key=record.owner_key,
            public_url=record.public_url,
            dirty=record.dirty,
            ctime_ns=record.ctime,
            atime_ns=record.atime,
            mtime_ns=record.mtime,
        )

    # ── internal helpers ─────────────────────────────────────────────────

    def _path_by_inode(self, inode: int) -> str | None:
        return self._inode_to_path.get(inode)

    def _entry_by_inode(self, inode: int) -> BaseEntryObj | None:
        path = self._path_by_inode(inode)
        if path is None:
            return None
        return self._entries.get(path)

    def _alloc_fh(self, entry: BaseEntryObj) -> int:
        fh = self._next_fh
        self._next_fh += 1
        self._open_files[fh] = OpenedFile(entry, fh)
        return fh

    def _persist_obj(self, obj: BaseEntryObj) -> None:
        record = EntryRecord(
            path=obj.path,
            kind="dir" if obj.is_dir else "file",
            size=obj.file_size,
            ctime=obj.ctime_ns,
            atime=obj.atime_ns,
            mtime=obj.mtime_ns,
            change_time=obj.mtime_ns,
            file_attributes=0,
            cache_relpath=obj.cache_path.name if obj.cache_path else None,
            remote_id=obj.remote_id,
            owner_key=obj.owner_key,
            public_url=obj.public_url,
            dirty=obj.dirty,
        )
        self.db.update_entry(record)

    def _sync_file(self, obj: BaseEntryObj) -> None:
        if obj.is_dir or obj.cache_path is None:
            return
        obj.ensure_cache(self.cloud)
        if not obj.dirty and obj.remote_id:
            return
        old_remote_id = obj.remote_id
        old_owner_key = obj.owner_key
        uploaded = self.cloud.upload(obj.cache_path)
        obj.remote_id = uploaded.remote_id
        obj.public_url = uploaded.public_url
        obj.owner_key = uploaded.owner_key
        obj.file_size = obj.cache_path.stat().st_size
        obj.dirty = False
        now = _time_ns()
        obj.atime_ns = now
        obj.mtime_ns = now
        self.db.set_remote(
            obj.path,
            remote_id=obj.remote_id,
            owner_key=obj.owner_key,
            public_url=obj.public_url,
            size=obj.file_size,
            dirty=False,
            atime=obj.atime_ns,
            mtime=obj.mtime_ns,
            change_time=obj.mtime_ns,
        )
        if old_remote_id and old_owner_key and old_remote_id != obj.remote_id:
            try:
                self.cloud.delete(old_remote_id, old_owner_key)
            except Exception as exc:
                logging.warning("Failed to delete old remote object %s: %s", old_remote_id, exc)

    # ── pyfuse3.Operations overrides ─────────────────────────────────────

    async def getattr(self, inode: int, ctx=None) -> pyfuse3.EntryAttributes:
        with self._lock:
            entry = self._entry_by_inode(inode)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return entry.entry_attributes()

    async def setattr(self, inode: int, attr, fields, fh, ctx) -> pyfuse3.EntryAttributes:
        with self._lock:
            entry = self._entry_by_inode(inode)
            if entry is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            if fields.update_size:
                if not entry.is_dir and entry.cache_path:
                    entry.ensure_cache(self.cloud)
                    with entry.cache_path.open("r+b") as f:
                        f.truncate(attr.st_size)
                    entry.file_size = attr.st_size
                    entry.dirty = True
            if fields.update_atime:
                entry.atime_ns = attr.st_atime_ns
            if fields.update_mtime:
                entry.mtime_ns = attr.st_mtime_ns
            if entry.path != "/":
                self._persist_obj(entry)
        return entry.entry_attributes()

    async def lookup(self, parent_inode: int, name: bytes, ctx=None) -> pyfuse3.EntryAttributes:
        name_str = name.decode("utf-8", errors="surrogateescape")
        with self._lock:
            parent_path_str = self._path_by_inode(parent_inode)
            if parent_path_str is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            child_path = join_virtual_path(parent_path_str, name_str)
            entry = self._entries.get(child_path)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return entry.entry_attributes()

    async def opendir(self, inode: int, ctx) -> int:
        with self._lock:
            entry = self._entry_by_inode(inode)
        if entry is None or not entry.is_dir:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return inode  # use inode as dir handle

    async def readdir(self, fh: int, start_id: int, token) -> None:
        inode = fh  # we returned inode from opendir
        with self._lock:
            entry = self._entry_by_inode(inode)
            if entry is None:
                return
            children = []
            for child_path, child_obj in self._entries.items():
                if child_path == entry.path:
                    continue
                if parent_path(child_path) == entry.path:
                    children.append(child_obj)
        children.sort(key=lambda e: e.name.lower())
        for idx, child in enumerate(children):
            if idx < start_id:
                continue
            name_bytes = child.name.encode("utf-8", errors="surrogateescape")
            if not pyfuse3.readdir_reply(token, name_bytes, child.entry_attributes(), idx + 1):
                break

    async def open(self, inode: int, flags: int, ctx) -> pyfuse3.FileInfo:
        with self._lock:
            entry = self._entry_by_inode(inode)
            if entry is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            if entry.is_dir:
                raise pyfuse3.FUSEError(errno.EISDIR)
            entry.ensure_cache(self.cloud)
            fh = self._alloc_fh(entry)
        fi = pyfuse3.FileInfo(fh=fh)
        return fi

    async def create(self, parent_inode: int, name: bytes, mode: int, flags: int, ctx) -> tuple[pyfuse3.FileInfo, pyfuse3.EntryAttributes]:
        name_str = name.decode("utf-8", errors="surrogateescape")
        with self._lock:
            parent_path_str = self._path_by_inode(parent_inode)
            if parent_path_str is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            child_path = join_virtual_path(parent_path_str, name_str)
            if child_path in self._entries:
                raise pyfuse3.FUSEError(errno.EEXIST)
            now = _time_ns()
            cache_relpath = self.db.generate_cache_relpath(name_str or "unnamed.bin")
            cache_abs = self.db.get_cache_abs_path(cache_relpath)
            cache_abs.parent.mkdir(parents=True, exist_ok=True)
            cache_abs.touch()
            inode = _alloc_inode()
            obj = BaseEntryObj(
                path=child_path,
                inode=inode,
                is_dir=False,
                file_size=0,
                cache_path=cache_abs,
                dirty=True,
                ctime_ns=now,
                atime_ns=now,
                mtime_ns=now,
            )
            self._entries[child_path] = obj
            self._inode_to_path[inode] = child_path
            self.db.create_file(
                child_path,
                ctime=now, atime=now, mtime=now, change_time=now,
                file_attributes=0,
                cache_relpath=cache_relpath,
                size=0, dirty=True,
            )
            fh = self._alloc_fh(obj)
        fi = pyfuse3.FileInfo(fh=fh)
        return fi, obj.entry_attributes()

    async def read(self, fh: int, off: int, size: int) -> bytes:
        with self._lock:
            opened = self._open_files.get(fh)
            if opened is None:
                raise pyfuse3.FUSEError(errno.EBADF)
            entry = opened.entry
            entry.ensure_cache(self.cloud)
            if entry.cache_path is None:
                raise pyfuse3.FUSEError(errno.EIO)
            if off >= entry.file_size:
                return b""
            with entry.cache_path.open("rb") as f:
                f.seek(off)
                data = f.read(size)
            entry.atime_ns = _time_ns()
        return data

    async def write(self, fh: int, off: int, buf: bytes) -> int:
        with self._lock:
            opened = self._open_files.get(fh)
            if opened is None:
                raise pyfuse3.FUSEError(errno.EBADF)
            entry = opened.entry
            entry.ensure_cache(self.cloud)
            if entry.cache_path is None:
                raise pyfuse3.FUSEError(errno.EIO)
            entry.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with entry.cache_path.open("r+b") as f:
                f.seek(off)
                f.write(buf)
            entry.file_size = entry.cache_path.stat().st_size
            now = _time_ns()
            entry.atime_ns = now
            entry.mtime_ns = now
            entry.dirty = True
            self.db.mark_dirty(
                entry.path,
                size=entry.file_size,
                atime=entry.atime_ns,
                mtime=entry.mtime_ns,
                change_time=entry.mtime_ns,
            )
        return len(buf)

    async def release(self, fh: int) -> None:
        with self._lock:
            opened = self._open_files.pop(fh, None)
            if opened is None:
                return
            entry = opened.entry
            if not entry.is_dir and (entry.dirty or not entry.remote_id):
                self._sync_file(entry)

    async def releasedir(self, fh: int) -> None:
        pass  # nothing to release for dirs (we use inode as handle)

    async def flush(self, fh: int) -> None:
        with self._lock:
            opened = self._open_files.get(fh)
            if opened is None:
                return
            entry = opened.entry
            if not entry.is_dir and (entry.dirty or not entry.remote_id):
                self._sync_file(entry)

    async def mkdir(self, parent_inode: int, name: bytes, mode: int, ctx) -> pyfuse3.EntryAttributes:
        name_str = name.decode("utf-8", errors="surrogateescape")
        with self._lock:
            parent_path_str = self._path_by_inode(parent_inode)
            if parent_path_str is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            child_path = join_virtual_path(parent_path_str, name_str)
            if child_path in self._entries:
                raise pyfuse3.FUSEError(errno.EEXIST)
            now = _time_ns()
            inode = _alloc_inode()
            obj = BaseEntryObj(
                path=child_path,
                inode=inode,
                is_dir=True,
                ctime_ns=now, atime_ns=now, mtime_ns=now,
            )
            self._entries[child_path] = obj
            self._inode_to_path[inode] = child_path
            self.db.ensure_dir(
                child_path,
                ctime=now, atime=now, mtime=now, change_time=now,
                file_attributes=0,
            )
        return obj.entry_attributes()

    async def rmdir(self, parent_inode: int, name: bytes, ctx) -> None:
        name_str = name.decode("utf-8", errors="surrogateescape")
        with self._lock:
            parent_path_str = self._path_by_inode(parent_inode)
            if parent_path_str is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            child_path = join_virtual_path(parent_path_str, name_str)
            entry = self._entries.get(child_path)
            if entry is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            if not entry.is_dir:
                raise pyfuse3.FUSEError(errno.ENOTDIR)
            # check empty
            for p in self._entries:
                if p != child_path and parent_path(p) == child_path:
                    raise pyfuse3.FUSEError(errno.ENOTEMPTY)
            self._entries.pop(child_path, None)
            self._inode_to_path.pop(entry.inode, None)
            self.db.delete_path(child_path)

    async def unlink(self, parent_inode: int, name: bytes, ctx) -> None:
        name_str = name.decode("utf-8", errors="surrogateescape")
        with self._lock:
            parent_path_str = self._path_by_inode(parent_inode)
            if parent_path_str is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            child_path = join_virtual_path(parent_path_str, name_str)
            entry = self._entries.get(child_path)
            if entry is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            if entry.is_dir:
                raise pyfuse3.FUSEError(errno.EISDIR)
            if entry.remote_id and entry.owner_key:
                try:
                    self.cloud.delete(entry.remote_id, entry.owner_key)
                except Exception as exc:
                    logging.warning("Failed to delete remote file %s: %s", entry.remote_id, exc)
            if entry.cache_path and entry.cache_path.exists():
                entry.cache_path.unlink(missing_ok=True)
            self._entries.pop(child_path, None)
            self._inode_to_path.pop(entry.inode, None)
            self.db.delete_path(child_path)

    async def rename(self, parent_inode_old: int, name_old: bytes, parent_inode_new: int, name_new: bytes, flags: int, ctx) -> None:
        old_name = name_old.decode("utf-8", errors="surrogateescape")
        new_name = name_new.decode("utf-8", errors="surrogateescape")
        with self._lock:
            old_parent = self._path_by_inode(parent_inode_old)
            new_parent = self._path_by_inode(parent_inode_new)
            if old_parent is None or new_parent is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            old_path = join_virtual_path(old_parent, old_name)
            new_path = join_virtual_path(new_parent, new_name)
            entry = self._entries.get(old_path)
            if entry is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            # If target exists, remove it
            existing = self._entries.get(new_path)
            if existing is not None:
                if existing.is_dir:
                    # check empty
                    for p in self._entries:
                        if p != new_path and parent_path(p) == new_path:
                            raise pyfuse3.FUSEError(errno.ENOTEMPTY)
                else:
                    if existing.remote_id and existing.owner_key:
                        try:
                            self.cloud.delete(existing.remote_id, existing.owner_key)
                        except Exception as exc:
                            logging.warning("Failed to delete replaced remote object %s: %s", existing.remote_id, exc)
                    if existing.cache_path and existing.cache_path.exists():
                        existing.cache_path.unlink(missing_ok=True)
                self._entries.pop(new_path, None)
                self._inode_to_path.pop(existing.inode, None)
                self.db.delete_path(new_path)

            # Move the subtree
            affected = [p for p in self._entries if p == old_path or p.startswith(old_path + "/")]
            affected.sort(key=len)
            moved: dict[str, BaseEntryObj] = {}
            for current in affected:
                e = self._entries.pop(current)
                self._inode_to_path.pop(e.inode, None)
                target = new_path if current == old_path else new_path + current[len(old_path):]
                e.path = target
                moved[target] = e
            for p, e in moved.items():
                self._entries[p] = e
                self._inode_to_path[e.inode] = p
            self.db.rename_subtree(old_path, new_path)

    async def statfs(self, ctx) -> pyfuse3.StatvfsData:
        total = 1024 * 1024 * 1024 * 1024  # 1 TiB virtual
        used = self.db.total_file_size()
        block_size = 4096
        total_blocks = total // block_size
        free_blocks = max(0, (total - used)) // block_size
        s = pyfuse3.StatvfsData()
        s.f_bsize = block_size
        s.f_frsize = block_size
        s.f_blocks = total_blocks
        s.f_bfree = free_blocks
        s.f_bavail = free_blocks
        s.f_files = len(self._entries)
        s.f_ffree = 1000000
        s.f_favail = 1000000
        s.f_namemax = 255
        return s
