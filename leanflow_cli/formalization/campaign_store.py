"""Process-safe transactions for a formalization campaign ledger."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from core.utils import atomic_json_write

try:  # pragma: no cover - Windows uses the in-process guard only.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


T = TypeVar("T")
_PROCESS_GUARD = threading.RLock()


@contextmanager
def campaign_file_lock(campaign_path: str | Path):
    """Serialize campaign transactions across threads and POSIX processes."""
    path = Path(campaign_path).expanduser().resolve()
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_GUARD:
        with lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_campaign(campaign_path: str | Path) -> dict[str, Any]:
    path = Path(campaign_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("campaign JSON must contain an object")
    return payload


def update_campaign_file(
    campaign_path: str | Path,
    transform: Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], T]],
) -> T:
    """Read, transform, and atomically replace a campaign under one lock."""
    path = Path(campaign_path).expanduser().resolve()
    with campaign_file_lock(path):
        current = read_campaign(path)
        updated, result = transform(current)
        atomic_json_write(path, dict(updated))
        return result
