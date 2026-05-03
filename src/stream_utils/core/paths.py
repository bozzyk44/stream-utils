"""Path helpers.

:func:`out_dir` is the conventional output path used by the sibling projects:
``<project_root>/out/<YYYY-MM-DD_HHMMSS>/`` by default. The second-level
timestamp ensures multiple calibration runs in the same day don't collide;
the resulting clutter in ``out/`` is swept at the end of a session anyway.

If a plain ``date`` is passed instead, falls back to date-only naming with
``_2``/``_3`` collision suffix (legacy mode, mainly for tests / dated reports).

XDG helpers are optional — they wrap ``platformdirs`` for consumers who want
OS-standard dirs. The sibling projects don't use them.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_state_dir


def out_dir(
    project_root: Path | str,
    when: datetime | date | None = None,
) -> Path:
    """Return ``<project_root>/out/<dirname>``, creating it.

    Directory name derived from ``when``:

    - ``None`` (default) → ``datetime.now()`` → ``"YYYY-MM-DD_HHMMSS"``
    - ``datetime``       → ``"YYYY-MM-DD_HHMMSS"``
    - plain ``date``     → ``"YYYY-MM-DD"`` (legacy mode)

    On collision (same exact second / same date in legacy mode), append
    ``_2``, ``_3``, ... until a fresh name is free.
    """
    if when is None:
        when = datetime.now()
    base = Path(project_root) / "out"
    name = (
        when.strftime("%Y-%m-%d_%H%M%S")
        if isinstance(when, datetime)
        else when.isoformat()
    )
    target = base / name
    if not target.exists():
        target.mkdir(parents=True)
        return target
    n = 2
    while True:
        candidate = base / f"{name}_{n}"
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
