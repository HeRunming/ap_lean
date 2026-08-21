"""Prepare non-mutating source images for verified declaration promotion."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from leanflow_cli.lean.lean_parsing import _declaration_line_index_from_text


class PromotionTransactionError(ValueError):
    """Raised when a promotion candidate no longer matches its recorded source."""


def _normalized_declaration(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _digest(text: str) -> str:
    return hashlib.sha256(_normalized_declaration(text).encode("utf-8")).hexdigest()[:16]


def _module_path(project_root: Path, module: str) -> Path:
    if not module or any(not part for part in module.split(".")):
        raise PromotionTransactionError("promotion target module is missing")
    path = project_root / Path(*module.split(".")).with_suffix(".lean")
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise PromotionTransactionError("promotion target escapes the project") from exc
    return path


def _with_import(source: str, module: str) -> str:
    import_line = f"import {module}"
    if re.search(rf"^\s*{re.escape(import_line)}\s*$", source, flags=re.MULTILINE):
        return source
    lines = source.splitlines()
    insert_at = 0
    while insert_at < len(lines) and lines[insert_at].lstrip().startswith("import "):
        insert_at += 1
    lines.insert(insert_at, import_line)
    if insert_at == 0 and len(lines) > 1 and lines[1].strip():
        lines.insert(1, "")
    return "\n".join(lines).rstrip() + "\n"


def _wrapped_declaration(namespace: list[str], declaration: str) -> str:
    opens = "\n".join(f"namespace {name}" for name in namespace)
    closes = "\n".join(f"end {name}" for name in reversed(namespace))
    return "\n\n".join(part for part in (opens, declaration.strip(), closes) if part) + "\n"


def prepare_promotion_after_images(
    project_root: Path,
    workspace: Path,
    transaction: Mapping[str, Any],
) -> dict[Path, str]:
    """Return exact source/target after-images for one approved promotion candidate."""
    if transaction.get("status") != "ready_for_verified_candidate_patch":
        raise PromotionTransactionError("transaction is not ready for a verified candidate patch")
    targets = [str(value) for value in transaction.get("target_modules", []) or [] if str(value)]
    if len(targets) != 1:
        raise PromotionTransactionError("dry-run promotion requires exactly one target module")
    source_path = (workspace / str(transaction.get("source_file", ""))).resolve()
    try:
        source_path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise PromotionTransactionError("promotion source escapes the corpus workspace") from exc
    if not source_path.is_file():
        raise PromotionTransactionError("promotion source file is missing")
    source = source_path.read_text(encoding="utf-8")
    name = str(transaction.get("name", "") or "")
    expected_digest = str(transaction.get("declaration_digest", "") or "")
    matches = [
        entry
        for entry in _declaration_line_index_from_text(source)
        if str(entry.get("name", "") or "") == name
        and _digest(str(entry.get("text", "") or "")) == expected_digest
    ]
    if len(matches) != 1:
        raise PromotionTransactionError("promotion declaration digest is stale or ambiguous")
    entry = matches[0]
    start = int(entry["line"])
    end = int(entry["end_line"])
    declaration = str(entry["text"])
    lines = source.splitlines()
    source_without = "\n".join([*lines[: start - 1], *lines[end:]]).strip() + "\n"
    source_after = _with_import(source_without, targets[0])

    target_path = _module_path(project_root.resolve(), targets[0])
    target_before = (
        target_path.read_text(encoding="utf-8") if target_path.is_file() else "import Mathlib\n"
    )
    qualified_name = str(transaction.get("qualified_name", "") or name)
    if re.search(
        rf"\b(?:def|abbrev|structure|class|instance)\s+{re.escape(name)}\b", target_before
    ):
        raise PromotionTransactionError(
            f"promotion target already declares `{qualified_name}` or an ambiguous short name"
        )
    namespace = [str(value) for value in transaction.get("namespace", []) or [] if str(value)]
    target_after = target_before.rstrip() + "\n\n" + _wrapped_declaration(namespace, declaration)
    return {source_path: source_after, target_path: target_after}


def materialize_promotion_sandbox(
    project_root: Path,
    after_images: Mapping[Path, str],
    destination: Path,
) -> dict[str, Any]:
    """Copy a project and atomically install candidate after-images in the disposable copy."""
    root = project_root.resolve()
    sandbox = destination.resolve()
    if sandbox.exists():
        raise PromotionTransactionError("promotion sandbox destination already exists")
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        root,
        sandbox,
        ignore=shutil.ignore_patterns(".git", ".lake", ".leanflow", ".venv", "__pycache__"),
        symlinks=True,
    )
    lake_build = root / ".lake"
    if lake_build.exists():
        (sandbox / ".lake").symlink_to(lake_build, target_is_directory=True)
    installed: list[str] = []
    for original, content in after_images.items():
        try:
            relative = original.resolve().relative_to(root)
        except ValueError as exc:
            raise PromotionTransactionError("candidate after-image escapes the project") from exc
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".promotion-tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        installed.append(relative.as_posix())
    source_files = [
        path for path in installed if not path.endswith("/Shared.lean") and "/Shared/" not in path
    ]
    target_files = [path for path in installed if path not in source_files]
    return {
        "sandbox_root": str(sandbox),
        "installed_files": installed,
        "verification_plan": {
            "steps": [
                *[f"lake env lean {path}" for path in target_files],
                *[f"lake env lean {path}" for path in source_files],
                "lake build",
            ],
            "acceptance": "all commands exit 0; no new errors, sorries, or forbidden axioms",
            "source_project_mutated": False,
        },
    }
