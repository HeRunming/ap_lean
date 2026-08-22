"""Document text-extraction helpers for LeanFlow formalization preflight.

Split verbatim out of :mod:`leanflow_cli.formalization.formalization_documents`. This is the
text/LaTeX/PDF EXTRACTION layer: it turns a resolved source file into a structured
summary (theorem blocks, sections, references, extracted text). It is a closed set
under "calls" -- every non-stdlib callee is another helper defined here -- and it
reaches no module-mutable state in the origin, so it imports nothing from
``formalization_documents`` (no import cycle). The origin re-exports these names so
every caller and test keeps resolving them as ``formalization_documents.<name>``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

MAX_EXTRACTED_TEXT_CHARS = 120_000
MAX_STATEMENT_CHARS = 1_600
MAX_THEOREM_BLOCKS = 80
MAX_SECTIONS = 80
MAX_REFERENCES = 80
MAX_QA_ITEMS = 2_000
MAX_QA_BATCH_ITEMS = 24


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    """Return the first non-empty scalar field from a parsed JSON item."""
    for key in keys:
        value = item.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value).strip()
    return ""


def _qa_batch_index(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build stable chapter-aware batches without changing source item order."""
    batches: list[dict[str, Any]] = []
    current_chapter = ""
    current_labels: list[str] = []

    def flush() -> None:
        if not current_labels:
            return
        ordinal = 1 + sum(1 for batch in batches if batch["chapter"] == current_chapter)
        batches.append(
            {
                "id": f"chapter-{current_chapter}-batch-{ordinal}",
                "chapter": current_chapter,
                "labels": list(current_labels),
                "count": len(current_labels),
                "first_label": current_labels[0],
                "last_label": current_labels[-1],
            }
        )

    for block in blocks:
        label = str(block.get("label", "") or "")
        chapter = label.partition(".")[0] or "unscoped"
        if current_labels and (
            chapter != current_chapter or len(current_labels) >= MAX_QA_BATCH_ITEMS
        ):
            flush()
            current_labels = []
        current_chapter = chapter
        current_labels.append(label)
    flush()
    return batches


def _extract_qa_json_summary(path: Path) -> dict[str, Any]:
    """Normalize a parser-produced QA JSON file into document theorem blocks.

    Accept a canonical ``items`` array and common ``qa_pairs``/``questions``/
    ``data`` aliases.  Natural-language solutions are retained only as optional
    prover hints; the statement remains the formalization source of truth.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        raw_items = payload
        metadata: dict[str, Any] = {}
    elif isinstance(payload, dict):
        metadata = payload
        raw_items = next(
            (
                payload[key]
                for key in ("items", "qa_pairs", "questions", "problems", "data")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
    else:
        raise ValueError("QA JSON must contain an object or array")

    audit_by_label: dict[str, dict[str, Any]] = {}
    audit_path = path.parent / "visual_correction_audit.json"
    if audit_path.is_file():
        audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
        if isinstance(audit_payload, list):
            audit_by_label = {
                str(item.get("label", "")): item
                for item in audit_payload
                if isinstance(item, dict) and item.get("label")
            }
    crop_by_label: dict[str, dict[str, Any]] = {}
    crop_path = path.parent.parent / "crop_manifest.json"
    if crop_path.is_file():
        crop_payload = json.loads(crop_path.read_text(encoding="utf-8"))
        if isinstance(crop_payload, dict):
            crop_by_label = {
                str(label): item for label, item in crop_payload.items() if isinstance(item, dict)
            }
    foundations_path = path.parent / "source_foundations.json"
    source_foundations: list[dict[str, Any]] = []
    if foundations_path.is_file():
        foundations_payload = json.loads(foundations_path.read_text(encoding="utf-8"))
        if isinstance(foundations_payload, dict):
            foundations_payload = foundations_payload.get("foundations", [])
        if isinstance(foundations_payload, list):
            source_foundations = [
                dict(item) for item in foundations_payload if isinstance(item, dict)
            ]

    blocks: list[dict[str, Any]] = []
    extracted_parts: list[str] = []
    for index, raw_item in enumerate(raw_items[:MAX_QA_ITEMS], start=1):
        if not isinstance(raw_item, dict):
            continue
        statement = _first_text(raw_item, ("statement", "question", "problem", "prompt", "text"))
        if not statement:
            continue
        proof = _first_text(
            raw_item, ("proof", "solution", "answer", "reference_answer", "rationale")
        )
        label = _first_text(raw_item, ("id", "label", "uid", "name")) or f"qa-{index}"
        audit = audit_by_label.get(label, {})
        crop = crop_by_label.get(label, {})
        crop_specs = crop.get("specs", []) if isinstance(crop.get("specs"), list) else []
        dependencies = raw_item.get("dependencies", raw_item.get("uses", []))
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        if not isinstance(dependencies, list):
            dependencies = []
        pages = audit.get("pages", []) if isinstance(audit.get("pages"), list) else []
        if not pages:
            pages = [
                spec.get("pdf_page")
                for spec in crop_specs
                if isinstance(spec, dict) and spec.get("pdf_page") not in (None, "")
            ]
        page = raw_item.get("page", raw_item.get("page_number", pages[0] if pages else 0))
        source_locator = _first_text(raw_item, ("source_locator", "locator"))
        if not source_locator and pages:
            page_text = ",".join(str(value) for value in pages)
            source_locator = f"{path.name}:pdf-pages-{page_text}"
        source_boxes = [
            {
                "pdf_page": spec.get("pdf_page"),
                "page_idx": spec.get("page_idx"),
                "bbox_1000": spec.get("bbox_1000"),
            }
            for spec in crop_specs
            if isinstance(spec, dict)
        ]
        blocks.append(
            {
                "kind": _first_text(raw_item, ("kind", "type")) or "question",
                "line": int(raw_item.get("line", index) or index),
                "end_line": int(raw_item.get("end_line", raw_item.get("line", index)) or index),
                "label": label,
                "title": _first_text(raw_item, ("title", "heading")),
                "uses": [str(value) for value in dependencies if str(value).strip()],
                "statement": _bounded(statement, MAX_STATEMENT_CHARS),
                "proof": _bounded(proof, MAX_STATEMENT_CHARS),
                "page": page,
                "source_locator": source_locator,
                "statement_sources": source_boxes,
                "uncertain_spans": audit.get("uncertain_spans", []),
                "parser_metadata": {
                    key: raw_item[key]
                    for key in ("chapter", "section", "page", "page_number", "bbox")
                    if key in raw_item
                },
            }
        )
        extracted_parts.append(f"[{label}]\n{statement}")
        if proof:
            extracted_parts.append(f"Reference solution (optional hint):\n{proof}")

    title = _first_text(metadata, ("title", "book_title", "name"))
    return {
        "source_kind": "qa_json",
        "title": title,
        "bytes": path.stat().st_size,
        "sections": metadata.get("sections", []) if isinstance(metadata, dict) else [],
        "theorem_blocks": blocks,
        "labels": [block["label"] for block in blocks],
        "refs": [],
        "extracted_text": _bounded("\n\n".join(extracted_parts), MAX_EXTRACTED_TEXT_CHARS),
        "qa_schema_version": str(metadata.get("schema_version", "unversioned")),
        "qa_item_count": len(blocks),
        "qa_batches": _qa_batch_index(blocks),
        "source_foundations": source_foundations,
        "qa_sidecar_foundations": str(foundations_path) if source_foundations else "",
        "qa_sidecar_audit": str(audit_path) if audit_by_label else "",
        "qa_sidecar_crop_manifest": str(crop_path) if crop_by_label else "",
    }


def _bounded(text: str, limit: int) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 44)].rstrip() + "\n\n[truncated by LeanFlow document preflight]"


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _line_span(text: str, start: int, end: int) -> tuple[int, int]:
    return _line_number(text, start), _line_number(text, max(start, end))


def _clean_tex_statement(value: str) -> str:
    text = re.sub(r"\\(?:label|lean|uses|proves|discussion)\{[^{}]*\}", " ", value or "")
    text = re.sub(r"\\(?:leanok|notready|mathlibok)\b", " ", text)
    text = re.sub(r"%[^\n]*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_braced_commands(text: str, command: str) -> list[str]:
    pattern = re.compile(rf"\\{re.escape(command)}\{{([^{{}}]+)\}}")
    values: list[str] = []
    for match in pattern.finditer(text or ""):
        for value in match.group(1).split(","):
            item = value.strip()
            if item and item not in values:
                values.append(item)
    return values


_DEFAULT_LATEX_THEOREM_ENV_KINDS = {
    "assumption": "assumption",
    "claim": "claim",
    "construction": "construction",
    "theorem": "theorem",
    "thm": "theorem",
    "lemma": "lemma",
    "lem": "lemma",
    "proposition": "proposition",
    "prop": "proposition",
    "corollary": "corollary",
    "cor": "corollary",
    "definition": "definition",
    "defn": "definition",
    "def": "definition",
    "conjecture": "conjecture",
    "conj": "conjecture",
    "example": "example",
    "ex": "example",
    "exercise": "exercise",
    "fact": "fact",
    "notation": "notation",
    "observation": "observation",
    "problem": "problem",
    "question": "question",
    "remark": "remark",
    "rem": "remark",
}


def _normalize_theorem_kind(env: str, title: str = "") -> str:
    text = f"{title} {env}".strip().lower()
    for token, kind in (
        ("theorem", "theorem"),
        ("lemma", "lemma"),
        ("proposition", "proposition"),
        ("corollary", "corollary"),
        ("definition", "definition"),
        ("conjecture", "conjecture"),
        ("example", "example"),
        ("remark", "remark"),
        ("claim", "claim"),
        ("problem", "problem"),
        ("question", "question"),
        ("assumption", "assumption"),
        ("observation", "observation"),
        ("construction", "construction"),
        ("fact", "fact"),
        ("comment", "comment"),
        ("notation", "notation"),
        ("exercise", "exercise"),
    ):
        if re.search(rf"\b{token}\b", text):
            return kind
    fallback = re.sub(r"[^A-Za-z]+", " ", title or env).strip().lower()
    return fallback.split()[0] if fallback else "statement"


def _extract_latex_option_name(options: str) -> str:
    text = str(options or "")
    match = re.search(
        r"(?:^|,)\s*name\s*=\s*(?:\{(?P<braced>[^{}]+)\}|(?P<plain>[^,\]]+))",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return (match.group("braced") or match.group("plain") or "").strip()


def _latex_theorem_environment_kinds(raw: str) -> dict[str, str]:
    r"""Build a map of LaTeX theorem environment names to normalized kinds by scanning for declarations. Starts with built-in LaTeX theorem environments and augments with custom environments defined via \newtheorem, \declaretheorem, \newmdtheoremenv, \spnewtheorem, \newtcbtheorem, and \newenvironment directives in the source."""
    envs = dict(_DEFAULT_LATEX_THEOREM_ENV_KINDS)
    newtheorem_pattern = re.compile(
        r"\\newtheorem\*?\s*"
        r"\{(?P<env>[^{}\s]+)\}"
        r"(?:\[[^\]]+\])?\s*"
        r"\{(?P<title>[^{}]+)\}"
        r"(?:\[[^\]]+\])?",
        flags=re.IGNORECASE,
    )
    for match in newtheorem_pattern.finditer(raw or ""):
        env = str(match.group("env") or "").strip()
        title = str(match.group("title") or "").strip()
        if env:
            envs[env] = _normalize_theorem_kind(env, title)

    declaretheorem_pattern = re.compile(
        r"\\declaretheorem\s*(?:\[(?P<options>[^\]]*)\])?\s*\{(?P<env>[^{}\s]+)\}",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in declaretheorem_pattern.finditer(raw or ""):
        env = str(match.group("env") or "").strip()
        title = _extract_latex_option_name(match.group("options") or "")
        if env:
            envs[env] = _normalize_theorem_kind(env, title)

    mdtheorem_pattern = re.compile(
        r"\\(?:newmdtheoremenv|mdtheorem)\s*(?:\[[^\]]*\])?\s*"
        r"\{(?P<env>[^{}\s]+)\}\s*\{(?P<title>[^{}]*)\}",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in mdtheorem_pattern.finditer(raw or ""):
        env = str(match.group("env") or "").strip()
        title = str(match.group("title") or "").strip()
        if env:
            envs[env] = _normalize_theorem_kind(env, title)

    spnewtheorem_pattern = re.compile(
        r"\\spnewtheorem\*?\s*\{(?P<env>[^{}\s]+)\}\s*(?:\[[^\]]+\])?\s*" r"\{(?P<title>[^{}]+)\}",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in spnewtheorem_pattern.finditer(raw or ""):
        env = str(match.group("env") or "").strip()
        title = str(match.group("title") or "").strip()
        if env:
            envs[env] = _normalize_theorem_kind(env, title)

    tcbtheorem_pattern = re.compile(
        r"\\(?:newtcbtheorem|NewTcbTheorem)\s*(?:\[[^\]]*\])?\s*"
        r"\{(?P<env>[^{}\s]+)\}\s*\{(?P<title>[^{}]+)\}",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in tcbtheorem_pattern.finditer(raw or ""):
        env = str(match.group("env") or "").strip()
        title = str(match.group("title") or "").strip()
        if env:
            envs[env] = _normalize_theorem_kind(env, title)

    newenvironment_pattern = re.compile(
        r"\\(?:newenvironment|renewenvironment)\s*\{(?P<env>[^{}\s]+)\}",
        flags=re.IGNORECASE,
    )
    for match in newenvironment_pattern.finditer(raw or ""):
        env = str(match.group("env") or "").strip()
        if env and _normalize_theorem_kind(env) != "statement":
            envs.setdefault(env, _normalize_theorem_kind(env))
    return envs


def _following_latex_proof(raw: str, end_offset: int, theorem_env_pattern: str) -> dict[str, Any]:
    following = raw[end_offset : end_offset + 20_000]
    boundary_pattern = re.compile(
        rf"\\begin\{{(?:{theorem_env_pattern})\}}|\\(?:chapter|section|subsection|subsubsection)\*?\{{",
        flags=re.IGNORECASE,
    )
    proof_pattern = re.compile(
        r"^\s*(?:%[^\n]*(?:\n|$)\s*)*(?:"
        r"\\begin\{proof\}(?:\[[^\]]*\])?(?P<braced_body>.*?)\\end\{proof\}"
        r"|\\proof\b(?P<plain_body>.*?)\\endproof"
        r")",
        flags=re.IGNORECASE | re.DOTALL,
    )
    proof_match = proof_pattern.search(following)
    if not proof_match:
        return {"proof": "", "proof_line": 0, "proof_end_line": 0}
    boundary = boundary_pattern.search(following)
    if boundary and boundary.start() < proof_match.start():
        return {"proof": "", "proof_line": 0, "proof_end_line": 0}
    proof_start = end_offset + proof_match.start()
    proof_end = end_offset + proof_match.end()
    proof_start_line, proof_end_line = _line_span(raw, proof_start, proof_end)
    return {
        "proof": _bounded(
            _clean_tex_statement(
                proof_match.group("braced_body") or proof_match.group("plain_body") or ""
            ),
            MAX_STATEMENT_CHARS,
        ),
        "proof_line": proof_start_line,
        "proof_end_line": proof_end_line,
    }


def _extract_latex_summary(path: Path) -> dict[str, Any]:
    r"""Extract a structured summary from a LaTeX file for formalization preflight: theorem blocks, \profess statements, sections, citations, labels, and raw text, all bounded by size limits. Returns document title, parsed blocks with extracted Lean hints and proofs, bibliography references, and full text for context."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    theorem_env_kinds = _latex_theorem_environment_kinds(raw)
    env_pattern = "|".join(
        re.escape(env) for env in sorted(theorem_env_kinds, key=len, reverse=True)
    )
    theorem_pattern = re.compile(
        rf"\\begin\{{(?P<env>{env_pattern})\}}"
        r"(?P<option>\[[^\]]*\])?"
        r"(?P<body>.*?)"
        r"\\end\{(?P=env)\}",
        re.IGNORECASE | re.DOTALL,
    )
    blocks: list[dict[str, Any]] = []
    for match in theorem_pattern.finditer(raw):
        body = match.group("body") or ""
        label_match = re.search(r"\\label\{([^{}]+)\}", body)
        option = (match.group("option") or "").strip()
        start_line, end_line = _line_span(raw, match.start(), match.end())
        proof_info = _following_latex_proof(raw, match.end(), env_pattern)
        env = match.group("env")
        label = label_match.group(1).strip() if label_match else f"line-{start_line}"
        blocks.append(
            {
                "kind": theorem_env_kinds.get(env, _normalize_theorem_kind(env)),
                "environment": env,
                "line": start_line,
                "end_line": end_line,
                "offset": match.start(),
                "label": label,
                "title": option.strip("[]"),
                "lean": _extract_braced_commands(body, "lean"),
                "uses": _extract_braced_commands(body, "uses"),
                "statement": _bounded(_clean_tex_statement(body), MAX_STATEMENT_CHARS),
                **proof_info,
            }
        )
        if len(blocks) >= MAX_THEOREM_BLOCKS:
            break
    if len(blocks) < MAX_THEOREM_BLOCKS:
        existing_offsets = {int(block.get("offset", -1) or -1) for block in blocks}
        profess_pattern = re.compile(
            r"\\profess\{(?P<kind>[^{}]+)\}\s*(?P<body>.*?)\\endprofess",
            re.IGNORECASE | re.DOTALL,
        )
        for match in profess_pattern.finditer(raw):
            if match.start() in existing_offsets:
                continue
            raw_kind = str(match.group("kind") or "statement").strip()
            kind = re.sub(r"[^A-Za-z]+", " ", raw_kind).strip().lower() or "statement"
            if kind.endswith("."):
                kind = kind[:-1].strip()
            following = raw[match.end() : match.end() + 12_000]
            proof = ""
            proof_match = re.search(
                r"^\s*\\proof\s*(?P<body>.*?)\\endproof",
                following,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if proof_match:
                proof = _bounded(
                    _clean_tex_statement(proof_match.group("body") or ""), MAX_STATEMENT_CHARS
                )
                proof_start_line, proof_end_line = _line_span(
                    raw,
                    match.end() + proof_match.start(),
                    match.end() + proof_match.end(),
                )
            else:
                proof_start_line = proof_end_line = 0
            line = _line_number(raw, match.start())
            _start_line, end_line = _line_span(raw, match.start(), match.end())
            label_match = re.search(r"\\label\{([^{}]+)\}", match.group("body") or "")
            blocks.append(
                {
                    "kind": kind,
                    "line": line,
                    "end_line": end_line,
                    "proof_line": proof_start_line,
                    "proof_end_line": proof_end_line,
                    "offset": match.start(),
                    "label": label_match.group(1).strip() if label_match else f"line-{line}",
                    "title": raw_kind,
                    "lean": _extract_braced_commands(match.group("body") or "", "lean"),
                    "uses": _extract_braced_commands(match.group("body") or "", "uses"),
                    "statement": _bounded(
                        _clean_tex_statement(match.group("body") or ""), MAX_STATEMENT_CHARS
                    ),
                    "proof": proof,
                }
            )
            if len(blocks) >= MAX_THEOREM_BLOCKS:
                break

    section_pattern = re.compile(
        r"\\(?P<level>chapter|section|subsection|subsubsection)\*?\{(?P<title>[^{}\n]+)\}"
    )
    sections = [
        {
            "level": match.group("level"),
            "line": _line_number(raw, match.start()),
            "title": match.group("title").strip(),
        }
        for match in section_pattern.finditer(raw)
    ][:MAX_SECTIONS]
    title_match = re.search(r"\\title\{([^{}\n]+)\}", raw)
    title = title_match.group(1).strip() if title_match else ""
    if not title:
        title_lines = re.findall(r"\\centerline\{\\titlefont\s+([^{}\n]+)\}", raw)
        if title_lines:
            title = " ".join(item.strip() for item in title_lines if item.strip())
    bibliography_files = _extract_braced_commands(raw, "bibliography") + _extract_braced_commands(
        raw, "addbibresource"
    )
    citations = _extract_braced_commands(raw, "cite")[:MAX_REFERENCES]
    labels = _extract_braced_commands(raw, "label")[:MAX_REFERENCES]
    refs = _extract_braced_commands(raw, "ref")[:MAX_REFERENCES]
    return {
        "source_kind": "latex",
        "title": title,
        "bytes": path.stat().st_size,
        "sections": sections,
        "theorem_blocks": blocks,
        "labels": labels,
        "refs": refs,
        "citations": citations,
        "bibliography_files": bibliography_files[:MAX_REFERENCES],
        "extracted_text": _bounded(raw, MAX_EXTRACTED_TEXT_CHARS),
        "extraction_status": "ok",
    }


def _run_document_tool(command: list[str], timeout_s: int = 30) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        return False, error or output or f"exit code {result.returncode}"
    return True, output


def _extract_pdf_summary(path: Path) -> dict[str, Any]:
    tools = {
        "pdftotext": bool(shutil.which("pdftotext")),
        "pdfinfo": bool(shutil.which("pdfinfo")),
        "pdfimages": bool(shutil.which("pdfimages")),
    }
    metadata: dict[str, Any] = {
        "source_kind": "pdf",
        "bytes": path.stat().st_size,
        "pdf_tools": tools,
        "sections": [],
        "theorem_blocks": [],
        "extracted_text": "",
        "extraction_status": "missing-pdftotext",
        "degraded_reasons": [],
    }
    if tools["pdfinfo"]:
        ok, info = _run_document_tool(["pdfinfo", str(path)], timeout_s=20)
        metadata["pdfinfo"] = _bounded(info, 4_000)
        if ok:
            pages = re.search(r"^Pages:\s+(\d+)", info, flags=re.MULTILINE)
            if pages:
                metadata["pages"] = int(pages.group(1))
        else:
            metadata["degraded_reasons"].append(f"pdfinfo failed: {info}")
    if tools["pdfimages"]:
        ok, image_list = _run_document_tool(["pdfimages", "-list", str(path)], timeout_s=30)
        metadata["pdf_images"] = _bounded(image_list, 6_000) if ok else ""
        if not ok:
            metadata["degraded_reasons"].append(f"pdfimages failed: {image_list}")
    if not tools["pdftotext"]:
        metadata["degraded_reasons"].append(
            "pdftotext is not installed; PDF text extraction was not available"
        )
        return metadata

    ok, extracted = _run_document_tool(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], timeout_s=60
    )
    if not ok:
        metadata["extraction_status"] = "pdftotext-failed"
        metadata["degraded_reasons"].append(f"pdftotext failed: {extracted}")
        return metadata
    metadata["extracted_text"] = _bounded(extracted, MAX_EXTRACTED_TEXT_CHARS)
    metadata["extraction_status"] = "ok"
    metadata["sections"] = _extract_plaintext_sections(extracted)
    return metadata


def _extract_plaintext_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*((?:\d+(?:\.\d+)*)\s+[A-Z][^\n]{2,120}|[A-Z][A-Z0-9 ,;:()'/-]{6,120})\s*$"
    )
    for line_number, line in enumerate((text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if pattern.match(stripped):
            sections.append({"level": "section", "line": line_number, "title": stripped})
        if len(sections) >= MAX_SECTIONS:
            break
    return sections
