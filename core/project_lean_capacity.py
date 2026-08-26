"""Provide opt-in bounded Lean-heavy capacity for one project."""

from __future__ import annotations

import os
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from core.filesystem import ensure_directory

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


PROJECT_LEAN_CAPACITY_ENV = "LEANFLOW_PROJECT_LEAN_CAPACITY"
# This is a guardrail against accidental unbounded fan-out, not an operating
# recommendation.  Large verification hosts commonly have more than eight
# useful Lean slots; callers still opt in explicitly and the default remains 1.
MAX_PROJECT_LEAN_CAPACITY = 64
_POLL_INTERVAL_S = 0.05
_SEMAPHORE_GUARD = threading.Lock()
_SEMAPHORES: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_CURRENT_LEASE: ContextVar[ProjectLeanCapacityLease | None] = ContextVar(
    "leanflow_project_lean_capacity_lease", default=None
)


def project_lean_capacity() -> int:
    """Return the explicitly configured project Lean slot count."""
    raw = str(os.getenv(PROJECT_LEAN_CAPACITY_ENV, "1") or "1").strip()
    try:
        return max(1, min(MAX_PROJECT_LEAN_CAPACITY, int(raw)))
    except ValueError:
        return 1


def _local_semaphore(root: Path, capacity: int) -> threading.BoundedSemaphore:
    key = (str(root), capacity)
    with _SEMAPHORE_GUARD:
        semaphore = _SEMAPHORES.get(key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(capacity)
            _SEMAPHORES[key] = semaphore
        return semaphore


@dataclass
class ProjectLeanCapacityLease:
    """Own one crash-released cross-process Lean-heavy slot."""

    slot: int
    lock_path: str
    waited_s: float
    _file: IO[bytes]
    _semaphore: threading.BoundedSemaphore
    _references: int = 1
    _released: bool = False
    _retained: bool = False

    def retain(self) -> ProjectLeanCapacityLease:
        """Share this context's slot with a nested Lean operation."""
        if self._released:
            raise RuntimeError("cannot retain a released project Lean slot")
        self._references += 1
        return self

    def retain_until_process_exit(self) -> None:
        """Keep the OS-owned slot when a resident Lean service cannot close."""
        self._retained = True

    def release(self) -> None:
        """Release the final reference unless the slot was made sticky."""
        if self._released:
            return
        self._references -= 1
        if self._references > 0 or self._retained:
            return
        self._released = True
        try:
            if fcntl is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._semaphore.release()
            if _CURRENT_LEASE.get() is self:
                _CURRENT_LEASE.set(None)


def acquire_project_lean_capacity(root: Path) -> ProjectLeanCapacityLease:
    """Wait for one configured project slot, retaining nested ownership."""
    existing = _CURRENT_LEASE.get()
    if existing is not None and not existing._released:
        return existing.retain()
    capacity = project_lean_capacity()
    semaphore = _local_semaphore(root, capacity)
    started = time.monotonic()
    semaphore.acquire()
    try:
        slot_root = ensure_directory(root / ".leanflow" / "resource-gates" / "lean-capacity")
        while True:
            for slot in range(capacity):
                path = slot_root / f"slot-{slot}.lock"
                handle = path.open("a+b")
                if fcntl is None:
                    lease = ProjectLeanCapacityLease(
                        slot, str(path), time.monotonic() - started, handle, semaphore
                    )
                    _CURRENT_LEASE.set(lease)
                    return lease
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    continue
                lease = ProjectLeanCapacityLease(
                    slot, str(path), time.monotonic() - started, handle, semaphore
                )
                _CURRENT_LEASE.set(lease)
                return lease
            time.sleep(_POLL_INTERVAL_S)
    except BaseException:
        semaphore.release()
        raise
