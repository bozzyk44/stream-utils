"""Twitch Helix API client.

Sync HTTP via ``httpx``. App-token auth with auto-refresh: the client gets a
client-credentials token from ``id.twitch.tv``, refreshes 60 seconds before
expiry, and retries once on a mid-flight 401. Optional :class:`Cache`
integration: every successful read response is cached by URL + params, so
repeated lookups don't burn rate limit (default TTL 1 hour, overridable).

Public surface covers the methods the four consuming projects need:

- :meth:`HelixClient.get_users` — collab-hunter, chat-digest, clip-rater
- :meth:`HelixClient.get_channel` — collab-hunter (description + language)
- :meth:`HelixClient.get_videos` — collab-hunter, mention-watcher, clip-rater
- :meth:`HelixClient.get_streams` — collab-hunter (candidate pool by game),
  chat-digest (live status)
- :meth:`HelixClient.get_clips` — clip-rater
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from types import TracebackType
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from stream_utils.core.cache import Cache
from stream_utils.core.errors import ConfigError, StreamUtilsError


class TwitchAPIError(StreamUtilsError):
    """Helix returned an error or auth failed."""


HELIX_BASE_URL = "https://api.twitch.tv/helix"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class TwitchUser(BaseModel):
    """Twitch user / channel owner."""

    model_config = ConfigDict(extra="ignore")

    id: str
    login: str
    display_name: str
    description: str = ""
    profile_image_url: str = ""
    broadcaster_type: str = ""  # "" | "affiliate" | "partner"
    type: str = ""  # "" | "admin" | "global_mod" | "staff"
    view_count: int = 0
    created_at: datetime


class TwitchChannel(BaseModel):
    """Channel metadata: title, language, current game."""

    model_config = ConfigDict(extra="ignore")

    broadcaster_id: str
    broadcaster_login: str
    broadcaster_name: str
    broadcaster_language: str = ""
    game_id: str = ""
    game_name: str = ""
    title: str = ""
    delay: int = 0
    tags: list[str] = Field(default_factory=list)


class TwitchVideo(BaseModel):
    """Past broadcast / VOD / highlight."""

    model_config = ConfigDict(extra="ignore")

    id: str
    user_id: str
    user_login: str
    user_name: str
    title: str = ""
    description: str = ""
    created_at: datetime
    published_at: datetime
    url: str
    thumbnail_url: str = ""
    viewable: str = "public"
    view_count: int = 0
    language: str = ""
    type: str = "archive"  # "archive" | "upload" | "highlight"
    duration: str = ""  # e.g. "3h21m33s"


class TwitchStream(BaseModel):
    """Currently live stream snapshot."""

    model_config = ConfigDict(extra="ignore")

    id: str
    user_id: str
    user_login: str
    user_name: str
    game_id: str = ""
    game_name: str = ""
    type: str = ""  # "live" or ""
    title: str = ""
    viewer_count: int = 0
    started_at: datetime
    language: str = ""
    thumbnail_url: str = ""
    tags: list[str] = Field(default_factory=list)


class TwitchClip(BaseModel):
    """Twitch clip (viewer-created short)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    url: str
    embed_url: str = ""
    broadcaster_id: str
    broadcaster_name: str = ""
    creator_id: str = ""
    creator_name: str = ""
    video_id: str = ""  # may be empty if VOD deleted
    game_id: str = ""
    language: str = ""
    title: str = ""
    view_count: int = 0
    created_at: datetime
    thumbnail_url: str = ""
    duration: float = 0.0
    vod_offset: int | None = None


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _cache_key(path: str, params: list[tuple[str, str]]) -> str:
    """Stable cache key for a Helix call. Order-independent on params."""
    sorted_params = sorted(params)
    return path + "?" + "&".join(f"{k}={v}" for k, v in sorted_params)


class HelixClient:
    """Twitch Helix API client.

    App-token auth (client credentials) with auto-refresh. Sync HTTP. Optional
    cache. All methods raise :class:`TwitchAPIError` on Helix 4xx/5xx, with
    the response body included in the message.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        cache: Cache | None = None,
        cache_namespace: str = "twitch",
        default_ttl: float = 3600.0,
        timeout: float = 30.0,
        base_url: str = HELIX_BASE_URL,
        token_url: str = TWITCH_TOKEN_URL,
    ) -> None:
        if not client_id:
            raise ConfigError("client_id is required")
        if not client_secret:
            raise ConfigError("client_secret is required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._cache = cache
        self._cache_ns = cache_namespace
        self._default_ttl = default_ttl
        self._base_url = base_url.rstrip("/")
        self._token_url = token_url
        self._http = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._log = logger.bind(module="stream_utils.twitch")

    # ----- Auth ------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token is not None and time.time() < self._token_expires_at:
            return self._token
        r = self._http.post(
            self._token_url,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        )
        if r.status_code != 200:
            raise TwitchAPIError(
                f"Token request failed: {r.status_code} {r.text[:300]}"
            )
        data = r.json()
        self._token = str(data["access_token"])
        # Refresh 60 seconds before actual expiry.
        self._token_expires_at = time.time() + float(data.get("expires_in", 3600)) - 60
        return self._token

    # ----- HTTP plumbing ---------------------------------------------------

    def _request(
        self,
        path: str,
        params: list[tuple[str, str]],
        *,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        """Single GET to Helix, with cache and 401-retry. Returns parsed JSON."""
        cache_ttl = self._default_ttl if ttl is None else ttl
        cache_key = _cache_key(path, params)
        if self._cache is not None and cache_ttl > 0:
            cached = self._cache.get(self._cache_ns, cache_key)
            if cached is not None:
                return cached  # type: ignore[no-any-return]

        token = self._get_token()
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {token}", "Client-Id": self._client_id}
        # httpx accepts list[tuple[str, Primitive]]; we always pass strings.
        httpx_params: Any = params
        r = self._http.get(url, headers=headers, params=httpx_params)
        if r.status_code == 401:
            self._log.info("Helix 401, refreshing app token and retrying once")
            self._token = None
            token = self._get_token()
            headers["Authorization"] = f"Bearer {token}"
            r = self._http.get(url, headers=headers, params=httpx_params)
        if r.status_code != 200:
            raise TwitchAPIError(
                f"Helix {path} returned {r.status_code}: {r.text[:300]}"
            )
        try:
            data: dict[str, Any] = r.json()
        except json.JSONDecodeError as e:
            raise TwitchAPIError(f"Invalid JSON from Helix {path}: {e}") from e

        if self._cache is not None and cache_ttl > 0:
            self._cache.set(self._cache_ns, cache_key, data, ttl_seconds=cache_ttl)
        return data

    def _paginated(
        self,
        path: str,
        base_params: list[tuple[str, str]],
        *,
        limit: int | None,
        page_size: int = 100,
        ttl: float | None = None,
    ) -> list[dict[str, Any]]:
        """Auto-paginate a Helix endpoint. ``limit=None`` means all pages."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = list(base_params)
            params.append(("first", str(page_size)))
            if cursor is not None:
                params.append(("after", cursor))
            data = self._request(path, params, ttl=ttl)
            out.extend(data.get("data", []))
            if limit is not None and len(out) >= limit:
                return out[:limit]
            cursor = data.get("pagination", {}).get("cursor")
            if not cursor:
                return out

    # ----- Public methods --------------------------------------------------

    def get_users(
        self,
        *,
        logins: list[str] | None = None,
        ids: list[str] | None = None,
    ) -> list[TwitchUser]:
        """Look up users by login and/or id.

        Helix accepts up to 100 ids+logins per call combined; this method
        chunks transparently. The response order is not guaranteed.
        """
        if not logins and not ids:
            return []
        result: list[TwitchUser] = []
        # Chunk separately for clarity. The combined Helix limit of 100 still
        # holds because each chunk is <=100 of either kind.
        if logins:
            for chunk in _chunked(list(logins), 100):
                params = [("login", login) for login in chunk]
                data = self._request("/users", params)
                result.extend(TwitchUser.model_validate(d) for d in data.get("data", []))
        if ids:
            for chunk in _chunked(list(ids), 100):
                params = [("id", uid) for uid in chunk]
                data = self._request("/users", params)
                result.extend(TwitchUser.model_validate(d) for d in data.get("data", []))
        return result

    def get_channel(self, broadcaster_id: str) -> TwitchChannel | None:
        """Get channel info (title, language, current game). ``None`` if not found."""
        data = self._request("/channels", [("broadcaster_id", broadcaster_id)])
        rows = data.get("data", [])
        if not rows:
            return None
        return TwitchChannel.model_validate(rows[0])

    def get_videos(
        self,
        *,
        user_id: str,
        video_type: str = "archive",
        limit: int = 20,
    ) -> list[TwitchVideo]:
        """Recent videos for a broadcaster. Default ``archive`` (past broadcasts)."""
        params = [("user_id", user_id), ("type", video_type)]
        rows = self._paginated("/videos", params, limit=limit)
        return [TwitchVideo.model_validate(r) for r in rows]

    def get_video_by_id(self, video_id: str) -> TwitchVideo | None:
        """Look up a single VOD by ID. ``None`` if not found / deleted."""
        data = self._request("/videos", [("id", video_id)])
        rows = data.get("data", [])
        if not rows:
            return None
        return TwitchVideo.model_validate(rows[0])

    def get_streams(
        self,
        *,
        game_ids: list[str] | None = None,
        user_logins: list[str] | None = None,
        user_ids: list[str] | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[TwitchStream]:
        """Live streams matching any of the given filters."""
        params: list[tuple[str, str]] = []
        for gid in game_ids or []:
            params.append(("game_id", gid))
        for login in user_logins or []:
            params.append(("user_login", login))
        for uid in user_ids or []:
            params.append(("user_id", uid))
        if language:
            params.append(("language", language))
        # Live data: short cache TTL.
        rows = self._paginated("/streams", params, limit=limit, ttl=60.0)
        return [TwitchStream.model_validate(r) for r in rows]

    def get_clips(
        self,
        *,
        broadcaster_id: str | None = None,
        game_id: str | None = None,
        clip_ids: list[str] | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        limit: int = 100,
    ) -> list[TwitchClip]:
        """Clips matching any of the given filters.

        Helix requires exactly one of ``broadcaster_id``, ``game_id``, or
        ``clip_ids``. ``started_at``/``ended_at`` are ISO 8601 timestamps;
        clips outside the window are filtered server-side.
        """
        targets = sum(x is not None for x in (broadcaster_id, game_id, clip_ids))
        if targets != 1:
            raise ValueError(
                "exactly one of broadcaster_id, game_id, clip_ids must be given"
            )
        params: list[tuple[str, str]] = []
        if broadcaster_id is not None:
            params.append(("broadcaster_id", broadcaster_id))
        if game_id is not None:
            params.append(("game_id", game_id))
        for cid in clip_ids or []:
            params.append(("id", cid))
        if started_at is not None:
            params.append(("started_at", started_at.isoformat().replace("+00:00", "Z")))
        if ended_at is not None:
            params.append(("ended_at", ended_at.isoformat().replace("+00:00", "Z")))
        rows = self._paginated("/clips", params, limit=limit, ttl=300.0)
        return [TwitchClip.model_validate(r) for r in rows]

    # ----- Lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HelixClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = [
    "HELIX_BASE_URL",
    "TWITCH_TOKEN_URL",
    "HelixClient",
    "TwitchAPIError",
    "TwitchChannel",
    "TwitchClip",
    "TwitchStream",
    "TwitchUser",
    "TwitchVideo",
]
