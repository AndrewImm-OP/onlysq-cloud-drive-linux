from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .paths import cache_dir, db_path, ensure_base_dirs
from .vpaths import basename, normalize_virtual_path, parent_path


@dataclass(slots=True)
class EntryRecord:
    path: str
    kind: str
    size: int
    ctime: int
    atime: int
    mtime: int
    change_time: int
    file_attributes: int
    cache_relpath: str | None
    remote_id: str | None
    owner_key: str | None
    public_url: str | None
    dirty: bool


class IndexDB:
    def __init__(self, path: Path | None = None) -> None:
        ensure_base_dirs()
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS entries (
                    path TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('file', 'dir')),
                    size INTEGER NOT NULL DEFAULT 0,
                    ctime INTEGER NOT NULL,
                    atime INTEGER NOT NULL,
                    mtime INTEGER NOT NULL,
                    change_time INTEGER NOT NULL,
                    file_attributes INTEGER NOT NULL,
                    cache_relpath TEXT,
                    remote_id TEXT,
                    owner_key TEXT,
                    public_url TEXT,
                    dirty INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(path);
                """
            )
            self.conn.commit()

    def ensure_root(self, *, ctime: int, atime: int, mtime: int, change_time: int, file_attributes: int) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO entries(
                    path, kind, size, ctime, atime, mtime, change_time, file_attributes, cache_relpath,
                    remote_id, owner_key, public_url, dirty
                ) VALUES(?, 'dir', 0, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0)
                """,
                ("/", ctime, atime, mtime, change_time, file_attributes),
            )
            self.conn.commit()

    def generate_cache_relpath(self, suggested_name: str) -> str:
        suffix = Path(suggested_name).suffix
        return f"{uuid.uuid4().hex}{suffix}"

    def get_cache_abs_path(self, cache_relpath: str) -> Path:
        return cache_dir() / cache_relpath

    def iter_entries(self) -> Iterator[EntryRecord]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM entries ORDER BY CASE WHEN path='/' THEN 0 ELSE 1 END, path"
            ).fetchall()
        for row in rows:
            record = self._row_to_record(row)
            if record is not None:
                yield record

    def get_entry(self, path: str) -> EntryRecord | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM entries WHERE path=?", (normalize_virtual_path(path),)).fetchone()
        return self._row_to_record(row) if row else None

    def list_children(self, directory: str) -> list[EntryRecord]:
        directory = normalize_virtual_path(directory)
        results: list[EntryRecord] = []
        for record in self.iter_entries():
            if record.path == directory:
                continue
            if parent_path(record.path) == directory:
                results.append(record)
        return sorted(results, key=lambda r: basename(r.path).lower())

    def ensure_dir(self, path: str, *, ctime: int, atime: int, mtime: int, change_time: int, file_attributes: int) -> None:
        path = normalize_virtual_path(path)
        if path == "/":
            self.ensure_root(
                ctime=ctime,
                atime=atime,
                mtime=mtime,
                change_time=change_time,
                file_attributes=file_attributes,
            )
            return
        parent = parent_path(path)
        if parent != path and self.get_entry(parent) is None:
            self.ensure_dir(
                parent,
                ctime=ctime,
                atime=atime,
                mtime=mtime,
                change_time=change_time,
                file_attributes=file_attributes,
            )
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO entries(
                    path, kind, size, ctime, atime, mtime, change_time, file_attributes, cache_relpath,
                    remote_id, owner_key, public_url, dirty
                ) VALUES (?, 'dir', 0, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0)
                """,
                (path, ctime, atime, mtime, change_time, file_attributes),
            )
            self.conn.commit()

    def create_file(
        self,
        path: str,
        *,
        ctime: int,
        atime: int,
        mtime: int,
        change_time: int,
        file_attributes: int,
        cache_relpath: str,
        size: int = 0,
        remote_id: str | None = None,
        owner_key: str | None = None,
        public_url: str | None = None,
        dirty: bool = True,
    ) -> None:
        path = normalize_virtual_path(path)
        self.ensure_dir(
            parent_path(path),
            ctime=ctime,
            atime=atime,
            mtime=mtime,
            change_time=change_time,
            file_attributes=file_attributes,
        )
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO entries(
                    path, kind, size, ctime, atime, mtime, change_time, file_attributes, cache_relpath,
                    remote_id, owner_key, public_url, dirty
                ) VALUES (?, 'file', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    path,
                    size,
                    ctime,
                    atime,
                    mtime,
                    change_time,
                    file_attributes,
                    cache_relpath,
                    remote_id,
                    owner_key,
                    public_url,
                    1 if dirty else 0,
                ),
            )
            self.conn.commit()

    def update_entry(self, record: EntryRecord) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE entries
                SET size=?, ctime=?, atime=?, mtime=?, change_time=?, file_attributes=?, cache_relpath=?,
                    remote_id=?, owner_key=?, public_url=?, dirty=?
                WHERE path=?
                """,
                (
                    record.size,
                    record.ctime,
                    record.atime,
                    record.mtime,
                    record.change_time,
                    record.file_attributes,
                    record.cache_relpath,
                    record.remote_id,
                    record.owner_key,
                    record.public_url,
                    1 if record.dirty else 0,
                    record.path,
                ),
            )
            self.conn.commit()

    def set_remote(
        self,
        path: str,
        *,
        remote_id: str | None,
        owner_key: str | None,
        public_url: str | None,
        size: int,
        dirty: bool,
        atime: int,
        mtime: int,
        change_time: int,
    ) -> None:
        path = normalize_virtual_path(path)
        with self._lock:
            self.conn.execute(
                """
                UPDATE entries
                SET remote_id=?, owner_key=?, public_url=?, size=?, dirty=?, atime=?, mtime=?, change_time=?
                WHERE path=?
                """,
                (remote_id, owner_key, public_url, size, 1 if dirty else 0, atime, mtime, change_time, path),
            )
            self.conn.commit()

    def mark_dirty(self, path: str, *, size: int, atime: int, mtime: int, change_time: int) -> None:
        path = normalize_virtual_path(path)
        with self._lock:
            self.conn.execute(
                "UPDATE entries SET dirty=1, size=?, atime=?, mtime=?, change_time=? WHERE path=?",
                (size, atime, mtime, change_time, path),
            )
            self.conn.commit()

    def set_times_and_attrs(
        self,
        path: str,
        *,
        ctime: int,
        atime: int,
        mtime: int,
        change_time: int,
        file_attributes: int,
        size: int,
    ) -> None:
        path = normalize_virtual_path(path)
        with self._lock:
            self.conn.execute(
                """
                UPDATE entries
                SET ctime=?, atime=?, mtime=?, change_time=?, file_attributes=?, size=?
                WHERE path=?
                """,
                (ctime, atime, mtime, change_time, file_attributes, size, path),
            )
            self.conn.commit()

    def delete_path(self, path: str) -> None:
        path = normalize_virtual_path(path)
        with self._lock:
            self.conn.execute("DELETE FROM entries WHERE path=?", (path,))
            self.conn.commit()

    def rename_subtree(self, old_path: str, new_path: str) -> None:
        old_path = normalize_virtual_path(old_path)
        new_path = normalize_virtual_path(new_path)
        rows = [r for r in self.iter_entries() if r.path == old_path or r.path.startswith(old_path + "/")]
        rows.sort(key=lambda r: len(r.path))
        with self._lock:
            for row in rows:
                if row.path == old_path:
                    updated_path = new_path
                else:
                    updated_path = new_path + row.path[len(old_path):]
                self.conn.execute("UPDATE entries SET path=? WHERE path=?", (updated_path, row.path))
            self.conn.commit()

    def total_file_size(self) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(size), 0) AS total_size FROM entries WHERE kind='file'"
            ).fetchone()
        return int(row["total_size"])

    def _row_to_record(self, row: sqlite3.Row | None) -> EntryRecord | None:
        if row is None:
            return None
        return EntryRecord(
            path=row["path"],
            kind=row["kind"],
            size=int(row["size"]),
            ctime=int(row["ctime"]),
            atime=int(row["atime"]),
            mtime=int(row["mtime"]),
            change_time=int(row["change_time"]),
            file_attributes=int(row["file_attributes"]),
            cache_relpath=row["cache_relpath"],
            remote_id=row["remote_id"],
            owner_key=row["owner_key"],
            public_url=row["public_url"],
            dirty=bool(row["dirty"]),
        )
