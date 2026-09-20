"""Cheap, side-effect-free checks for whether a generated Lean module is integrated.

The campaign ledger must distinguish a declaration which compiles in isolation
from one which is reachable from the project's public root.  This module only
parses imports; the Lake/Lean build remains the authoritative kernel check.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from leanflow_cli.lean.lean_module_paths import _lean_imports_from_text


@lru_cache(maxsize=8)
def _reachable_modules(root_name: str, root_file_name: str, root_mtime_ns: int) -> frozenset[str]:
    """Build one import closure per project snapshot.

    Proof previews inspect many generated files.  Re-parsing every local Lean
    file for each candidate made a read-only preview take minutes.  The root
    file mtime invalidates the cache when integration imports change; the
    process remains side-effect free.
    """
    root = Path(root_name)
    root_file = Path(root_file_name)
    local_imports: dict[str, set[str]] = {}
    for source_file in root.rglob("*.lean"):
        if any(part in {".git", ".lake", "build"} for part in source_file.parts):
            continue
        try:
            module = ".".join(source_file.relative_to(root).with_suffix("").parts)
            local_imports[module] = set(
                _lean_imports_from_text(source_file.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError):
            continue
    try:
        root_imports = set(_lean_imports_from_text(root_file.read_text(encoding="utf-8")))
    except (OSError, UnicodeError):
        root_imports = set()
    reachable: set[str] = set()
    pending = list(root_imports)
    while pending:
        module = pending.pop()
        if module in reachable:
            continue
        reachable.add(module)
        pending.extend(local_imports.get(module, set()) - reachable)
    return frozenset(reachable)


def project_target_reachability(
    project_root: str | Path, target_file: str | Path
) -> dict[str, Any]:
    """Return import-closure metadata for ``target_file``.

    ``root_reachable`` is ``True`` or ``False`` when a conventional project
    aggregator is present, and ``None`` when no aggregator can be identified.
    The latter is intentionally *unknown*, never an implicit success.  No
    files are written and no Lean process is started.
    """

    root = Path(project_root).expanduser().resolve()
    target = (root / Path(target_file)).resolve()
    result: dict[str, Any] = {
        "root_file": "",
        "target_file": str(target_file),
        "target_module": "",
        "root_reachable": None,
        "integration_status": "unknown_no_project_root",
    }
    if not target.is_relative_to(root):
        result.update(
            {
                "root_reachable": False,
                "integration_status": "invalid_target_path",
            }
        )
        return result

    try:
        result["target_module"] = ".".join(target.relative_to(root).with_suffix("").parts)
    except ValueError:
        result["root_reachable"] = False
        result["integration_status"] = "invalid_target_path"
        return result

    root_candidates = (
        root / "FateXWork.lean",
        root / "Main.lean",
        root / "HDP.lean",
        root / f"{root.name}.lean",
    )
    root_file = next((candidate for candidate in root_candidates if candidate.is_file()), None)
    if root_file is None:
        return result
    result["root_file"] = str(root_file.relative_to(root))

    try:
        root_mtime_ns = root_file.stat().st_mtime_ns
    except OSError:
        root_mtime_ns = 0
    reachable = _reachable_modules(str(root), str(root_file), root_mtime_ns)
    is_reachable = result["target_module"] in reachable
    result["root_reachable"] = is_reachable
    result["integration_status"] = "integrated" if is_reachable else "not_reachable"
    return result
