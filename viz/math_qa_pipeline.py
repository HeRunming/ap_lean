"""
DataFlow Pipeline: Math QA Clean -> Synthesize -> Reason -> Deduplicate
Run ID: f88ee4b3-2f50-425a-99aa-753dd0574e50
Status: v3 (task status=validated; v2 syntax defect fixed, no structural change)

Input  (task.input_contract): id, question, answer (all string; jsonl or json array)
Output (out/final_dataset.jsonl): id, seed_id, origin, question, answer,
    question_raw, answer_raw, reasoning_trace, trace_consistent,
    validation_confidence, solve_agreement

v3 fixes vs v2:
  G1 BLOCKER: `from datasketch import MinHash as minhash_cls` inside a function
     that also assigns `minhash_cls = lsh_cls = None` is a SyntaxError in CPython
     (import binds a local name that is also assigned before the import). The
     dedup operator now resolves the optional dependency through a module-level
     helper `_load_minhash()` that returns (MinHash, MinHashLSH) or None.
  G2 dedup LSH branch keeps a per-id signature table so `duplicate_of` and the
     jaccard confirmation both work on real record ids; LSH hits are verified
     against the exact jaccard threshold before removal (LSH is approximate).
  G3 StateStore.put fsyncs on the append path, matching the atomic export write.
  G4 removed the unused `_reasoning_prompt` re-derivation per retry; the prompt is
     built once and reused, so a retry cannot silently change the task framing.

Carried over from v2:
  F2 solve drops a record only on unsolvable majority or no answer majority.
  F3 prefilter truncation heuristic is word-boundary aware.
  F4 MATH_SIGNAL requires digits, operators, or explicit LaTeX macros.
  F5 answer_key unwraps \\text{}/\\mathrm{}/\\mbox{} before comparison.
  F6 reasoning marks trace_consistent=false after a bounded retry.
  F7 export writes are atomic (tmp + fsync + replace).

Open decisions, overridable, echoed into run_report.open_decisions:
  D1 synthetic answer source -> Config.synthetic_answer_strategy
       solve (default; solve_synthetic_question, self-consistency k=3)
       emit (synthesize returns the answer) | reasoning_derived (answer nullable)
  D2 source answer form -> Config.answer_form; with_solution switches reasoning
     from GENERATE to REFORMAT
  D3 dedup: word 5-gram jaccard, threshold 0.8, question only
  D4 seeds protected from dedup removal (Config.dedup_protect_seeds)
  D5 synthesis_count_per_seed=2 is an upper-bound target, no top-up loop;
     per-seed retention histogram is reported

Idempotency: each record is checkpointed per stage under
state/<run_id>/<stage>.jsonl keyed by (config fingerprint, record id). Reruns
skip completed keys, per-item failures go to dead_letter.jsonl, and the cost
ceiling stops the run gracefully with completed partitions preserved.

Deps: anthropic>=0.18.0; datasketch>=1.6.4 (optional, >100k records)
Env:  ANTHROPIC_API_KEY (required), ANTHROPIC_BASE_URL (optional)

Usage:
  python math_qa_pipeline.py --input seeds.jsonl --dry-run-sample 300 --max-usd 5
  python math_qa_pipeline.py --input seeds.jsonl --workers 32 --max-usd 200

Author: PipelineBuilder Agent (draft, isolated env; local files only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger("dataflow.math_qa")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

SEP = "::"
NL = chr(10)
BS = chr(92)


class BudgetExceeded(RuntimeError):
    """Run-level call/cost ceiling hit; triggers graceful stop."""


class ContractViolation(RuntimeError):
    """input_contract assertion failed; raised before any LLM spend."""


@dataclass
class Config:
    input_path: str
    output_dir: str = "out"
    state_dir: str = "state"
    run_id: str = "f88ee4b3-2f50-425a-99aa-753dd0574e50"

    validate_model: str = os.getenv("VALIDATE_MODEL", "claude-3-5-haiku-latest")
    arbiter_model: str = os.getenv("ARBITER_MODEL", "claude-sonnet-4-5")
    synthesize_model: str = os.getenv("SYNTHESIZE_MODEL", "claude-sonnet-4-5")
    solve_model: str = os.getenv("SOLVE_MODEL", "claude-sonnet-4-5")
    reasoning_model: str = os.getenv("REASONING_MODEL", "claude-sonnet-4-5")

    workers: int = 16
    unify_latex_delimiters: bool = True
    min_question_len: int = 10
    min_answer_len: int = 1
    require_math_signal: bool = True

    min_confidence: float = 0.6
    arbitrate_low_confidence: bool = True

    n_per_seed: int = 2
    synth_temperature: float = 0.8
    synthetic_answer_strategy: str = "solve"
    solve_self_consistency_k: int = 3
    validate_synth: bool = True

    reasoning_temperature: float = 0.3
    require_final_answer_match: bool = True
    reasoning_match_retries: int = 1
    drop_inconsistent_traces: bool = False
    answer_form: str = "unknown"

    ngram_n: int = 5
    dedup_threshold: float = 0.8
    dedup_field: str = "question"
    dedup_protect_seeds: bool = True
    minhash_threshold_records: int = 100000
    minhash_num_perm: int = 128

    max_records: Optional[int] = None
    max_llm_calls: int = 200000
    max_usd: float = 50.0
    request_timeout_s: int = 120
    max_retries: int = 3
    max_tokens: int = 2048

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def idempotency_key(self) -> str:
        return "pipeline_math_qa_v3_" + self.run_id.split("-")[0] + "_" + self.fingerprint()


# F4: digits, arithmetic/comparison operators, or explicit LaTeX macros only.
MATH_SIGNAL = re.compile(r"[0-9=+*/^%<>]|" + BS + r"(frac|sqrt|sum|int|pi|times|cdot)")
TRUNCATION_TAIL = ("and", "the", "of", "to", "is", "if", "for", "with")
LATEX_OPEN = BS + "("
LATEX_CLOSE = BS + ")"
LATEX_DOPEN = BS + "["
LATEX_DCLOSE = BS + "]"
ESCAPED_DOLLAR = BS + "$"
TEXT_WRAPPER = re.compile(BS + r"(text|mathrm|mbox)" + r"\{([^}]*)\}")


def _load_minhash():
    """G1: resolve the optional datasketch dependency at module scope.
    Returns (MinHash, MinHashLSH) or None when unavailable."""
    try:
        import datasketch
    except ImportError:
        return None
    return datasketch.MinHash, datasketch.MinHashLSH


def normalize_text(s: str, unify_latex: bool) -> str:
    """Whitespace collapse + control-char strip + optional LaTeX delimiter unify."""
    s = "".join(ch for ch in (s or "") if ord(ch) >= 32 or ch in (chr(9), NL))
    s = re.sub(r"\s+", " ", s).strip()
    if unify_latex and ESCAPED_DOLLAR not in s:
        s = s.replace(LATEX_DOPEN, "$$").replace(LATEX_DCLOSE, "$$")
        s = s.replace(LATEX_OPEN, "$").replace(LATEX_CLOSE, "$")
    return s


def answer_key(ans: str) -> str:
    """Canonical form for cheap answer equality (F5: unwrap text macros first)."""
    a = (ans or "").lower()
    a = TEXT_WRAPPER.sub(lambda m: m.group(2), a)
    a = a.replace(BS + "left", "").replace(BS + "right", "")
    a = re.sub(r"[\s$,]", "", a)
    return a.strip(".")


def word_ngrams(text: str, n: int) -> set:
    toks = re.findall(r"[a-z0-9]+|[^\sa-z0-9]", (text or "").lower())
    if not toks:
        return set()
    if len(toks) < n:
        return {" ".join(toks)}
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / float(union)) if union else 0.0


def extract_json(text: str) -> Any:
    """Tolerant JSON extraction from an LLM reply (strips prose and code fences)."""
    if not text:
        raise ValueError("empty llm response")
    cleaned = text.strip()
    if cleaned.startswith(chr(96) * 3):
        cleaned = cleaned.strip(chr(96)).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("unparseable json: " + cleaned[:200])


class StateStore:
    """Append-only record-level checkpoint store; config changes invalidate rows."""

    def __init__(self, root: str, run_id: str, fingerprint: str) -> None:
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fp = fingerprint
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _path(self, stage: str) -> Path:
        return self.dir / (stage + ".jsonl")

    def _lock(self, stage: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(stage, threading.Lock())

    def load(self, stage: str) -> Dict[str, Any]:
        path = self._path(stage)
        out: Dict[str, Any] = {}
        if not path.exists():
            return out
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # partial trailing line from an interrupted run
                if row.get("fp") == self.fp:
                    out[row["key"]] = row["value"]
        return out

    def put(self, stage: str, key: str, value: Any) -> None:
        """G3: flush and fsync so a hard stop cannot lose an already-paid result."""
        row = json.dumps({"fp": self.fp, "key": key, "value": value}, ensure_ascii=False)
        with self._lock(stage):
            with self._path(stage).open("a", encoding="utf-8") as fh:
                fh.write(row + NL)
                fh.flush()
                os.fsync(fh.fileno())

    def dead_letter(self, stage: str, key: str, error: Any) -> None:
        self.put("dead_letter", stage + SEP + str(key),
                 {"stage": stage, "key": key, "error": str(error)[:500]})


class LLMClient:
    IN_PRICE = float(os.getenv("USD_PER_INPUT_TOKEN", "0.000003"))
    OUT_PRICE = float(os.getenv("USD_PER_OUTPUT_TOKEN", "0.000015"))

    def __init__(self, cfg: Config) -> None:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        kwargs: Dict[str, Any] = {"api_key": api_key,
                                  "timeout": cfg.request_timeout_s,
                                  "max_retries": 0}
        base_url = os.getenv("ANTHROPIC_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
        self.cfg = cfg
        self._lock = threading.Lock()
        self.calls = 0
        self.in_tokens = 0
        self.out_tokens = 0
        self.failures = 0

    @property
    def est_usd(self) -> float:
        return self.in_tokens * self.IN_PRICE + self.out_tokens * self.OUT_PRICE

    def _charge(self, usage: Any) -> None:
        with self._lock:
            self.calls += 1
            self.in_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.out_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            if self.calls > self.cfg.max_llm_calls:
                raise BudgetExceeded("max_llm_calls reached: " + str(self.calls))
            if self.est_usd > self.cfg.max_usd:
                raise BudgetExceeded("max_usd reached: " + format(self.est_usd, ".2f"))

    def json_call(self, model: str, system: str, prompt: str, temperature: float,
                  required_keys: Tuple[str, ...] = ()) -> Any:
        last_err: Optional[Exception] = None
        for attempt in range(self.cfg.max_retries):
            try:
                resp = self._client.messages.create(
                    model=model, max_tokens=self.cfg.max_tokens,
                    temperature=temperature, system=system,
                    messages=[{"role": "user", "content": prompt}])
                self._charge(getattr(resp, "usage", None))
                text = "".join(getattr(b, "text", "") for b in resp.content
                               if getattr(b, "type", "") == "text")
                data = extract_json(text)
                if required_keys:
                    if not isinstance(data, dict):
                        raise ValueError("expected json object")
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        raise ValueError("missing keys: " + ", ".join(missing))
                return data
            except BudgetExceeded:
                raise
            except Exception as exc:
                last_err = exc
                with self._lock:
                    self.failures += 1
                time.sleep(min(2 ** attempt + random.random(), 30.0))
        raise RuntimeError("llm_call_failed: " + str(last_err))


def parallel_stage(stage: str, items: List[Any], fn: Callable[[Any], Any],
                   cfg: Config, store: StateStore) -> Tuple[List[Any], bool]:
    """Concurrent, checkpointed stage. Returns (results, budget_stopped)."""
    cached = store.load(stage)
    results: List[Any] = []
    pending: List[Tuple[str, Any]] = []
    for item in items:
        key = item["id"]
        if key in cached:
            results.append(cached[key])
        else:
            pending.append((key, item))
    LOG.info("stage=%s cached=%d pending=%d", stage, len(results), len(pending))
    if not pending:
        return results, False
    stopped = False
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        futures = {pool.submit(fn, item): key for key, item in pending}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                value = fut.result()
            except BudgetExceeded as exc:
                stopped = True
                LOG.error("budget stop in stage=%s: %s", stage, exc)
                continue
            except Exception as exc:
                store.dead_letter(stage, key, exc)
                continue
            if value is None:
                continue
            store.put(stage, key, value)
            results.append(value)
    if stopped:
        LOG.warning("stage=%s partial; rerun resumes from checkpoint", stage)
    return results, stopped


REQUIRED_FIELDS = ("id", "question", "answer")


def load_dataset(cfg: Config) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """ingest node: contract assertions before any LLM spend."""
    path = Path(cfg.input_path)
    if not path.exists():
        raise ContractViolation("input not found: " + str(path))
    text = path.read_text(encoding="utf-8")
    rows: List[Dict[str, Any]] = []
    if path.suffix == ".json":
        payload = json.loads(text)
        rows = list(payload) if isinstance(payload, list) else [payload]
    else:
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if cfg.max_records:
        rows = rows[:cfg.max_records]

    seen = set()
    empty = 0
    for idx, row in enumerate(rows):
        for fname in REQUIRED_FIELDS:
            if fname not in row:
                raise ContractViolation("row " + str(idx) + " missing field " + fname)
            if not isinstance(row[fname], str):
                raise ContractViolation("row " + str(idx) + " field " + fname + " not string")
        rid = row["id"]
        if SEP in rid:
            raise ContractViolation("source id contains reserved separator: " + rid)
        if rid in seen:
            raise ContractViolation("duplicate source id: " + rid)
        seen.add(rid)
        if not row["question"].strip() or not row["answer"].strip():
            empty += 1
    stats = {"total": len(rows), "empty_field_rows": empty, "unique_ids": len(seen)}
    LOG.info("ingest stats=%s", stats)
    return rows, stats


def text_normalize(row: Dict[str, Any], cfg: Config) -> Dict[str, Any]:
    return {"id": row["id"], "seed_id": row["id"], "origin": "seed",
            "question_raw": row["question"], "answer_raw": row["answer"],
            "question": normalize_text(row["question"], cfg.unify_latex_delimiters),
            "answer": normalize_text(row["answer"], cfg.unify_latex_delimiters)}


def rule_based_prefilter(rec: Dict[str, Any], cfg: Config) -> Tuple[bool, str]:
    """prefilter node. F3: truncation check is word-boundary aware."""
    q, a = rec["question"], rec["answer"]
    if len(q) < cfg.min_question_len:
        return False, "question_too_short"
    if len(a) < cfg.min_answer_len:
        return False, "answer_empty"
    if cfg.require_math_signal and not MATH_SIGNAL.search(q):
        return False, "no_math_signal"
    tail = q.rstrip()
    if tail.endswith(","):
        return False, "likely_truncated"
    last_word = re.split(r"\s+", tail)[-1].strip(".!?").lower() if tail else ""
    if last_word in TRUNCATION_TAIL:
        return False, "likely_truncated"
    return True, "ok"


VALIDATE_SYSTEM = (
    "You are a rigorous mathematics grader. Reply with STRICT JSON only, no markdown, "
    "with exactly these keys: is_well_formed (boolean), is_answer_correct (boolean), "
    "validation_reason (string, max 200 chars), confidence (number 0..1).")

FORM_HINTS = {
    "numeric": "The given answer is a plain number.",
    "latex": "The given answer is LaTeX; accept equivalent forms.",
    "with_solution": "The answer may contain a worked solution; grade the final result.",
}


def _validate_prompt(question: str, answer: str, answer_form: str) -> str:
    hint = FORM_HINTS.get(answer_form,
                          "Answer format unspecified; accept equivalent forms.")
    return ("Judge one math dataset item." + NL
            + "(a) is_well_formed: complete, self-consistent, unambiguous, solvable?" + NL
            + "(b) is_answer_correct: solve it independently, then check the given answer."
            + NL + hint + NL + "QUESTION:" + NL + question + NL
            + "GIVEN ANSWER:" + NL + answer + NL + "JSON only.")


def validate_math_question(rec: Dict[str, Any], cfg: Config, llm: LLMClient,
                           model: Optional[str] = None) -> Dict[str, Any]:
    """validate / validate_synth node: well-formedness + answer correctness gate.
    Low-confidence items are re-judged by the stronger arbiter model."""
    keys = ("is_well_formed", "is_answer_correct", "validation_reason", "confidence")
    prompt = _validate_prompt(rec["question"], rec.get("answer") or "", cfg.answer_form)
    data = llm.json_call(model or cfg.validate_model, VALIDATE_SYSTEM, prompt, 0.0, keys)
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    arbitrated = False
    if cfg.arbitrate_low_confidence and confidence < cfg.min_confidence and model is None:
        data = llm.json_call(cfg.arbiter_model, VALIDATE_SYSTEM, prompt, 0.0, keys)
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        arbitrated = True
    out = dict(rec)
    out["is_well_formed"] = bool(data.get("is_well_formed"))
    out["is_answer_correct"] = bool(data.get("is_answer_correct"))
    out["validation_reason"] = str(data.get("validation_reason", ""))[:200]
    out["validation_confidence"] = confidence
    out["arbitrated"] = arbitrated
    return out


def filter_invalid(records: List[Dict[str, Any]], cfg: Config
                   ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    rejects: List[Dict[str, Any]] = []
    for rec in records:
        ok = (rec.get("is_well_formed") and rec.get("is_answer_correct")
              and rec.get("validation_confidence", 0.0) >= cfg.min_confidence)
        (kept if ok else rejects).append(rec)
    return kept, rejects


SYNTH_SYSTEM = (
    "You are a mathematics problem designer. Reply with STRICT JSON only: an object "
    "with key questions holding a list of objects with keys question and answer. "
    "If told not to provide answers, use an empty string for answer.")


def _synth_prompt(seed_question: str, n: int, emit_answer: bool) -> str:
    rule = ("Provide the correct final answer for each new problem." if emit_answer
            else "Leave every answer field empty; answers are produced downstream.")
    return ("Using the seed problem below, design " + str(n) + " NEW math problems." + NL
            + "Hard requirements:" + NL
            + "- Do NOT paraphrase the seed and do NOT merely swap numbers." + NL
            + "- Vary at least two dimensions: condition structure, framing, solution "
            + "technique, or difficulty (one should be harder than the seed)." + NL
            + "- Each problem must be self-contained, unambiguous and solvable." + NL
            + "- " + rule + NL + "SEED PROBLEM:" + NL + seed_question + NL + "JSON only.")


def llm_synthesize_questions_n2(seed: Dict[str, Any], cfg: Config,
                                llm: LLMClient) -> List[Dict[str, Any]]:
    """synthesize node, modeled as flat_map: 1 seed -> up to n_per_seed rows.
    Anti-homogenization requirements are stated explicitly in the prompt."""
    emit_answer = cfg.synthetic_answer_strategy == "emit"
    data = llm.json_call(cfg.synthesize_model, SYNTH_SYSTEM,
                         _synth_prompt(seed["question"], cfg.n_per_seed, emit_answer),
                         cfg.synth_temperature, ("questions",))
    items = data.get("questions") or []
    out: List[Dict[str, Any]] = []
    for k, item in enumerate(items[:cfg.n_per_seed], start=1):
        item = item or {}
        question = normalize_text(str(item.get("question", "")), cfg.unify_latex_delimiters)
        if len(question) < cfg.min_question_len:
            continue  # keep_valid_subset_and_log
        answer = ""
        if emit_answer:
            answer = normalize_text(str(item.get("answer", "")), cfg.unify_latex_delimiters)
        out.append({"id": seed["id"] + SEP + "syn" + str(k),
                    "seed_id": seed["id"], "origin": "synthetic",
                    "question": question,
                    "question_raw": str(item.get("question", "")),
                    "answer": answer, "answer_raw": answer})
    if len(out) < cfg.n_per_seed:
        LOG.debug("seed=%s produced %d of %d", seed["id"], len(out), cfg.n_per_seed)
    return out


SOLVE_SYSTEM = (
    "You are a careful mathematician. Reply with STRICT JSON only with keys "
    "final_answer (string, final result only, no working), solvable (boolean), "
    "note (string).")


def solve_synthetic_question(rec: Dict[str, Any], cfg: Config,
                             llm: LLMClient) -> Optional[Dict[str, Any]]:
    """Closes the FieldMapper blocker: independent self-consistency solve for
    synthetic answers. Drops only on unsolvable majority or no answer majority,
    so the reasoning stage never writes a confident trace for an unverified answer."""
    votes: List[str] = []
    raw: Dict[str, str] = {}
    unsolvable = 0
    k = max(1, cfg.solve_self_consistency_k)
    prompt = ("Solve the problem. If unsolvable or self-contradictory set solvable=false."
              + NL + "PROBLEM:" + NL + rec["question"] + NL + "JSON only.")
    for i in range(k):
        data = llm.json_call(cfg.solve_model, SOLVE_SYSTEM, prompt,
                             0.0 if i == 0 else 0.7, ("final_answer", "solvable"))
        if not data.get("solvable"):
            unsolvable += 1
            continue
        ans = normalize_text(str(data.get("final_answer", "")), cfg.unify_latex_delimiters)
        if not ans:
            continue
        key = answer_key(ans)
        raw.setdefault(key, ans)
        votes.append(key)
    if unsolvable > k // 2:
        LOG.debug("solve: unsolvable majority for %s", rec["id"])
        return None
    if not votes:
        return None
    winner, count = Counter(votes).most_common(1)[0]
    needed = (len(votes) // 2) + 1 if len(votes) > 1 else 1
    if count < needed:
        LOG.debug("solve: no answer majority for %s", rec["id"])
        return None
    out = dict(rec)
    out["answer"] = raw[winner]
    out["answer_raw"] = raw[winner]
    out["solve_agreement"] = round(count / float(len(votes)), 4)
    return out


KEEP_FIELDS = ("id", "seed_id", "origin", "question", "answer", "question_raw",
               "answer_raw", "validation_confidence", "solve_agreement")


def union_records(seeds: List[Dict[str, Any]],
                  synthetics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """merge node with assert_unique_id."""
    pool: List[Dict[str, Any]] = []
    seen = set()
    for rec in list(seeds) + list(synthetics):
        rid = rec["id"]
        if rid in seen:
            raise ContractViolation("assert_unique_id failed at merge: " + rid)
        seen.add(rid)
        pool.append({k: rec.get(k) for k in KEEP_FIELDS})
    return pool


REASONING_SYSTEM = (
    "You are a mathematics tutor writing training-grade solutions. Reply with STRICT "
    "JSON only with keys reasoning_trace (numbered step-by-step derivation ending at "
    "the final answer) and final_answer (final result only).")


def _reasoning_prompt(rec: Dict[str, Any], cfg: Config, has_answer: bool) -> str:
    if cfg.answer_form == "with_solution" and has_answer:
        return ("Reformat the existing solution into a clean numbered derivation. Do not "
                "change the mathematics." + NL + "PROBLEM:" + NL + rec["question"] + NL
                + "EXISTING SOLUTION:" + NL + rec["answer"] + NL + "JSON only.")
    if has_answer:
        return ("Write a step-by-step derivation reaching the given answer. If your own "
                "derivation contradicts it, report your derivation honestly." + NL
                + "PROBLEM:" + NL + rec["question"] + NL + "GIVEN ANSWER:" + NL
                + rec["answer"] + NL + "JSON only.")
    return ("Solve step by step and report the final answer." + NL + "PROBLEM:" + NL
            + rec["question"] + NL + "JSON only.")


def llm_generate_reasoning_trace(rec: Dict[str, Any], cfg: Config,
                                 llm: LLMClient) -> Dict[str, Any]:
    """reasoning node. GENERATE by default, REFORMAT when answer_form=with_solution.
    G4: the prompt is built once and reused across retries. On persistent mismatch
    the record is kept with trace_consistent=false rather than silently accepted."""
    has_answer = bool(rec.get("answer"))
    prompt = _reasoning_prompt(rec, cfg, has_answer)
    check_match = has_answer and cfg.require_final_answer_match
    attempts = 1 + (cfg.reasoning_match_retries if check_match else 0)
    trace = ""
    derived = ""
    consistent: Optional[bool] = None
    for attempt in range(attempts):
        data = llm.json_call(cfg.reasoning_model, REASONING_SYSTEM, prompt,
                             cfg.reasoning_temperature,
                             ("reasoning_trace", "final_answer"))
        trace = str(data.get("reasoning_trace", "")).strip()
        derived = normalize_text(str(data.get("final_answer", "")),
                                 cfg.unify_latex_delimiters)
        if not check_match:
            break
        consistent = answer_key(derived) == answer_key(rec["answer"])
        if consistent:
            break
        LOG.debug("reasoning mismatch for %s (attempt %d)", rec["id"], attempt + 1)
    out = dict(rec)
    out["reasoning_trace"] = trace or None
    if has_answer:
        out["trace_consistent"] = consistent if check_match else None
    else:
        out["answer"] = derived
        out["trace_consistent"] = None
    return out


def _keep_rank(rec: Dict[str, Any]) -> Tuple[int, int, int]:
    """keep_priority: origin=seed, then trace_consistent, then longest trace."""
    origin_rank = 0 if rec.get("origin") == "seed" else 1
    trace_rank = 0 if rec.get("trace_consistent") is not False else 1
    return (origin_rank, trace_rank, -len(rec.get("reasoning_trace") or ""))


def _dedup_text(rec: Dict[str, Any], cfg: Config) -> str:
    if cfg.dedup_field == "question_answer":
        return (rec.get("question") or "") + " " + (rec.get("answer") or "")
    return rec.get("question") or ""


def ngram_deduplicate(records: List[Dict[str, Any]], cfg: Config
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """dedup node. Inverted-index exact jaccard, or MinHash-LSH candidate
    generation above minhash_threshold_records.

    G2: the LSH branch keeps id -> signature so every hit is confirmed against the
    exact jaccard threshold (LSH is approximate) and duplicate_of is a record id."""
    order = sorted(range(len(records)), key=lambda i: _keep_rank(records[i]))
    sigs = [word_ngrams(_dedup_text(rec, cfg), cfg.ngram_n) for rec in records]

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    minhash = _load_minhash() if len(records) > cfg.minhash_threshold_records else None
    if len(records) > cfg.minhash_threshold_records and minhash is None:
        LOG.warning("datasketch missing; falling back to inverted-index jaccard "
                    "(%d records, this may be slow)", len(records))

    if minhash is not None:
        minhash_cls, lsh_cls = minhash
        lsh = lsh_cls(threshold=cfg.dedup_threshold, num_perm=cfg.minhash_num_perm)
        sig_by_id: Dict[str, set] = {}
        for i in order:
            rec = records[i]
            mh = minhash_cls(num_perm=cfg.minhash_num_perm)
            for gram in sigs[i]:
                mh.update(gram.encode("utf-8"))
            dup_id = None
            for hit_id in lsh.query(mh):
                if jaccard(sigs[i], sig_by_id.get(hit_id, set())) >= cfg.dedup_threshold:
                    dup_id = hit_id
                    break
            protected = cfg.dedup_protect_seeds and rec.get("origin") == "seed"
            if dup_id is not None and not protected:
                removed.append({"id": rec["id"], "duplicate_of": dup_id,
                                "method": "minhash_lsh"})
                continue
            lsh.insert(rec["id"], mh)
            sig_by_id[rec["id"]] = sigs[i]
            kept.append(rec)
        return kept, removed

    index: Dict[str, List[int]] = defaultdict(list)
    for i in order:
        rec = records[i]
        candidates: Counter = Counter()
        for gram in sigs[i]:
            for j in index[gram]:
                candidates[j] += 1
        dup_of = None
        for j, _shared in candidates.most_common():
            if jaccard(sigs[i], sigs[j]) >= cfg.dedup_threshold:
                dup_of = j
                break
        protected = cfg.dedup_protect_seeds and rec.get("origin") == "seed"
        if dup_of is not None and not protected:
            removed.append({"id": rec["id"], "duplicate_of": records[dup_of]["id"],
                            "method": "ngram_jaccard"})
            continue
        for gram in sigs[i]:
            index[gram].append(i)
        kept.append(rec)
    return kept, removed


EXPORT_FIELDS = ("id", "seed_id", "origin", "question", "answer", "question_raw",
                 "answer_raw", "reasoning_trace", "trace_consistent",
                 "validation_confidence", "solve_agreement")


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    """F7: atomic write so a crash cannot leave a truncated artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + NL)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def export_dataset(records: List[Dict[str, Any]], cfg: Config) -> List[Dict[str, Any]]:
    rows = [{k: rec.get(k) for k in EXPORT_FIELDS} for rec in records]
    write_jsonl(Path(cfg.output_dir) / "final_dataset.jsonl", rows)
    return rows


def run_pipeline(cfg: Config) -> Dict[str, Any]:
    started = time.time()
    store = StateStore(cfg.state_dir, cfg.run_id, cfg.fingerprint())
    out_dir = Path(cfg.output_dir)
    budget_stopped = False

    raw_rows, ingest_stats = load_dataset(cfg)
    normalized = [text_normalize(row, cfg) for row in raw_rows]
    prefiltered: List[Dict[str, Any]] = []
    prefilter_rejects: List[Dict[str, Any]] = []
    for rec in normalized:
        ok, reason = rule_based_prefilter(rec, cfg)
        if ok:
            prefiltered.append(rec)
        else:
            rejected = dict(rec)
            rejected["reject_reason"] = reason
            prefilter_rejects.append(rejected)
    LOG.info("prefilter kept=%d rejected=%d", len(prefiltered), len(prefilter_rejects))

    llm = LLMClient(cfg)

    validated, stopped = parallel_stage(
        "validate", prefiltered, lambda r: validate_math_question(r, cfg, llm), cfg, store)
    budget_stopped = budget_stopped or stopped
    valid_seeds, rejects = filter_invalid(validated, cfg)
    write_jsonl(out_dir / "rejects.jsonl", prefilter_rejects + rejects)
    write_jsonl(out_dir / "valid_seeds_ckpt.jsonl", valid_seeds)
    LOG.info("validate kept=%d rejected=%d", len(valid_seeds), len(rejects))

    synth_batches, stopped = parallel_stage(
        "synthesize", valid_seeds,
        lambda r: llm_synthesize_questions_n2(r, cfg, llm), cfg, store)
    budget_stopped = budget_stopped or stopped
    synthesized = [item for batch in synth_batches for item in (batch or [])]
    LOG.info("synthesize produced=%d from seeds=%d", len(synthesized), len(valid_seeds))

    solved = synthesized
    if cfg.synthetic_answer_strategy == "solve":
        solved, stopped = parallel_stage(
            "solve_synthetic", synthesized,
            lambda r: solve_synthetic_question(r, cfg, llm), cfg, store)
        budget_stopped = budget_stopped or stopped
        LOG.info("solve kept=%d of %d", len(solved), len(synthesized))

    validated_synthetic = solved
    synth_rejects: List[Dict[str, Any]] = []
    if cfg.validate_synth and cfg.synthetic_answer_strategy != "reasoning_derived":
        checked, stopped = parallel_stage(
            "validate_synth", solved,
            lambda r: validate_math_question(r, cfg, llm), cfg, store)
        budget_stopped = budget_stopped or stopped
        validated_synthetic, synth_rejects = filter_invalid(checked, cfg)
        write_jsonl(out_dir / "rejects_synthetic.jsonl", synth_rejects)
        LOG.info("validate_synth kept=%d rejected=%d",
                 len(validated_synthetic), len(synth_rejects))

    question_pool = union_records(valid_seeds, validated_synthetic)
    with_reasoning, stopped = parallel_stage(
        "reasoning", question_pool,
        lambda r: llm_generate_reasoning_trace(r, cfg, llm), cfg, store)
    budget_stopped = budget_stopped or stopped

    if cfg.drop_inconsistent_traces:
        bad = [r for r in with_reasoning if r.get("trace_consistent") is False]
        with_reasoning = [r for r in with_reasoning
                          if r.get("trace_consistent") is not False]
        write_jsonl(out_dir / "low_confidence_traces.jsonl", bad)

    deduplicated, removed = ngram_deduplicate(with_reasoning, cfg)
    write_jsonl(out_dir / "dedup_removed.jsonl", removed)
    final_rows = export_dataset(deduplicated, cfg)

    per_seed: Counter = Counter()
    for rec in deduplicated:
        if rec.get("origin") == "synthetic":
            per_seed[rec.get("seed_id")] += 1
    hist = Counter(per_seed.get(s["id"], 0) for s in valid_seeds)

    report = {
        "run_id": cfg.run_id,
        "pipeline_version": "v3",
        "idempotency_key": cfg.idempotency_key(),
        "budget_stopped": budget_stopped,
        "duration_seconds": round(time.time() - started, 1),
        "stage_counts": {
            "ingest": ingest_stats,
            "prefilter_kept": len(prefiltered),
            "prefilter_rejected": len(prefilter_rejects),
            "valid_seeds": len(valid_seeds),
            "validation_rejected": len(rejects),
            "synthesized": len(synthesized),
            "synthetic_after_solve": len(solved),
            "synthetic_after_gates": len(validated_synthetic),
            "synthetic_rejected": len(synth_rejects),
            "question_pool": len(question_pool),
            "with_reasoning": len(with_reasoning),
            "dedup_removed": len(removed),
            "final": len(final_rows),
        },
        "rates": {
            "seed_pass_rate": round(len(valid_seeds) / max(1, len(prefiltered)), 4),
            "synthetic_pass_rate": round(
                len(validated_synthetic) / max(1, len(synthesized)), 4),
            "dedup_removal_rate": round(len(removed) / max(1, len(with_reasoning)), 4),
            "trace_inconsistent": sum(1 for r in final_rows
                                      if r.get("trace_consistent") is False),
        },
        "per_seed_retention": {
            "histogram": {str(k): v for k, v in sorted(hist.items())},
            "note": "synthesis_count_per_seed=2 is an upper-bound target, not a guarantee",
        },
        "llm_usage": {
            "calls": llm.calls,
            "input_tokens": llm.in_tokens,
            "output_tokens": llm.out_tokens,
            "estimated_usd": round(llm.est_usd, 2),
            "transient_failures": llm.failures,
        },
        "open_decisions": {
            "synthetic_answer_strategy": cfg.synthetic_answer_strategy,
            "answer_form": cfg.answer_form,
            "dedup": {"n": cfg.ngram_n, "threshold": cfg.dedup_threshold,
                      "field": cfg.dedup_field,
                      "protect_seeds": cfg.dedup_protect_seeds},
            "models": {"validate": cfg.validate_model, "arbiter": cfg.arbiter_model,
                       "synthesize": cfg.synthesize_model, "solve": cfg.solve_model,
                       "reasoning": cfg.reasoning_model},
        },
        "artifacts": {
            "final_dataset": str(out_dir / "final_dataset.jsonl"),
            "rejects": str(out_dir / "rejects.jsonl"),
            "rejects_synthetic": str(out_dir / "rejects_synthetic.jsonl"),
            "dedup_removed": str(out_dir / "dedup_removed.jsonl"),
            "dead_letter": str(Path(cfg.state_dir) / cfg.run_id / "dead_letter.jsonl"),
        },
    }
    (out_dir / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Math QA clean/synthesize/reason/dedup")
    ap.add_argument("--input", required=True, help="jsonl or json with id/question/answer")
    ap.add_argument("--output-dir", default="out")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--max-records", type=int, default=None)
    ap.add_argument("--dry-run-sample", type=int, default=None,
                    help="calibration run on first N records; try 300-500 first")
    ap.add_argument("--max-usd", type=float, default=50.0, help="hard cost ceiling")
    ap.add_argument("--synthetic-answer-strategy", default="solve",
                    choices=["solve", "emit", "reasoning_derived"])
    ap.add_argument("--answer-form", default="unknown",
                    choices=["unknown", "numeric", "latex", "natural_language",
                             "with_solution"])
    ap.add_argument("--ngram-n", type=int, default=5)
    ap.add_argument("--dedup-threshold", type=float, default=0.8)
    ap.add_argument("--dedup-field", default="question",
                    choices=["question", "question_answer"])
    ap.add_argument("--allow-seed-removal", action="store_true",
                    help="let dedup delete original seed rows; seeds protected by default")
    ap.add_argument("--drop-inconsistent-traces", action="store_true")
    args = ap.parse_args(argv)

    cfg = Config(input_path=args.input,
                 output_dir=args.output_dir,
                 state_dir=args.state_dir,
                 workers=args.workers,
                 max_records=args.dry_run_sample or args.max_records,
                 max_usd=args.max_usd,
                 synthetic_answer_strategy=args.synthetic_answer_strategy,
                 answer_form=args.answer_form,
                 ngram_n=args.ngram_n,
                 dedup_threshold=args.dedup_threshold,
                 dedup_field=args.dedup_field,
                 dedup_protect_seeds=not args.allow_seed_removal,
                 drop_inconsistent_traces=args.drop_inconsistent_traces)
    report = run_pipeline(cfg)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
