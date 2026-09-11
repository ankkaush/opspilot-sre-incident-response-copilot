import hmac
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from opspilot.config import Settings, get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Per-identity request timestamps, in-memory. Deliberately not backed by
# Redis or anything shared across processes — this is a single-instance
# deployment (see blueprint pushback #2), so a per-process bucket is the
# right amount of complexity. Revisit if this ever runs as multiple workers.
_request_log: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limit_state() -> None:
    """Test-only: clear all rate-limit buckets so tests don't interfere with
    each other's counts."""
    _request_log.clear()


def _check_rate_limit(identity: str, settings: Settings) -> None:
    now = time.monotonic()
    window_start = now - settings.rate_limit_window_seconds
    bucket = _request_log[identity]
    while bucket and bucket[0] < window_start:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again shortly.",
        )
    bucket.append(now)


def require_api_key(
    provided_key: str | None = Depends(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    """Deterministic authorization gate — every protected route depends on this.

    Constant-time comparison so response timing can't be used to guess the
    key. Also enforces a per-key rate limit, since both are properties of
    "who is calling this route," not separate concerns worth splitting into
    two dependencies that would each need to re-parse the header.
    """
    if provided_key is None or not hmac.compare_digest(provided_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key. Send it in the X-API-Key header.",
        )
    _check_rate_limit(provided_key, settings)
