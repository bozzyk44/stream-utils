"""faster-whisper wrapper.

Single function :func:`transcribe`: takes an audio/video file path, returns a
list of :class:`Segment` (start, end, text). Whisper models are expensive to
load (~5-10s, ~3 GB for ``large-v3``), so they're memoized per process by
``(model_size, device, compute_type)``.

Pass an optional :class:`stream_utils.Cache` to reuse transcripts between runs
on the same input — keyed by SHA-256 of the file contents plus the model and
language settings, so changes to either invalidate the cache.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from stream_utils.core.cache import Cache

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


@dataclass(frozen=True)
class Segment:
    """One contiguous transcribed chunk: start/end seconds, stripped text."""

    start: float
    end: float
    text: str


# Process-level memoization. Whisper models are heavy — loading them each
# call would dominate runtime. Multiple consumers in the same process with
# the same parameters share a model instance.
_loaded_models: dict[tuple[str, str, str], WhisperModel] = {}


def _load_model(model_size: str, device: str, compute_type: str) -> WhisperModel:
    key = (model_size, device, compute_type)
    if key not in _loaded_models:
        from faster_whisper import WhisperModel

        log = logger.bind(module="stream_utils.transcribe")
        log.info(
            f"Loading WhisperModel({model_size}, device={device}, "
            f"compute_type={compute_type})"
        )
        _loaded_models[key] = WhisperModel(
            model_size, device=device, compute_type=compute_type
        )
    return _loaded_models[key]


def _file_sha256(path: Path) -> str:
    """Streaming SHA-256 of file contents. 1 MB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(
    audio_path: Path | str,
    model_size: str,
    language: str | None,
    vad_filter: bool,
) -> str:
    """Deterministic cache key. Changes to any input invalidate the entry."""
    file_hash = _file_sha256(Path(audio_path))
    return f"{file_hash}|{model_size}|{language}|vad={vad_filter}"


def transcribe(
    audio_path: Path | str,
    *,
    model_size: str = "large-v3",
    language: str | None = "ru",
    device: str = "auto",
    compute_type: str = "default",
    vad_filter: bool = True,
    beam_size: int = 5,
    cache: Cache | None = None,
    cache_namespace: str = "transcribe",
) -> list[Segment]:
    """Transcribe an audio/video file into a list of :class:`Segment`.

    Defaults match the streamer-tooling consumers' needs: Russian language,
    large-v3 model, voice-activity filter on. Override per call.

    If ``cache`` is given, results are stored keyed by file-content hash plus
    settings — re-running with the same inputs is a no-op disk read.
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    if cache is not None:
        key = cache_key(path, model_size, language, vad_filter)
        cached = cache.get(cache_namespace, key)
        if cached is not None:
            return [Segment(**s) for s in cached]

    model = _load_model(model_size, device, compute_type)
    segments_iter, _info = model.transcribe(
        str(path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    segments = [
        Segment(start=float(s.start), end=float(s.end), text=s.text.strip())
        for s in segments_iter
    ]

    if cache is not None:
        cache.set(
            cache_namespace,
            cache_key(path, model_size, language, vad_filter),
            [asdict(s) for s in segments],
        )

    return segments


def clear_model_cache() -> None:
    """Drop all process-level memoized Whisper models. Mainly for tests."""
    _loaded_models.clear()


__all__ = ["Segment", "cache_key", "clear_model_cache", "transcribe"]
