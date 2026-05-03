"""Unit tests for LLM wrapper. No network calls — those gated by env in test_llm_live."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stream_utils import LLM, BudgetExceeded, CallResult, ConfigError, ModelPricing


def _make_llm(tmp_path: Path, **overrides: object) -> LLM:
    kwargs: dict[str, object] = {
        "proxyapi_key": "sk-test-fake",
        "spend_log_path": tmp_path / "cache.db",
        "project_tag": "unit-test",
        "daily_budget_rub": 100.0,
    }
    kwargs.update(overrides)
    return LLM(**kwargs)  # type: ignore[arg-type]


def test_init_creates_table(tmp_path: Path) -> None:
    _make_llm(tmp_path)
    db_path = tmp_path / "cache.db"
    assert db_path.is_file()
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_spend'"
        )
        assert cursor.fetchone() is not None


def test_init_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "cache.db"
    _make_llm(tmp_path, spend_log_path=nested)
    assert nested.is_file()


def test_init_rejects_empty_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="proxyapi_key"):
        _make_llm(tmp_path, proxyapi_key="")


def test_init_rejects_empty_project_tag(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="project_tag"):
        _make_llm(tmp_path, project_tag="")


def test_init_rejects_nonpositive_budget(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="daily_budget_rub"):
        _make_llm(tmp_path, daily_budget_rub=0)
    with pytest.raises(ConfigError, match="daily_budget_rub"):
        _make_llm(tmp_path, daily_budget_rub=-5)


def test_estimate_cost_known_model(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    # claude-sonnet-4-6: 0.40 in, 2.00 out per 1k
    cost = llm.estimate_cost_rub("claude-sonnet-4-6", tokens_in=1000, tokens_out=500)
    assert cost == pytest.approx(0.40 + 1.00, rel=1e-6)


def test_estimate_cost_unknown_model_returns_zero(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    assert llm.estimate_cost_rub("never-existed-9000", 1000, 500) == 0.0


def test_estimate_cost_zero_tokens(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    assert llm.estimate_cost_rub("claude-sonnet-4-6", 0, 0) == 0.0


def test_pricing_override(tmp_path: Path) -> None:
    custom = {"my-model": ModelPricing(rub_per_1k_in=10.0, rub_per_1k_out=20.0)}
    llm = _make_llm(tmp_path, pricing=custom)
    assert llm.estimate_cost_rub("my-model", 1000, 1000) == pytest.approx(30.0)
    # Built-in models still resolve.
    assert llm.estimate_cost_rub("claude-sonnet-4-6", 1000, 0) == pytest.approx(0.40)


def test_pricing_override_replaces_default(tmp_path: Path) -> None:
    custom = {"claude-sonnet-4-6": ModelPricing(rub_per_1k_in=100.0, rub_per_1k_out=200.0)}
    llm = _make_llm(tmp_path, pricing=custom)
    assert llm.estimate_cost_rub("claude-sonnet-4-6", 1000, 0) == pytest.approx(100.0)


def test_check_today_spend_empty(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    assert llm.check_today_spend() == 0.0


def test_check_today_spend_aggregates(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    # Insert manual rows.
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(str(tmp_path / "cache.db")) as conn:
        conn.execute(
            "INSERT INTO llm_spend VALUES(?, 'p', 'm', 100, 50, 1.5)",
            (now,),
        )
        conn.execute(
            "INSERT INTO llm_spend VALUES(?, 'p', 'm', 100, 50, 2.5)",
            (now,),
        )
    assert llm.check_today_spend() == pytest.approx(4.0)


def test_check_today_spend_excludes_yesterday(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    yesterday = (datetime.now(UTC) - timedelta(days=1, hours=2)).isoformat()
    today = datetime.now(UTC).isoformat()
    with sqlite3.connect(str(tmp_path / "cache.db")) as conn:
        conn.execute(
            "INSERT INTO llm_spend VALUES(?, 'p', 'm', 100, 50, 99.0)",
            (yesterday,),
        )
        conn.execute(
            "INSERT INTO llm_spend VALUES(?, 'p', 'm', 100, 50, 1.0)",
            (today,),
        )
    assert llm.check_today_spend() == pytest.approx(1.0)


def test_ensure_budget_raises_when_exceeded(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path, daily_budget_rub=10.0)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(str(tmp_path / "cache.db")) as conn:
        conn.execute(
            "INSERT INTO llm_spend VALUES(?, 'p', 'm', 1000, 500, 15.0)",
            (now,),
        )
    with pytest.raises(BudgetExceeded, match=r"15\.00 >= 10\.00"):
        llm._ensure_budget()


def test_ensure_budget_at_exact_threshold_raises(tmp_path: Path) -> None:
    """Spending == budget should refuse — the budget is a hard cap."""
    llm = _make_llm(tmp_path, daily_budget_rub=10.0)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(str(tmp_path / "cache.db")) as conn:
        conn.execute(
            "INSERT INTO llm_spend VALUES(?, 'p', 'm', 1000, 500, 10.0)",
            (now,),
        )
    with pytest.raises(BudgetExceeded):
        llm._ensure_budget()


def test_ensure_budget_below_threshold_passes(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path, daily_budget_rub=10.0)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(str(tmp_path / "cache.db")) as conn:
        conn.execute(
            "INSERT INTO llm_spend VALUES(?, 'p', 'm', 1000, 500, 9.99)",
            (now,),
        )
    llm._ensure_budget()  # must not raise


def test_record_writes_row(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    llm._record("test-model", tokens_in=100, tokens_out=50, cost_rub=1.23)
    with sqlite3.connect(str(tmp_path / "cache.db")) as conn:
        rows = conn.execute(
            "SELECT project, model, tokens_in, tokens_out, cost_rub FROM llm_spend"
        ).fetchall()
    assert rows == [("unit-test", "test-model", 100, 50, 1.23)]


def test_call_result_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    r = CallResult(text="hi", tokens_in=1, tokens_out=2, cost_rub=0.0, model="m")
    with pytest.raises(FrozenInstanceError):
        r.text = "ho"  # type: ignore[misc]


def test_default_pricing_covers_documented_models(tmp_path: Path) -> None:
    """Smoke check that the model names actually resolved on ProxyAPI are priced.
    Covers Claude (anthropic-native), DeepSeek (openrouter), GPT, embeddings."""
    llm = _make_llm(tmp_path)
    for name in [
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
        "deepseek/deepseek-chat-v3.1",
        "deepseek/deepseek-r1",
        "gpt-4o-mini",
        "text-embedding-3-large",
    ]:
        assert llm.estimate_cost_rub(name, 1000, 0) > 0, f"no pricing for {name}"


def test_is_anthropic_routing() -> None:
    """The routing heuristic must catch Claude models and not non-Claude ones."""
    from stream_utils.core.llm import is_anthropic_model

    assert is_anthropic_model("claude-sonnet-4-6")
    assert is_anthropic_model("claude-haiku-4-5-20251001")
    assert is_anthropic_model("claude-opus-4-7")
    assert not is_anthropic_model("deepseek/deepseek-chat-v3.1")
    assert not is_anthropic_model("gpt-4o-mini")
    assert not is_anthropic_model("openai/gpt-4o")
    assert not is_anthropic_model("text-embedding-3-large")


def test_close_is_idempotent(tmp_path: Path) -> None:
    llm = _make_llm(tmp_path)
    llm.close()
    llm.close()  # must not raise


def test_context_manager(tmp_path: Path) -> None:
    with _make_llm(tmp_path) as llm:
        assert llm.project_tag == "unit-test"
