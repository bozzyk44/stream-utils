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
import os
import sys
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
_dll_dirs_registered = False


def _register_nvidia_dll_dirs() -> None:
    """On Windows, make CTranslate2's CUDA dependencies (cuBLAS / cuDNN /
    cudart) loadable from pip-installed ``nvidia-*`` wheels.

    Two-stage workaround for the fact that CTranslate2 calls LoadLibrary
    lazily and doesn't honor ``os.add_dll_directory()`` for that path:

    1. Register each ``site-packages/nvidia/*/bin`` directory with the
       Python-level DLL loader (helps any later ``ctypes.CDLL`` calls).
    2. **Eagerly load** the critical DLLs via ``ctypes.CDLL`` so they
       sit in the process address space. Subsequent ``LoadLibrary`` calls
       from native code return the already-loaded handle by name, no
       search needed.

    No-op on non-Windows and after the first call.
    """
    global _dll_dirs_registered
    if _dll_dirs_registered or sys.platform != "win32":
        _dll_dirs_registered = True
        return
    log = logger.bind(module="stream_utils.transcribe")
    import ctypes
    import site

    sp_dirs = list(site.getsitepackages())
    if hasattr(site, "getusersitepackages"):
        sp_dirs.append(site.getusersitepackages())

    bin_dirs: list[Path] = []
    for sp in sp_dirs:
        nvidia_root = Path(sp) / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for sub in nvidia_root.iterdir():
            bin_dir = sub / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                bin_dirs.append(bin_dir)
                log.debug(f"registered DLL dir: {bin_dir}")

    # Pre-load critical DLLs by absolute path so CTranslate2's lazy
    # LoadLibrary calls find them already in memory.
    for bin_dir in bin_dirs:
        for dll in bin_dir.glob("*.dll"):
            try:
                ctypes.CDLL(str(dll))
            except OSError as e:
                log.debug(f"pre-load skipped {dll.name}: {e}")
    _dll_dirs_registered = True


def _load_model(model_size: str, device: str, compute_type: str) -> WhisperModel:
    key = (model_size, device, compute_type)
    if key not in _loaded_models:
        _register_nvidia_dll_dirs()
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
