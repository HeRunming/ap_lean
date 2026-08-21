"""Discover a local Lean toolchain for manager-owned verification subprocesses."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path


def discover_lean_bin(
    project_root: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Return the first directory containing executable `lake` and `lean` binaries."""
    env = dict(environ or os.environ)
    candidates: list[Path] = []
    elan_home = str(env.get("ELAN_HOME", "") or "").strip()
    if elan_home:
        candidates.append(Path(elan_home).expanduser() / "bin")
    lake = shutil.which("lake", path=env.get("PATH"))
    if lake:
        candidates.append(Path(lake).resolve().parent)
    root = Path(project_root).expanduser().resolve()
    candidates.extend(parent / ".elan-home" / "bin" for parent in (root, *root.parents))
    candidates.append(Path.home() / ".elan" / "bin")

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if os.access(resolved / "lake", os.X_OK) and os.access(resolved / "lean", os.X_OK):
            return resolved
    return None


def add_lean_toolchain_env(
    child_env: Mapping[str, str],
    *,
    project_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return child env with a discovered Lean bin prepended without duplicate entries."""
    current = dict(environ or os.environ)
    updated = dict(child_env)
    lean_bin = discover_lean_bin(project_root, environ={**current, **updated})
    if lean_bin is None:
        return updated
    path_parts = [
        part for part in str(updated.get("PATH", current.get("PATH", ""))).split(os.pathsep) if part
    ]
    lean_bin_text = str(lean_bin)
    updated["PATH"] = os.pathsep.join(
        [lean_bin_text, *(part for part in path_parts if part != lean_bin_text)]
    )
    if lean_bin.parent.name in {".elan", ".elan-home"}:
        updated.setdefault("ELAN_HOME", str(lean_bin.parent))
    return updated
