# stream-utils

Shared helpers for a small constellation of personal streamer-tooling projects.

This is a **library**, not a CLI tool. It wraps the parts that hurt to copy-paste across multiple sibling projects: Twitch Helix client, LLM cost tracking, faster-whisper, FFmpeg vertical-crop, Twitch IRC daemon, Telegram delivery, nickname matcher, SQLite KV cache.

See `CLAUDE.md` for the full design contract.

## Install

```bash
pip install git+https://github.com/bozzyk44/stream-utils.git@v0.1.0
```

Or pin to a specific commit pre-1.0:

```bash
pip install git+https://github.com/bozzyk44/stream-utils.git@<sha>
```

In a consumer's `pyproject.toml`:

```toml
dependencies = [
    "stream-utils @ git+https://github.com/bozzyk44/stream-utils.git@<tag-or-sha>",
]
```

## Usage

```python
from pathlib import Path

from stream_utils import Cache, out_dir

# Date-keyed output directory: <project_root>/out/<YYYY-MM-DD>/
day_out = out_dir(Path.cwd())

# JSON-serialized SQLite KV with optional TTL
cache = Cache(Path.cwd() / "cache.db")
cache.set("twitch.users", "broadcaster_42", {"login": "..."}, ttl_seconds=3600)
user = cache.get("twitch.users", "broadcaster_42")
```

The full public API surfaces from `stream_utils` directly:

```python
from stream_utils import (
    Cache,
    out_dir, xdg_data, xdg_state, xdg_cache,
    StreamUtilsError, BudgetExceeded, ConfigError, CacheError,
)
```

Phase 1 modules (`HelixClient`, `LLM`, `transcribe`, FFmpeg helpers) land as the first consumer (`shorts-from-stream`) drives them into existence.

## Develop

```bash
# Set up a venv and install dev deps
python -m venv .venv
.venv\Scripts\activate              # Windows PowerShell
pip install -e ".[dev]" || pip install -e . && pip install pytest ruff mypy

# Run tests
pytest

# Type-check core
mypy

# Lint
ruff check .
```

If you have `uv` installed, the equivalent is:

```bash
uv sync
uv run pytest
```

## License

MIT — see `LICENSE`.
