from datetime import date
from pathlib import Path

from stream_utils import out_dir, xdg_cache, xdg_data, xdg_state


def test_out_dir_creates_with_explicit_day(tmp_path: Path) -> None:
    d = out_dir(tmp_path, date(2026, 5, 3))
    assert d == tmp_path / "out" / "2026-05-03"
    assert d.is_dir()


def test_out_dir_collision_appends_suffix(tmp_path: Path) -> None:
    d1 = out_dir(tmp_path, date(2026, 5, 3))
    d2 = out_dir(tmp_path, date(2026, 5, 3))
    d3 = out_dir(tmp_path, date(2026, 5, 3))
    assert d1.name == "2026-05-03"
    assert d2.name == "2026-05-03_2"
    assert d3.name == "2026-05-03_3"
    for d in (d1, d2, d3):
        assert d.is_dir()


def test_out_dir_default_day_is_today(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    assert d.parent == tmp_path / "out"
    assert d.name == date.today().isoformat()


def test_out_dir_accepts_string_path(tmp_path: Path) -> None:
    d = out_dir(str(tmp_path), date(2026, 5, 3))
    assert d == tmp_path / "out" / "2026-05-03"
    assert d.is_dir()


def test_out_dir_creates_parent_out_subdir(tmp_path: Path) -> None:
    """If <project_root>/out/ doesn't exist yet, it gets created."""
    new_root = tmp_path / "fresh-project"
    new_root.mkdir()
    d = out_dir(new_root, date(2026, 5, 3))
    assert (new_root / "out").is_dir()
    assert d == new_root / "out" / "2026-05-03"


def test_xdg_helpers_return_paths() -> None:
    assert isinstance(xdg_data("stream-utils-test"), Path)
    assert isinstance(xdg_state("stream-utils-test"), Path)
    assert isinstance(xdg_cache("stream-utils-test"), Path)


def test_xdg_app_isolation() -> None:
    assert xdg_data("app-a") != xdg_data("app-b")
    assert xdg_state("app-a") != xdg_state("app-b")
    assert xdg_cache("app-a") != xdg_cache("app-b")
