"""ProxyAPI wrapper with local spend tracking and daily-budget guard.

ProxyAPI exposes multiple providers under different paths with **different SDK
shapes**. This wrapper hides that fact behind a single :meth:`LLM.chat` /
:meth:`LLM.embed` interface that routes by model name:

- ``claude-*`` → ``/anthropic/v1`` via the Anthropic SDK. ProxyAPI explicitly
  refuses Claude through OpenRouter ("Use the correct API endpoint for this
  provider"), so this is the only working path.
- everything else (``deepseek/*``, ``openai/*``, ``gpt-*`` etc.) →
  ``/openrouter/v1`` via the OpenAI SDK. OpenRouter is OpenAI-compatible and
  exposes the non-Claude catalog.
- embeddings → ``/openai/v1`` via the OpenAI SDK. OpenRouter is chat-only.

Cost tracking is **best-effort approximation** intended to catch prompt-loop
bugs and runaway spend, not authoritative billing. Authoritative cost is on
ProxyAPI's own dashboard. Per-1k-token rates are configurable via the
``pricing`` constructor argument.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from anthropic import Anthropic
from loguru import logger
from openai import OpenAI

from stream_utils.core.errors import BudgetExceeded, ConfigError


@dataclass(frozen=True)
class ModelPricing:
    """Approximate ProxyAPI cost in RUB per 1000 tokens."""

    rub_per_1k_in: float
    rub_per_1k_out: float


# Rough rates for budget-guard purposes only. Override via constructor when
# the real ProxyAPI rates drift. Numbers are intentionally pessimistic
# (slightly above real cost) so the guard fires before a runaway bill.
#
# Model IDs match exactly what ProxyAPI accepts on the route this wrapper
# picks for them — Anthropic-native (no prefix) for Claude, OpenRouter
# convention (``provider/model``) for non-Claude chat, plain OpenAI names
# for embeddings.
DEFAULT_PRICING: dict[str, ModelPricing] = {
    # Anthropic-native (claude-* → /anthropic/v1)
    "claude-opus-4-7": ModelPricing(rub_per_1k_in=2.00, rub_per_1k_out=10.00),
    "claude-sonnet-4-6": ModelPricing(rub_per_1k_in=0.40, rub_per_1k_out=2.00),
    "claude-opus-4-6": ModelPricing(rub_per_1k_in=2.00, rub_per_1k_out=10.00),
    "claude-haiku-4-5-20251001": ModelPricing(rub_per_1k_in=0.10, rub_per_1k_out=0.50),
    "claude-sonnet-4-5-20250929": ModelPricing(rub_per_1k_in=0.40, rub_per_1k_out=2.00),
    "claude-opus-4-5-20251101": ModelPricing(rub_per_1k_in=2.00, rub_per_1k_out=10.00),
    # DeepSeek via OpenRouter (chat)
    "deepseek/deepseek-chat-v3.1": ModelPricing(rub_per_1k_in=0.04, rub_per_1k_out=0.12),
    "deepseek/deepseek-v3.2": ModelPricing(rub_per_1k_in=0.04, rub_per_1k_out=0.12),
    "deepseek/deepseek-r1": ModelPricing(rub_per_1k_in=0.10, rub_per_1k_out=0.40),
    # OpenAI via OpenRouter (chat) or direct (embeddings)
    "openai/gpt-4o": ModelPricing(rub_per_1k_in=0.30, rub_per_1k_out=1.20),
    "openai/gpt-4o-mini": ModelPricing(rub_per_1k_in=0.02, rub_per_1k_out=0.08),
    "gpt-4o": ModelPricing(rub_per_1k_in=0.30, rub_per_1k_out=1.20),
    "gpt-4o-mini": ModelPricing(rub_per_1k_in=0.02, rub_per_1k_out=0.08),
    "text-embedding-3-large": ModelPricing(rub_per_1k_in=0.02, rub_per_1k_out=0.0),
    "text-embedding-3-small": ModelPricing(rub_per_1k_in=0.003, rub_per_1k_out=0.0),
}

DEFAULT_OPENROUTER_BASE_URL = "https://api.proxyapi.ru/openrouter/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.proxyapi.ru/openai/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.proxyapi.ru/anthropic"


@dataclass(frozen=True)
class CallResult:
    """One chat-completion's outcome: text + token counts + estimated cost."""

    text: str
    tokens_in: int
    tokens_out: int
    cost_rub: float
    model: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_spend (
    timestamp  TEXT NOT NULL,
    project    TEXT NOT NULL,
    model      TEXT NOT NULL,
    tokens_in  INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_rub   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_spend_ts ON llm_spend(timestamp);
"""


def is_anthropic_model(model: str) -> bool:
    """Heuristic: model names starting with ``claude-`` route via Anthropic native."""
    return model.startswith("claude-")


class LLM:
    """ProxyAPI wrapper with spend tracking and daily-budget guard.

    Each consumer instantiates one ``LLM`` per project, pinned to its own
    ``cache.db`` and ``project_tag``. The daily budget is enforced **per
    log file** — multiple consumers writing to separate logs don't share
    a budget (intentional, see ``CLAUDE.md`` "no cross-project aggregation").
    """

    def __init__(
        self,
        *,
        proxyapi_key: str,
        spend_log_path: Path | str,
        project_tag: str,
        daily_budget_rub: float = 500.0,
        openrouter_base_url: str = DEFAULT_OPENROUTER_BASE_URL,
        openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
        anthropic_base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        pricing: dict[str, ModelPricing] | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not proxyapi_key:
            raise ConfigError("proxyapi_key is required")
        if not project_tag:
            raise ConfigError("project_tag is required")
        if daily_budget_rub <= 0:
            raise ConfigError(f"daily_budget_rub must be > 0, got {daily_budget_rub}")
        self._openrouter_client = OpenAI(
            api_key=proxyapi_key, base_url=openrouter_base_url, timeout=timeout
        )
        self._openai_client = OpenAI(
            api_key=proxyapi_key, base_url=openai_base_url, timeout=timeout
        )
        self._anthropic_client = Anthropic(
            api_key=proxyapi_key, base_url=anthropic_base_url, timeout=timeout
        )
        self._budget = daily_budget_rub
        self._project = project_tag
        self._spend_log = Path(spend_log_path)
        self._spend_log.parent.mkdir(parents=True, exist_ok=True)
        self._pricing: dict[str, ModelPricing] = {**DEFAULT_PRICING, **(pricing or {})}
        with sqlite3.connect(str(self._spend_log)) as conn:
            conn.executescript(_SCHEMA)
        self._log = logger.bind(module="stream_utils.llm")

    @property
    def daily_budget_rub(self) -> float:
        return self._budget

    @property
    def project_tag(self) -> str:
        return self._project

    def check_today_spend(self) -> float:
        """RUB spent today (UTC) across all rows in this log."""
        today_start = (
            datetime.now(UTC)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        with sqlite3.connect(str(self._spend_log)) as conn:
            (total,) = conn.execute(
                "SELECT COALESCE(SUM(cost_rub), 0) FROM llm_spend WHERE timestamp >= ?",
                (today_start,),
            ).fetchone()
        return float(total)

    def estimate_cost_rub(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Estimate RUB cost for a hypothetical call. 0 for unknown models."""
        p = self._pricing.get(model)
        if p is None:
            return 0.0
        return tokens_in / 1000 * p.rub_per_1k_in + tokens_out / 1000 * p.rub_per_1k_out

    def _ensure_budget(self) -> None:
        spent = self.check_today_spend()
        if spent >= self._budget:
            raise BudgetExceeded(
                f"Daily budget exceeded: {spent:.2f} >= {self._budget:.2f} RUB. "
                "Refusing further LLM calls today."
            )

    def _record(self, model: str, tokens_in: int, tokens_out: int, cost_rub: float) -> None:
        ts = datetime.now(UTC).isoformat()
        with sqlite3.connect(str(self._spend_log)) as conn:
            conn.execute(
                "INSERT INTO llm_spend(timestamp, project, model, tokens_in, tokens_out, cost_rub) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (ts, self._project, model, tokens_in, tokens_out, cost_rub),
            )

    def _cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        if model not in self._pricing:
            self._log.warning(f"No pricing entry for {model!r}; recording cost_rub=0")
            return 0.0
        return self.estimate_cost_rub(model, tokens_in, tokens_out)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> CallResult:
        """Single chat completion. Routes to Anthropic native for Claude models,
        OpenRouter for everything else.

        ``messages`` follows OpenAI shape: list of ``{"role": ..., "content": ...}``
        with roles ``"system"``, ``"user"``, ``"assistant"``. For Claude, system
        messages are extracted into the Anthropic ``system`` parameter
        automatically.

        ``response_format`` is forwarded only for OpenAI/OpenRouter — Anthropic
        ignores it (use prompt instructions for JSON output instead).
        """
        self._ensure_budget()
        if is_anthropic_model(model):
            return self._chat_anthropic(messages, model, max_tokens, temperature)
        return self._chat_openrouter(messages, model, max_tokens, temperature, response_format)

    def _chat_openrouter(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int | None,
        temperature: float | None,
        response_format: dict[str, Any] | None,
    ) -> CallResult:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format
        response = self._openrouter_client.chat.completions.create(**kwargs)
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        cost_rub = self._cost(model, tokens_in, tokens_out)
        self._record(model, tokens_in, tokens_out, cost_rub)
        return CallResult(
            text=response.choices[0].message.content or "",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_rub=cost_rub,
            model=model,
        )

    def _chat_anthropic(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int | None,
        temperature: float | None,
    ) -> CallResult:
        # Anthropic API keeps system prompts as a separate top-level field, not
        # mixed into messages. Extract any "system" entries and concatenate.
        system_parts: list[str] = []
        chat_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(str(m.get("content", "")))
            else:
                chat_messages.append(m)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_tokens if max_tokens is not None else 1024,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = self._anthropic_client.messages.create(**kwargs)
        # Anthropic returns content blocks; concatenate text blocks.
        text_parts = [
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ]
        usage = response.usage
        tokens_in = usage.input_tokens
        tokens_out = usage.output_tokens
        cost_rub = self._cost(model, tokens_in, tokens_out)
        self._record(model, tokens_in, tokens_out, cost_rub)
        return CallResult(
            text="".join(text_parts),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_rub=cost_rub,
            model=model,
        )

    def embed(
        self,
        texts: list[str],
        *,
        model: str = "text-embedding-3-large",
    ) -> list[list[float]]:
        """Batch embeddings via OpenAI direct. Budget-guarded and spend-tracked."""
        self._ensure_budget()
        response = self._openai_client.embeddings.create(model=model, input=texts)
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        cost_rub = self._cost(model, tokens_in, 0)
        self._record(model, tokens_in, 0, cost_rub)
        return [item.embedding for item in response.data]

    def close(self) -> None:
        self._openrouter_client.close()
        self._openai_client.close()
        self._anthropic_client.close()

    def __enter__(self) -> LLM:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
