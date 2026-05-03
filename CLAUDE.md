# CLAUDE.md — stream-utils

Pure shared library. No CLI utilities, no console scripts, no end-user "tool" mode.

A standalone Git repository. Physically lives inside `claude-ideas/` as a nested clone or submodule, but is otherwise fully independent: its own `.git`, its own GitHub repo, its own release cycle. **Nothing in this repo references `claude-ideas` or sibling repos.**

This file is the entry point for any work in this repo. Read fully before adding or modifying anything.

## What this is

A Python package installable via `pip install git+https://github.com/{user}/stream-utils.git@{tag-or-sha}`. After install, consumers do:

```python
from stream_utils import HelixClient, LLM, transcribe, NickMatcher, notify_telegram
```

The package wraps the parts that hurt to copy-paste across the 8 sibling streamer-tool projects in `claude-ideas/`: Helix auth, LLM cost tracking, Whisper invocation, Twitch IRC daemon, FFmpeg vertical-crop, nickname matching, Telegram delivery, SQLite KV cache.

## Relationship to sibling projects

The 8 sibling projects (`shorts-from-stream`, `tg-post-drafter`, `collab-hunter`, `chat-digest`, `mention-watcher`, `clip-rater`, `lore-bot`, `sponsor-pitch`) **MAY** depend on `stream-utils`. Each project independently chooses:

- **Depend on stream-utils** — list it in `pyproject.toml` as a Git URL dependency. Get bug fixes for free, accept the upgrade burden.
- **Copy a specific helper** — vendor it directly into the project. Stay frozen, accept maintenance burden.

Both are fine. The choice is per-project, even per-helper. What is **not** allowed: cross-sibling source imports (`from shorts_from_stream import ...`). If something is reusable, it goes here or gets copied — never linked from another sibling.

## What this is NOT

- **Not a CLI tool.** No `stream-utils` console-script. No `cmd/` subpackage. No `__main__.py` with subcommands. CLIs live in consuming projects.
- **Not a SaaS or service.** Pure library.
- **Not a framework.** `core/` is a stash of helpers, not a plugin architecture.
- **Not under semantic versioning yet.** Pre-1.0, breaking changes between commits are fine. Consumers pin to a Git SHA. After 1.0 (when stable consumers exist), follow strict semver.
- **Not bundled with user data.** No voice corpora, no nickname lists, no lore YAMLs, no pitch examples — those are per-streamer hand-maintained and live in the consuming project's `data/`.
- **Not a coordinator across consumers.** No shared budget file, no shared cache, no shared state. Each consumer manages its own `cache.db` and its own LLM spend ledger.

## Hard isolation rules

These are non-negotiable.

- **No imports from outside `stream_utils/`.** No `import claude_ideas.*`, no `sys.path.append`, no imports pointing to sibling repos.
- **No relative filesystem paths pointing outside the repo root.** Never `../something/`, never absolute paths to `D:\repos\...` hardcoded.
- **No environment-variable contracts.** stream-utils does not read env vars implicitly. Consumers read their own env and pass values explicitly to constructors. (An optional `Settings` helper with `STREAM_UTILS_` prefix exists for someone running their own quick scripts on top of the lib — but it is opt-in, not the path consumers should use.)
- **No assumptions about co-located data files.** stream-utils does not auto-discover sibling project data; consumers pass paths in.
- **No `cmd/` subpackage, no `[project.scripts]`.** This package exposes a Python API only.
- **No global state.** No module-level singletons, no thread locals — every public class takes config explicitly.

If `pip install git+...` followed by `from stream_utils import HelixClient` doesn't yield a working library on a fresh machine, the isolation is broken.

## Repo layout

```
stream-utils/
├── CLAUDE.md                # this file
├── README.md                # public-facing: install, usage examples
├── LICENSE                  # MIT — important since this'll be a dep
├── pyproject.toml           # uv-managed, no [project.scripts]
├── .env.example             # for local development of the lib only
├── .gitignore               # ignores .cache/, .env, *.session, *.log, dist/
│
├── src/
│   └── stream_utils/        # the importable package
│       ├── __init__.py      # version + curated public re-exports
│       ├── py.typed         # PEP 561 — we ship type info
│       │
│       └── core/            # all helpers live here
│           ├── __init__.py  # explicit __all__ for stable public API
│           ├── twitch.py    # Helix client (cached, app-token auto-refresh)
│           ├── llm.py       # ProxyAPI wrapper + spend tracker
│           ├── transcribe.py# faster-whisper wrapper
│           ├── cache.py     # SQLite KV used by twitch/llm/transcribe
│           ├── ffmpeg.py    # vertical crop, subtitle burn-in
│           ├── chat_irc.py  # twitchio IRC daemon helper
│           ├── notify.py    # Telegram bot delivery (python-telegram-bot)
│           ├── nicks.py     # nickname variant matcher
│           ├── paths.py     # date-keyed out_dir helper, optional XDG
│           ├── config.py    # optional pydantic-settings model with STREAM_UTILS_ prefix
│           └── errors.py    # StreamUtilsError + subclasses
│
└── tests/                   # minimal — see Testing section
    ├── core/
    │   ├── test_nicks.py
    │   ├── test_cache.py
    │   └── test_paths.py
    └── conftest.py
```

No `cmd/`, no `data_examples/`, no `__main__.py`, no `[project.scripts]` — those belonged to the tool-mode this project no longer has.

## Tech stack — fixed

Pinned in `pyproject.toml`. No optional deps, no extras.

- Python 3.12+
- `httpx` (sync where fine, async where required)
- `pydantic-settings` for the optional `Settings` helper
- `platformdirs` for optional XDG path resolution
- `loguru` for logs (consumers can override sinks)
- `openai` SDK pointed at `https://api.proxyapi.ru/openai/v1`
- `faster-whisper` (large-v3)
- `ffmpeg-python` + raw subprocess for tricky cases
- `twitchio` for IRC
- `python-telegram-bot` for Telegram delivery
- `pyyaml`
- `sqlite3` stdlib for caches

Notably **not** included (vs the old combined-tool design):

- `typer` — no CLI here. Consumers add `typer` themselves.
- `telethon` — needed by exactly one sibling (`mention-watcher`). It vendors that itself.
- `chat-downloader` — needed by 2 siblings (`shorts-from-stream`, `clip-rater`). Add only if a clearly shared wrapper emerges.
- `pandas` — CSV ergonomics belong in consumers (`collab-hunter`, `clip-rater`).
- `selectolax` — HTML parsing belongs in consumers until a shared wrapper emerges.

## `pyproject.toml` essentials

```toml
[project]
name = "stream-utils"
version = "0.1.0"
description = "Shared helpers for the streamer-tools sibling projects"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
    "httpx>=0.27",
    "pydantic-settings>=2.5",
    "platformdirs>=4.3",
    "loguru>=0.7",
    "openai>=1.50",
    "faster-whisper>=1.0",
    "ffmpeg-python>=0.2",
    "twitchio>=2.10",
    "python-telegram-bot>=21.5",
    "pyyaml>=6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/stream_utils"]
```

No `[project.scripts]`. This is a library, not a CLI tool.

Consumers depend on stream-utils via:

```toml
# in <consumer>/pyproject.toml
dependencies = [
    "stream-utils @ git+https://github.com/{user}/stream-utils.git@v0.3.0",
]
```

Pin to a tag once 1.0 lands; pin to a Git SHA pre-1.0. Avoid `@main` — that's an unpinned dependency.

## `core/` design rules

`core/` is a public library surface, so it has stricter rules than internal helpers.

### Public API contract

`core/__init__.py` re-exports what consumers use:

```python
from stream_utils.core.twitch import HelixClient, TwitchUser, TwitchVideo
from stream_utils.core.llm import LLM, TaskType, BudgetExceeded
from stream_utils.core.transcribe import transcribe, Segment
from stream_utils.core.cache import Cache
from stream_utils.core.notify import notify_telegram
from stream_utils.core.nicks import NickMatcher
from stream_utils.core.paths import out_dir, xdg_data, xdg_state, xdg_cache
from stream_utils.core.errors import StreamUtilsError, BudgetExceeded, ConfigError

__all__ = [
    "HelixClient", "TwitchUser", "TwitchVideo",
    "LLM", "TaskType",
    "transcribe", "Segment",
    "Cache",
    "notify_telegram",
    "NickMatcher",
    "out_dir", "xdg_data", "xdg_state", "xdg_cache",
    "StreamUtilsError", "BudgetExceeded", "ConfigError",
]
```

Consumers do `from stream_utils import HelixClient, LLM`. Anything not in `__all__` is private — break it whenever, no notice required.

### Rules for `core/` modules

- **Each module is independently importable.** `core/twitch.py` doesn't depend on `core/llm.py`, etc. They share `core/cache.py`, `core/paths.py`, `core/errors.py` only.
- **No global state.** Every public class takes config explicitly via constructor. There is no global `Settings` singleton — consumers manage their own config and pass values in.
- **Type-hinted strictly.** `mypy --strict` must pass on `core/`.
- **Pydantic models for data shapes** (`TwitchUser`, `TwitchVideo`, `Segment`).
- **Errors via custom exceptions** subclassing `StreamUtilsError`. Consumers should be able to `except StreamUtilsError:` and catch all of our problems.
- **Logging via `loguru`**, module-prefixed: `logger.bind(module="stream_utils.twitch")`. Consumers can filter our logs from theirs.
- **Public methods have docstrings.** Private methods don't need them.

## Configuration

stream-utils does **not** read env vars implicitly. Each consumer reads its own env and passes values explicitly:

```python
import os
from pathlib import Path
from stream_utils import HelixClient, LLM

helix = HelixClient(
    client_id=os.environ["TWITCH_CLIENT_ID"],
    client_secret=os.environ["TWITCH_CLIENT_SECRET"],
)

llm = LLM(
    proxyapi_key=os.environ["PROXYAPI_KEY"],
    daily_budget_rub=float(os.environ.get("DAILY_LLM_BUDGET_RUB", "500")),
    spend_log_path=Path("./cache.db"),
    project_tag="shorts-from-stream",  # written into the spend log row
)
```

This keeps consumers in charge of env naming. A consumer using `TWITCH_CLIENT_ID` doesn't need to rename to `STREAM_UTILS_TWITCH_CLIENT_ID`.

For convenience, an opt-in `stream_utils.core.config.Settings` pydantic-settings model is available, reading the `STREAM_UTILS_` prefix — useful only for someone running their own quick scripts on top of `stream-utils`. Consumer projects should NOT use it; they own their own env scheme.

## Output paths

`core/paths.py`:

```python
from pathlib import Path
from datetime import date

def out_dir(project_root: Path, day: date | None = None) -> Path:
    """{project_root}/out/{YYYY-MM-DD}, creating it if needed.
    Adds _2 / _3 suffix on collision so multiple same-day runs don't clobber.
    """
    ...

# Optional XDG helpers — not required by the convention.
def xdg_data(app_name: str) -> Path: ...
def xdg_state(app_name: str) -> Path: ...
def xdg_cache(app_name: str) -> Path: ...
```

The 8 sibling projects use `out/{YYYY-MM-DD}/` in the project root by convention. `out_dir()` is the helper for that. XDG helpers are available for anyone who prefers OS-standard dirs, but the sibling projects don't need them.

## LLM cost tracking

`core.llm.LLM` writes every call to the consumer-provided `spend_log_path` (typically `cache.db` in the consumer's project root). Schema:

```sql
CREATE TABLE IF NOT EXISTS llm_spend (
    timestamp  TEXT NOT NULL,
    project    TEXT NOT NULL,   -- consumer's project_tag
    model      TEXT NOT NULL,
    tokens_in  INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_rub   REAL NOT NULL
);
```

Each consumer enforces its own daily budget by calling `llm.check_today_spend()` on startup. There is no cross-project aggregation — if two consumers run on the same day, they have separate budgets and separate ledgers. (That's intentional: aggregation would mean a shared file, which breaks isolation.)

## Testing

Minimal but real. `tests/` only covers `core/`:

- `test_nicks.py` — table-driven tests for nickname matching, including transliteration and false-positive cases
- `test_cache.py` — TTL behavior, eviction, concurrent reads
- `test_paths.py` — `out_dir` collision suffix, XDG resolution, override behavior, Windows path handling

Run via `pytest`. CI on GitHub Actions: lint (`ruff`), type-check (`mypy --strict` on `core/`), tests on Linux + Windows. No tests for consumer projects — those live in their own repos.

## Versioning and releases

**Pre-1.0 (current):** semver loose, breaking changes between commits OK, no release tags required. Consumers pin via Git SHA: `stream-utils @ git+...@abc1234`.

**At 1.0** (once 2+ sibling projects depend on it stably):

- Tag releases on GitHub
- `core/` follows strict semver — breaking change in `core/` = major version bump
- Maintain `CHANGELOG.md` from 1.0 onwards

Don't pre-optimize for 1.0. Keep the door open by following the API contract above; that's enough.

## Embedding inside `claude-ideas`

The repo lives physically at `claude-ideas/stream-utils/` but is a separate Git repo, like every other top-level directory in `claude-ideas/`. Both this repo and the 8 consumer repos follow the parent-level rule: 1 dir = 1 project = 1 GitHub repo.

Two embedding options for the streamer's own machine:

- **Option A: nested clone (simpler).** `git clone` into the directory. `claude-ideas/.gitignore` (if `claude-ideas` were Git-tracked, which it isn't) would list `stream-utils/`. In practice the parent dir isn't tracked.
- **Option B: git submodule (cleaner sync across machines).** `git submodule add https://github.com/{user}/stream-utils.git`.

stream-utils itself doesn't know which option is in use — it's just a Git repo with no awareness of its parent context.

## Implementation order

Build `core/` incrementally, **driven by what the first consumer that adopts it actually needs**. Don't build the whole library up front.

Suggested first consumer: `shorts-from-stream` — it exercises `core/twitch.py`, `core/llm.py`, `core/transcribe.py`, `core/ffmpeg.py`, `core/cache.py`, `core/paths.py` in one project. After it works end-to-end on top of stream-utils, freeze that API as v0.1.

After the 2nd consumer adopts it, evaluate: did the API hold up under a different shape of use? If yes, push toward v0.2. If a copy-paste-only consumer (one that didn't adopt) caught a bug we missed, fix it here.

Don't build modules no consumer needs yet. `chat_irc.py`, `nicks.py`, `notify.py` should land only when `chat-digest`, `mention-watcher`, `lore-bot` actually want them.

### Phase 0 — scaffolding (0.5 day)

`pyproject.toml`, `LICENSE`, `README.md`, `src/stream_utils/__init__.py`, `core/errors.py`, `core/cache.py`, `core/paths.py` — minimal viable plus tests for the trivially-testable parts.

End of Phase 0: `pip install -e .` works. `from stream_utils import Cache, out_dir, StreamUtilsError` resolves. `pytest` passes.

### Phase 1 — first consumer drives core (1.5 days)

While building `shorts-from-stream`, push these modules into stream-utils as their needs emerge:

- `core/twitch.py` — Helix client, app-token auto-refresh
- `core/llm.py` — ProxyAPI wrapper + spend tracker
- `core/transcribe.py` — faster-whisper wrapper
- `core/ffmpeg.py` — vertical 9:16 crop + subtitle burn-in

End of Phase 1: `shorts-from-stream` runs end-to-end on top of stream-utils, with stream-utils pinned via Git SHA in its `pyproject.toml`. **Do not move to Phase 2 until this works on a fresh machine.**

### Phase 2+ — additional helpers as consumers adopt

- `core/chat_irc.py` lands when `chat-digest` adopts it.
- `core/notify.py` lands when `chat-digest` or `mention-watcher` needs Telegram delivery.
- `core/nicks.py` lands when `mention-watcher` adopts it.

Each addition follows the same rule: a real consumer drives the design, the API freezes after the second user.

## Things that will be tempting but are wrong

- **"Let me also expose a CLI inside stream-utils."** No. Pure library. CLIs live in consumers.
- **"Let me put the 8 utilities back inside stream-utils as `cmd/*`."** No. That was the old design. Each utility is now its own repo.
- **"Let me publish to PyPI."** Not yet. GitHub install via URL is enough until external users actually exist.
- **"Let me make `core/` async-everywhere."** No. Sync where simple, async where the underlying lib forces it (twitchio IRC). Mixed sync/async APIs are honest about what they wrap.
- **"Let me detect which consumer is calling `core/` and customize behavior."** No. Core is dumb — same behavior regardless of caller.
- **"Let me share the SQLite cache across consumers."** No. Each consumer has its own `cache.db`. Sharing breaks isolation and creates lock contention.
- **"Let me share a single LLM spend ledger across all consumers for a true daily budget."** No. Consumers are independent. If a per-streamer global cap is desired, the streamer adds a thin wrapper script in their own dotfiles — outside this repo.
- **"Let me bundle voice corpora / lore YAMLs / nick lists."** No. Per-streamer data lives in the consuming project, not in this lib.
- **"Let me expose the LLM router as a configurable plugin system."** No. Two providers (DeepSeek + Sonnet) routed by task name. A plugin system is overkill until 5+ providers, which won't happen.
- **"Let me add a global `Settings` singleton consumers must use."** No. Per-consumer config, passed explicitly.
- **"Let me have stream-utils auto-discover sibling project paths so consumers can chain pipelines."** No. Path-passing is the consumer's job. Cross-sibling auto-discovery breaks isolation.

## When in doubt

This is the streamer's shared toolbox. Optimize every decision for "I, the streamer, can read this code in 6 months and understand what it does, on a fresh machine, without context."

Boring is good. Three lines of clear code beat one clever abstraction.

If a question can't be resolved from this file, default to the simpler implementation and add a `# TODO: revisit` comment. Don't ask Claude Code to come up with new conventions — push the question back to me.

## Environment

For local development of the lib itself (running tests, smoke-checking helpers), copy `.env.example` to `.env` and fill in:

```
# For test fixtures only — consumers do NOT use this file.
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
PROXYAPI_KEY=...
```

These exist purely so library tests can hit real APIs in CI / smoke runs. Production consumers manage their own env entirely.
