"""Path helpers.

:func:`out_dir` is the conventional output path used by the sibling projects:
``<project_root>/out/<YYYY-MM-DD>/``. On collision (same project_root + same
day, second run), it appends ``_2`` / ``_3`` so reruns don't clobber each other.

XDG helpers are optional — they wrap ``platformdirs`` for consumers who want
OS-standard dirs. The sibling projects don't use them.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_state_dir


def out_dir(project_root: Path | str, day: date | None = None) -> Path:
    """Return ``<project_root>/out/<YYYY-MM-DD>``, creating it.

    If the directory already exists, append ``_2``, ``_3``, ... until a fresh
    name is free. A same-day rerun therefore gets ``out/2026-05-03_2/``
    instead of overwriting the previous run's outputs.
    """
    if day is None:
        day = date.today()
    base = Path(project_root) / "out"
    target = base / day.isoformat()
    if not target.exists():
        target.mkdir(parents=True)
        return target
    n = 2
    while True:
        candidate = base / f"{day.isoformat()}_{n}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
        n += 1


def xdg_data(app_name: str) -> Path:
    """OS-standard user data/config dir, e.g. ``~/.config/<app>`` on Linux."""
    return Path(user_config_dir(app_name))


def xdg_state(app_name: str) -> Path:
    """OS-standard user state dir, e.g. ``~/.local/state/<app>`` on Linux."""
    return Path(user_state_dir(app_name))


def xdg_cache(app_name: str) -> Path:
    """OS-standard user cache dir, e.g. ``~/.cache/<app>`` on Linux."""
    return Path(user_cache_dir(app_name))
