"""Tests for HelixClient.

Pure-logic tests (model parsing, cache-key, chunking, validation) run
unconditionally. Live API tests require ``TWITCH_CLIENT_ID`` and
``TWITCH_CLIENT_SECRET`` env vars and are skipped otherwise.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stream_utils import (
    Cache,
    ConfigError,
    HelixClient,
    TwitchAPIError,
    TwitchClip,
    TwitchUser,
    TwitchVideo,
)
from stream_utils.core.twitch import _cache_key, _chunked

# ---- Pure logic ------------------------------------------------------------


def test_cache_key_order_independent() -> None:
    a = _cache_key("/users", [("login", "x"), ("login", "y")])
    b = _cache_key("/users", [("login", "y"), ("login", "x")])
    assert a == b


def test_cache_key_includes_path_and_params() -> None:
    k = _cache_key("/streams", [("game_id", "42"), ("language", "ru")])
    assert "/streams" in k
    assert "game_id=42" in k
    assert "language=ru" in k


def test_cache_key_distinguishes_paths() -> None:
    assert _cache_key("/a", [("k", "v")]) != _cache_key("/b", [("k", "v")])


def test_chunked_basic() -> None:
    assert _chunked(list("abcde"), 2) == [["a", "b"], ["c", "d"], ["e"]]


def test_chunked_exact_division() -> None:
    assert _chunked(list("abcd"), 2) == [["a", "b"], ["c", "d"]]


def test_chunked_size_larger_than_input() -> None:
    assert _chunked(list("ab"), 100) == [["a", "b"]]


def test_chunked_empty() -> None:
    assert _chunked([], 5) == []


# ---- Init validation -------------------------------------------------------


def test_init_rejects_empty_client_id() -> None:
    with pytest.raises(ConfigError, match="client_id"):
        HelixClient(client_id="", client_secret="x")


def test_init_rejects_empty_client_secret() -> None:
    with pytest.raises(ConfigError, match="client_secret"):
        HelixClient(client_id="x", client_secret="")


def test_get_users_empty_returns_empty() -> None:
    """Edge case: no logins, no ids → empty result, no HTTP call."""
    client = HelixClient(client_id="x", client_secret="y")
    assert client.get_users() == []
    client.close()


def test_get_clips_requires_one_target(tmp_path: Path) -> None:
    client = HelixClient(client_id="x", client_secret="y")
    with pytest.raises(ValueError, match="exactly one"):
        client.get_clips()
    with pytest.raises(ValueError, match="exactly one"):
        client.get_clips(broadcaster_id="1", game_id="2")
    client.close()


# ---- Pydantic model parsing ------------------------------------------------


def test_twitch_user_parses_minimal() -> None:
    u = TwitchUser.model_validate(
        {
            "id": "123",
            "login": "alice",
            "display_name": "Alice",
            "created_at": "2020-01-01T00:00:00Z",
        }
    )
    assert u.id == "123"
    assert u.login == "alice"
    assert u.broadcaster_type == ""  # default
    assert u.view_count == 0  # default


def test_twitch_user_ignores_unknown_fields() -> None:
    """Helix may add fields we don't model — extra='ignore' must hold."""
    u = TwitchUser.model_validate(
        {
            "id": "1",
            "login": "a",
            "display_name": "A",
            "created_at": "2020-01-01T00:00:00Z",
            "future_field_we_dont_know_about": "value",
        }
    )
    assert u.id == "1"


def test_twitch_video_parses() -> None:
    v = TwitchVideo.model_validate(
        {
            "id": "vid1",
            "user_id": "u1",
            "user_login": "alice",
            "user_name": "Alice",
            "title": "Stream",
            "created_at": "2026-05-03T10:00:00Z",
            "published_at": "2026-05-03T10:00:00Z",
            "url": "https://www.twitch.tv/videos/vid1",
            "duration": "3h21m33s",
        }
    )
    assert v.duration == "3h21m33s"
    assert v.type == "archive"  # default


def test_twitch_clip_parses() -> None:
    c = TwitchClip.model_validate(
        {
            "id": "AwkwardHelplessSalamanderSwiftRage",
            "url": "https://clips.twitch.tv/...",
            "embed_url": "https://clips.twitch.tv/embed?clip=...",
            "broadcaster_id": "1",
            "broadcaster_name": "alice",
            "creator_id": "2",
            "creator_name": "bob",
            "video_id": "vid1",
            "title": "lol",
            "view_count": 42,
            "created_at": "2026-05-03T10:00:00Z",
            "duration": 28.5,
            "vod_offset": 1234,
        }
    )
    assert c.duration == 28.5
    assert c.vod_offset == 1234


def test_twitch_clip_optional_vod_offset() -> None:
    """Clips with deleted source VOD have vod_offset=None."""
    c = TwitchClip.model_validate(
        {
            "id": "x",
            "url": "https://clips.twitch.tv/x",
            "broadcaster_id": "1",
            "created_at": "2026-05-03T10:00:00Z",
            "duration": 30.0,
            "vod_offset": None,
        }
    )
    assert c.vod_offset is None


# ---- Cache integration -----------------------------------------------------


def test_cache_uses_namespace(tmp_path: Path) -> None:
    """A custom cache_namespace must be respected on read/write paths."""
    cache = Cache(tmp_path / "c.db")
    client = HelixClient(
        client_id="x",
        client_secret="y",
        cache=cache,
        cache_namespace="custom",
    )
    # Pre-populate cache with the value we expect _request to return.
    cache.set("custom", "/users?login=alice", {"data": [{"id": "1"}]})
    # Now call _request — it should hit cache, not make HTTP call.
    result = client._request("/users", [("login", "alice")])
    assert result == {"data": [{"id": "1"}]}
    client.close()


def test_cache_ttl_zero_disables_cache(tmp_path: Path) -> None:
    """Calling with ttl=0 should bypass cache entirely (no read, no write)."""
    cache = Cache(tmp_path / "c.db")
    client = HelixClient(
        client_id="x",
        client_secret="y",
        cache=cache,
        default_ttl=0,
    )
    # Pre-populate cache.
    cache.set("twitch", "/users?login=alice", {"would-be-stale": True})
    # _request with ttl=0 should ignore the cache and try an HTTP call —
    # which will fail because we have no real token. We assert the failure
    # path proves cache was bypassed.
    with pytest.raises(TwitchAPIError):
        client._request("/users", [("login", "alice")], ttl=0)
    client.close()


# ---- Live smoke (gated by env) ---------------------------------------------

_TWITCH_LIVE_REASON = (
    "TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET env required for live test"
)


def _has_twitch_creds() -> bool:
    return bool(os.environ.get("TWITCH_CLIENT_ID")) and bool(
        os.environ.get("TWITCH_CLIENT_SECRET")
    )


@pytest.mark.skipif(not _has_twitch_creds(), reason=_TWITCH_LIVE_REASON)
def test_live_get_users(tmp_path: Path) -> None:
    """Resolve a known stable login (twitch own staff account) end-to-end."""
    with HelixClient(
        client_id=os.environ["TWITCH_CLIENT_ID"],
        client_secret=os.environ["TWITCH_CLIENT_SECRET"],
        cache=Cache(tmp_path / "c.db"),
    ) as client:
        users = client.get_users(logins=["twitch"])
    assert len(users) == 1
    assert users[0].login == "twitch"
    assert users[0].id  # populated


@pytest.mark.skipif(not _has_twitch_creds(), reason=_TWITCH_LIVE_REASON)
def test_live_get_streams_with_filter(tmp_path: Path) -> None:
    """Pull a few live RU streams. Doesn't assert content, just that the call
    succeeds and returns parseable models."""
    with HelixClient(
        client_id=os.environ["TWITCH_CLIENT_ID"],
        client_secret=os.environ["TWITCH_CLIENT_SECRET"],
        cache=Cache(tmp_path / "c.db"),
    ) as client:
        streams = client.get_streams(language="ru", limit=5)
    assert isinstance(streams, list)
    # Twitch-RU is busy; expect at least one live stream nearly always.
    if streams:
        assert streams[0].language == "ru"
        assert streams[0].started_at <= datetime.now(UTC)
