"""stream-utils: shared helpers for the streamer-tools sibling projects."""

from stream_utils.core.cache import Cache
from stream_utils.core.errors import (
    BudgetExceeded,
    CacheError,
    ConfigError,
    StreamUtilsError,
)
from stream_utils.core.llm import LLM, CallResult, ModelPricing
from stream_utils.core.paths import out_dir, xdg_cache, xdg_data, xdg_state

__version__ = "0.2.0"

__all__ = [
    "LLM",
    "BudgetExceeded",
    "Cache",
    "CacheError",
    "CallResult",
    "ConfigError",
    "ModelPricing",
    "StreamUtilsError",
    "__version__",
    "out_dir",
    "xdg_cache",
    "xdg_data",
    "xdg_state",
]
