"""Core helpers — the public library surface."""

from stream_utils.core.cache import Cache
from stream_utils.core.errors import (
    BudgetExceeded,
    CacheError,
    ConfigError,
    StreamUtilsError,
)
from stream_utils.core.llm import LLM, CallResult, ModelPricing
from stream_utils.core.paths import out_dir, xdg_cache, xdg_data, xdg_state
from stream_utils.core.transcribe import Segment, transcribe

__all__ = [
    "LLM",
    "BudgetExceeded",
    "Cache",
    "CacheError",
    "CallResult",
    "ConfigError",
    "ModelPricing",
    "Segment",
    "StreamUtilsError",
    "out_dir",
    "transcribe",
    "xdg_cache",
    "xdg_data",
    "xdg_state",
]
