"""Verify explicit Lean slot capacity across independent Python processes."""

from __future__ import annotations

import multiprocessing
import queue
from pathlib import Path

import pytest


def _claim(root, events, release):
    from core.project_lean_capacity import acquire_project_lean_capacity

    try:
        with acquire_project_lean_capacity(Path(root), capacity=2):
            events.put("acquired")
            if not release.wait(10):
                raise TimeoutError("test did not release slot")
    except Exception as exc:
        events.put(f"error: {exc}")


def test_explicit_capacity_is_shared_across_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("LEANFLOW_PROJECT_LEAN_CAPACITY", "1")
    ctx = multiprocessing.get_context("spawn")
    events = ctx.Queue()
    release = ctx.Event()
    workers = [ctx.Process(target=_claim, args=(str(tmp_path), events, release)) for _ in range(3)]
    try:
        for worker in workers:
            worker.start()
        assert events.get(timeout=10) == "acquired"
        assert events.get(timeout=10) == "acquired", "explicit capacity=2 must override global 1"
        with pytest.raises(queue.Empty):
            events.get(timeout=0.2)
        release.set()
        assert events.get(timeout=10) == "acquired"
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
        events.close()
    assert all(worker.exitcode == 0 for worker in workers)


def test_capacity_releases_after_failure_and_nested_calls_share_slot(tmp_path):
    from core.project_lean_capacity import acquire_project_lean_capacity

    with pytest.raises(RuntimeError, match="compiler failed"):
        with acquire_project_lean_capacity(tmp_path, capacity=1) as outer:
            with acquire_project_lean_capacity(tmp_path, capacity=1) as nested:
                assert nested is outer
            assert not outer._released
            raise RuntimeError("compiler failed")
    assert outer._released
    with acquire_project_lean_capacity(tmp_path, capacity=1) as next_lease:
        assert next_lease.slot == 0
        assert next_lease is not outer
