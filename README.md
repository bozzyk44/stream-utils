# stream-utils

Shared-библиотека общих хелперов для серии личных стример-инструментов.

Это **library**, а не CLI. Заворачивает то, что больно копипастить между несколькими sibling-проектами: Twitch Helix-клиент, LLM-обёртка с трекингом расхода, faster-whisper, FFmpeg vertical-crop, Twitch IRC daemon, Telegram delivery, nickname matcher, SQLite KV cache.

Полный архитектурный контракт — в [`CLAUDE.md`](CLAUDE.md).

## Установка

```bash
pip install git+https://github.com/bozzyk44/stream-utils.git@v0.1.0
```

Или пин на конкретный коммит (pre-1.0):

```bash
pip install git+https://github.com/bozzyk44/stream-utils.git@<sha>
```

В `pyproject.toml` потребителя:

```toml
dependencies = [
    "stream-utils @ git+https://github.com/bozzyk44/stream-utils.git@<tag-or-sha>",
]
```

## Использование

```python
from pathlib import Path

from stream_utils import Cache, out_dir

# Каталог под выходные данные с датой: <project_root>/out/<YYYY-MM-DD>/
day_out = out_dir(Path.cwd())

# JSON-сериализованный SQLite KV с опциональным TTL
cache = Cache(Path.cwd() / "cache.db")
cache.set("twitch.users", "broadcaster_42", {"login": "..."}, ttl_seconds=3600)
user = cache.get("twitch.users", "broadcaster_42")
```

Полный публичный API доступен напрямую из `stream_utils`:

```python
from stream_utils import (
    Cache,
    LLM, CallResult, ModelPricing,
    HelixClient, TwitchUser, TwitchVideo, TwitchStream, TwitchChannel, TwitchClip,
    Segment, Word, transcribe,
    SubtitleStyle, cut_vertical, segments_to_srt, write_srt, ffmpeg_available,
    out_dir, xdg_data, xdg_state, xdg_cache,
    StreamUtilsError, BudgetExceeded, ConfigError, CacheError, FFmpegError, TwitchAPIError,
)
```

## Разработка

```bash
# Создать venv и поставить dev-зависимости
python -m venv .venv
.venv\Scripts\activate              # Windows PowerShell
pip install -e ".[dev]" || pip install -e . && pip install pytest ruff mypy

# Тесты
pytest

# Type-check core/
mypy

# Lint
ruff check .
```

Если установлен `uv`, эквивалент:

```bash
uv sync
uv run pytest
```

## Лицензия

MIT — см. [`LICENSE`](LICENSE).
