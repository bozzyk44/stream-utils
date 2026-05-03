"""stream-utils: shared helpers for the streamer-tools sibling projects."""

from stream_utils.core.cache import Cache
from stream_utils.core.errors import (
    BudgetExceeded,
    CacheError,
    ConfigError,
    StreamUtilsError,
)
from stream_utils.core.ffmpeg import (
    FFmpegError,
    SubtitleStyle,
    cut_vertical,
    ffmpeg_available,
    segments_to_srt,
    write_srt,
)
from stream_utils.core.llm import LLM, CallResult, ModelPricing
from stream_utils.core.paths import out_dir, xdg_cache, xdg_data, xdg_state
from stream_utils.core.transcribe import Segment, transcribe

__version__ = "0.4.0"

__all__ = [
    "LLM",
    "BudgetExceeded",
    "Cache",
    "CacheError",
    "CallResult",
    "ConfigError",
    "FFmpegError",
    "ModelPricing",
    "Segment",
    "StreamUtilsError",
    "SubtitleStyle",
    "__version__",
    "cut_vertical",
    "ffmpeg_available",
    "out_dir",
    "segments_to_srt",
    "transcribe",
    "write_srt",
    "xdg_cache",
    "xdg_data",
    "xdg_state",
]
