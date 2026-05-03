"""Tests for the transcribe wrapper.

Cache-key determinism + Segment frozenness can be verified without invoking
faster-whisper. Real transcription is exercised in a separate live smoke
script (requires faster-whisper model download + audio file)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from stream_utils import Segment
from stream_utils.core.transcribe import cache_key


def _write_audio(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_segment_frozen() -> None:
    s = Segment(start=0.0, end=1.0, text="hi")
    with pytest.raises(FrozenInstanceError):
        s.text = "ho"  # type: ignore[misc]


def test_cache_key_deterministic(tmp_path: Path) -> None:
    f = _write_audio(tmp_path / "a.mp4", b"hello world")
    k1 = cache_key(f, "large-v3", "ru", True)
    k2 = cache_key(f, "large-v3", "ru", True)
    assert k1 == k2


def test_cache_key_changes_with_model(tmp_path: Path) -> None:
    f = _write_audio(tmp_path / "a.mp4", b"hello")
    a = cache_key(f, "large-v3", "ru", True)
    b = cache_key(f, "medium", "ru", True)
    assert a != b


def test_cache_key_changes_with_language(tmp_path: Path) -> None:
    f = _write_audio(tmp_path / "a.mp4", b"hello")
    a = cache_key(f, "large-v3", "ru", True)
    b = cache_key(f, "large-v3", "en", True)
    c = cache_key(f, "large-v3", None, True)
    assert a != b != c != a


def test_cache_key_changes_with_vad(tmp_path: Path) -> None:
    f = _write_audio(tmp_path / "a.mp4", b"hello")
    a = cache_key(f, "large-v3", "ru", True)
    b = cache_key(f, "large-v3", "ru", False)
    assert a != b


def test_cache_key_changes_with_content(tmp_path: Path) -> None:
    a = _write_audio(tmp_path / "a.mp4", b"hello")
    b = _write_audio(tmp_path / "b.mp4", b"goodbye")
    assert cache_key(a, "large-v3", "ru", True) != cache_key(b, "large-v3", "ru", True)


def test_cache_key_path_independent(tmp_path: Path) -> None:
    """Same content under different paths → same key (we hash content, not path)."""
    a = _write_audio(tmp_path / "a.mp4", b"identical")
    b = _write_audio(tmp_path / "subdir/b.mp4", b"identical")
    assert cache_key(a, "large-v3", "ru", True) == cache_key(b, "large-v3", "ru", True)


def test_cache_key_accepts_string_path(tmp_path: Path) -> None:
    f = _write_audio(tmp_path / "a.mp4", b"hello")
    assert cache_key(str(f), "large-v3", "ru", True) == cache_key(f, "large-v3", "ru", True)


def test_transcribe_missing_file_raises(tmp_path: Path) -> None:
    from stream_utils import transcribe

    with pytest.raises(FileNotFoundError):
        transcribe(tmp_path / "nonexistent.mp4")
