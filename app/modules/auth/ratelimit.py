import math
import threading
import time

MAX_FAILURES = 10
WINDOW_SECONDS = 300

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _prune(key: str, now: float) -> list[float]:
    hits = [at for at in _failures.get(key, ()) if now - at < WINDOW_SECONDS]
    if hits:
        _failures[key] = hits
    else:
        _failures.pop(key, None)
    return hits


def too_many_attempts(key: str) -> int | None:
    now = time.monotonic()
    with _lock:
        hits = _prune(key, now)
        if len(hits) < MAX_FAILURES:
            return None
        return math.ceil(WINDOW_SECONDS - (now - hits[0]))


def register_failure(key: str) -> None:
    now = time.monotonic()
    with _lock:
        _failures[key] = _prune(key, now) + [now]


def clear_failures(key: str) -> None:
    with _lock:
        _failures.pop(key, None)


def reset_state() -> None:
    with _lock:
        _failures.clear()
