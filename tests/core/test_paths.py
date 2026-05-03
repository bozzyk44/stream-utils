import re
from datetime import date, datetime
from pathlib import Path

from stream_utils import out_dir, xdg_cache, xdg_data, xdg_state


def test_out_dir_default_uses_now_with_seconds(tmp_path: Path) -> None:
    """Default mode: <project_root>/out/<YYYY-MM-DD_HHMMSS>/."""
    d = out_dir(tmp_path)
    assert d.parent == tmp_path / "out"
    # Name format: YYYY-MM-DD_HHMMSS (15 + 1 + 6 = 17 chars exactly with underscore)
    assert re.match(r"^\d{4}-\d{2}-\d{2}_\d{6}$", d.name), f"got {d.name!r}"
    assert d.is_dir()


def test_out_dir_explicit_datetime(tmp_path: Path) -> None:
    when = datetime(2026, 5, 3, 14, 30, 52)
    d = out_dir(tmp_path, when)
    assert d == tmp_path / "out" / "2026-05-03_143052"
    assert d.is_dir()


def test_out_dir_explicit_date_legacy(tmp_path: Path) -> None:
    """Date-only mode: still supported, name is YYYY-MM-DD without time."""
    d = out_dir(tmp_path, date(2026, 5, 3))
    assert d == tmp_path / "out" / "2026-05-03"
    assert d.is_dir()


def test_out_dir_collision_appends_suffix_datetime(tmp_path: Path) -> None:
    """Two calls in the exact same second still get different dirs."""
    when = datetime(2026, 5, 3, 14, 30, 52)
    d1 = out_dir(tmp_path, when)
    d2 = out_dir(tmp_path, when)
    d3 = out_dir(tmp_path, when)
    assert d1.name == "2026-05-03_143052"
    assert d2.name == "2026-05-03_143052_2"
    assert d3.name == "2026-05-03_143052_3"
    for d in (d1, d2, d3):
        assert d.is_dir()


def test_out_dir_collision_appends_suffix_date(tmp_path: Path) -> None:
    """Legacy date-only mode still has collision suffix."""
    d1 = out_dir(tmp_path, date(2026, 5, 3))
    d2 = out_dir(tmp_path, date(2026, 5, 3))
    assert d1.name == "2026-05-03"
    assert d2.name == "2026-05-03_2"


def test_out_dir_accepts_string_path(tmp_path: Path) -> None:
    d = out_dir(str(tmp_path), datetime(2026, 5, 3, 14, 30, 52))
    assert d == tmp_path / "out" / "2026-05-03_143052"
    assert d.is_dir()


def test_out_dir_creates_parent_out_subdir(tmp_path: Path) -> None:
    """If <project_root>/out/ doesn't exist yet, it gets created."""
    new_root = tmp_path / "fresh-project"
    new_root.mkdir()
    d = out_dir(new_root, datetime(2026, 5, 3, 14, 30, 52))
    assert (new_root / "out").is_dir()
    assert d == new_root / "out" / "2026-05-03_143052"


def test_xdg_helpers_return_paths() -> None:
    assert isinstance(xdg_data("stream-utils-test"), Path)
    assert isinstance(xdg_state("stream-utils-test"), Path)
    assert isinstance(xdg_cache("stream-utils-test"), Path)


def test_xdg_app_isolation() -> None:
    assert xdg_data("app-a") != xdg_data("app-b")
    assert xdg_state("app-a") != xdg_state("app-b")
    assert xdg_cache("app-a") != xdg_cache("app-b")
