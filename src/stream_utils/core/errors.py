"""Custom exceptions.

Every error stream-utils can raise is either a :class:`StreamUtilsError` or a
subclass of one. Consumers can ``except StreamUtilsError:`` and catch all of
the library's problems in one branch.
"""


class StreamUtilsError(Exception):
    """Base class for all stream-utils errors."""


class ConfigError(StreamUtilsError):
    """Required configuration is missing or invalid."""


class BudgetExceeded(StreamUtilsError):
    """LLM daily budget exceeded — refuse to make further calls."""


class CacheError(StreamUtilsError):
    """SQLite cache layer failed (corrupt value, encoding error, etc.)."""
