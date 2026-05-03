import sqlite3
import time
from pathlib import Path

import pytest

from stream_utils import Cache
from stream_utils.core.errors import CacheError


def test_set_and_get_roundtrip(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k1", {"a": 1, "b": [2, 3]})
    assert c.get("ns", "k1") == {"a": 1, "b": [2, 3]}


def test_get_missing_returns_none(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    assert c.get("ns", "missing") is None


def test_namespace_isolation(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns_a", "k", "value_a")
    c.set("ns_b", "k", "value_b")
    assert c.get("ns_a", "k") == "value_a"
    assert c.get("ns_b", "k") == "value_b"


def test_overwrite_same_key(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k", "old")
    c.set("ns", "k", "new")
    assert c.get("ns", "k") == "new"


def test_delete(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k", "v")
    c.delete("ns", "k")
    assert c.get("ns", "k") is None


def test_delete_missing_is_noop(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.delete("ns", "never-existed")  # must not raise


def test_unicode_values(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k", "Привет, мир! 🎉")
    assert c.get("ns", "k") == "Привет, мир! 🎉"


def test_ttl_expiration(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k", "value", ttl_seconds=0.05)
    assert c.get("ns", "k") == "value"
    time.sleep(0.1)
    assert c.get("ns", "k") is None


def test_ttl_zero_is_immediately_expired(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k", "value", ttl_seconds=0)
    assert c.get("ns", "k") is None


def test_ttl_none_means_no_expiration(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k", "value", ttl_seconds=None)
    time.sleep(0.05)
    assert c.get("ns", "k") == "value"


def test_evict_expired_returns_count(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "expired_a", "v", ttl_seconds=0.01)
    c.set("ns", "expired_b", "v", ttl_seconds=0.01)
    c.set("ns", "alive", "v")
    time.sleep(0.05)
    assert c.evict_expired() == 2
    assert c.get("ns", "alive") == "v"
    assert c.get("ns", "expired_a") is None


def test_concurrent_handles_same_file(tmp_path: Path) -> None:
    """Two Cache handles on the same path should both work (WAL mode)."""
    c1 = Cache(tmp_path / "c.db")
    c2 = Cache(tmp_path / "c.db")
    c1.set("ns", "k1", "from_c1")
    c2.set("ns", "k2", "from_c2")
    assert c1.get("ns", "k2") == "from_c2"
    assert c2.get("ns", "k1") == "from_c1"


def test_corrupt_value_raises_cache_error(tmp_path: Path) -> None:
    db_path = tmp_path / "c.db"
    c = Cache(db_path)
    c.set("ns", "k", "valid")
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        "UPDATE kv SET value = 'not-json' WHERE namespace = 'ns' AND key = 'k'"
    )
    raw.commit()
    raw.close()
    with pytest.raises(CacheError):
        c.get("ns", "k")


def test_unencodable_value_raises_cache_error(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    with pytest.raises(CacheError):
        c.set("ns", "k", {1, 2, 3})  # set is not JSON-serializable


def test_keys_listing(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k1", "v")
    c.set("ns", "k2", "v")
    c.set("other", "k3", "v")
    assert sorted(c.keys("ns")) == ["k1", "k2"]


def test_keys_excludes_expired(tmp_path: Path) -> None:
    c = Cache(tmp_path / "c.db")
    c.set("ns", "k_expired", "v", ttl_seconds=0.01)
    c.set("ns", "k_alive", "v")
    time.sleep(0.05)
    assert sorted(c.keys("ns")) == ["k_alive"]


def test_context_manager_closes(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    with Cache(db) as c:
        c.set("ns", "k", "v")
    # After close, opening a fresh handle should still see the data.
    c2 = Cache(db)
    assert c2.get("ns", "k") == "v"


def test_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "cache.db"
    c = Cache(nested)
    c.set("ns", "k", "v")
    assert nested.is_file()
    assert c.get("ns", "k") == "v"
