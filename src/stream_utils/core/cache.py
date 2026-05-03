"""SQLite KV cache used by the twitch / llm / transcribe modules.

JSON-serialized values, optional per-key TTL, namespaced keys. Each consumer
typically passes its own ``cache.db`` path — multiple :class:`Cache` instances
on the same path are safe (WAL journal mode).

Not for large blobs (>1 MB per value) — use a flat file for those.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any

from stream_utils.core.errors import CacheError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    namespace  TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    expires_at REAL,
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv(expires_at);
"""


class Cache:
    """A simple JSON-serialized SQLite KV cache with optional per-key TTL."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None → autocommit; we don't do multi-statement transactions here
        self._conn = sqlite3.connect(str(self._path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)

    def get(self, namespace: str, key: str) -> Any | None:
        """Return the cached value, or ``None`` if missing or expired.

        Expired rows are deleted lazily on read.
        """
        row = self._conn.execute(
            "SELECT value, expires_at FROM kv WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
        if row is None:
            return None
        value_json, expires_at = row
        if expires_at is not None and expires_at <= time.time():
            self._conn.execute(
                "DELETE FROM kv WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            return None
        try:
            return json.loads(value_json)
        except json.JSONDecodeError as e:
            raise CacheError(
                f"Corrupt cache value for {namespace}/{key}: {e}"
            ) from e

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl_seconds: float | None = None,
    ) -> None:
        """Store a value. ``ttl_seconds=None`` means no expiration."""
        try:
            value_json = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise CacheError(
                f"Cannot JSON-encode value for {namespace}/{key}: {e}"
            ) from e
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        self._conn.execute(
            "INSERT OR REPLACE INTO kv(namespace, key, value, expires_at) VALUES(?, ?, ?, ?)",
            (namespace, key, value_json, expires_at),
        )

    def delete(self, namespace: str, key: str) -> None:
        """Remove the row if present. No-op if missing."""
        self._conn.execute(
            "DELETE FROM kv WHERE namespace = ? AND key = ?",
            (namespace, key),
        )

    def evict_expired(self) -> int:
        """Delete all expired rows. Returns the number of rows deleted."""
        cur = self._conn.execute(
            "DELETE FROM kv WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (time.time(),),
        )
        return cur.rowcount

    def keys(self, namespace: str) -> Iterable[str]:
        """Yield non-expired keys in the given namespace."""
        now = time.time()
        for (key,) in self._conn.execute(
            "SELECT key FROM kv WHERE namespace = ? AND (expires_at IS NULL OR expires_at > ?)",
            (namespace, now),
        ):
            yield key

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Cache:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
