'''
DataFlow Pipeline: Math QA Clean / Synthesize / Reason / Dedup
Version: v2  (repair round 1 for Validator run 02fd98bc)

Pipeline (12 nodes, per ResearchPlanner.plan.dag)
    ingest -> validate_seed -> filter_seed -> gate(pass_rate + error_rate)
           -> synthesize -> validate_synth -> filter_synth -> [optional topup]
           -> merge -> reasoning -> judge(deferred equivalence) -> filter_trace
           -> conflict_check -> dedup -> report

Input  (jsonl): id, question, answer
Output (jsonl): id, seed_id, question, answer, reasoning_trace, provenance

Repairs in v2 (all Validator critical/high hints)
    C1  extract_json moved INSIDE the retry loop for every LLM stage; parse_error
        and truncation are separate retriable classes; synthesize gets 2 parse
        retries (plan.synthesize.retry_on_parse_error=2). Parse failures surface as
        stage errors (error_rate), never as 'invalid question'.
    C2  validator max_tokens 1536 (env MAX_TOKENS_VALIDATE) and llm.call returns
        stop_reason; stop_reason=='max_tokens' is retried with a larger budget.
        Reasoning switched from JSON to tag delimiters <trace>...</trace>
        <final>...</final>, removing the un-escaped-newline failure mode.
    C3  test cases: 12 offline cases runnable with `--selftest` (no API key, no
        network). See TEST_CASES / run_self_test.
    H1  price_of(): longest-prefix match, fail-closed on unknown models, plus a
        preflight check so an unpriced model aborts before any spend.
    H2  checkpoint keys include a per-stage fingerprint of (model, prompt, K,
        max_tokens, retries); checkpoints live under a config-fingerprint dir.
    H3  gate: pass_rate = valid / successfully-validated; separate error_rate gate
        (>10% halts and points at rate limits / parsing).
    M1  halt path never touches final_dataset.jsonl; normal export is tmp+os.replace.
    M2  any exception (incl. AssertionError from merge asserts, now explicit raises)
        writes run_report before propagating.
    M3  answer conflict check runs BEFORE dedup and again inside near-dup clusters.
    M4  validator_k split into seed/synth; validate_synth defaults to a different
        model and K=3 to cut same-model self-confirmation bias.
    M5  sympy/latex imports are lazy with graceful degradation; sympy pinned 1.12.*.
    M6  sympy guarded by expression-length cap and numeric-first comparison; LLM
        equivalence judging is a decoupled batch stage, off the reasoning path.
    L1  dedup params exposed via env; per-seed survival histogram in the report.
    L2  optional one-round synthesis top-up (ENABLE_SYNTH_TOPUP=1, default off).
    L3  percent answers parsed as value/100; judge calls de-duplicated by key.

Guardrails: reads INPUT_PATH only, writes OUTPUT_DIR only, no production access.
Hard budget cap (BUDGET_USD, default 500): warn at 90%, stop at 100% with all
completed work checkpointed for resume.

Author: PipelineBuilder Agent
'''

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PIPELINE_VERSION = 'v2'
RUN_ID = os.getenv('RUN_ID', '02fd98bc-ff7c-45d5-83aa-2fe80a16773f')

TEST_CASES = [
    'ingest_schema_and_exact_dedup',
    'validator_good_item_fenced_json',
    'validator_non_math_item',
    'validator_wrong_answer',
    'validator_truncation_is_retried_not_rejected',
    'synthesize_two_parse_errors_then_success',
    'reasoning_multiline_cot_via_tags',
    'math_equal_percent_vs_decimal',
    'math_equal_latex_fraction_vs_decimal',
    'dedup_prefers_seed_and_flags_answer_conflict',
    'gate_metrics_exclude_infra_errors',
    'export_atomic_and_halt_does_not_truncate',
    'price_lookup_prefix_match_and_fail_closed',
]


class BudgetExceeded(RuntimeError):
    pass


class QualityGateFailed(RuntimeError):
    pass


class ParseFailure(RuntimeError):
    pass


class UnpricedModel(RuntimeError):
    pass


class ExportContractError(RuntimeError):
    pass


class MergeContractError(RuntimeError):
    pass


# ------------------------------------------------------------------ pricing
# (model_prefix, (usd per 1M input tokens, usd per 1M output tokens)).
# Longest matching prefix wins. Values are estimates and MUST be calibrated
# against real billing before the cost cap can be trusted.
PRICES: List[Tuple[str, Tuple[float, float]]] = [
    ('claude-opus-4-1', (15.0, 75.0)),
    ('claude-opus-4-5', (5.0, 25.0)),
    ('claude-opus-4', (15.0, 75.0)),
    ('claude-sonnet-4-5', (3.0, 15.0)),
    ('claude-sonnet-4', (3.0, 15.0)),
    ('claude-haiku-4-5', (1.0, 5.0)),
    ('claude-3-5-haiku', (0.8, 4.0)),
]


def price_of(model: str) -> Tuple[float, float]:
    '''H1: prefix match + fail-closed. Never silently bill an unknown model at the
    cheapest tier (that made the hard budget cap ineffective in v1).'''
    table = list(PRICES)
    extra = os.getenv('EXTRA_PRICES')
    if extra:
        for prefix, pair in json.loads(extra).items():
            table.append((prefix, (float(pair[0]), float(pair[1]))))
    best: Optional[Tuple[str, Tuple[float, float]]] = None
    for prefix, pair in table:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, pair)
    if best is None:
        raise UnpricedModel(
            'unpriced model %r: register it in PRICES or EXTRA_PRICES before running' % model)
    return best[1]


@dataclass
class Config:
    input_path: str = os.getenv('INPUT_PATH', 'data/seed_math_qa.jsonl')
    output_dir: str = os.getenv('OUTPUT_DIR', 'artifacts/' + RUN_ID)
    model_validator_seed: str = os.getenv('MODEL_VALIDATOR_SEED', 'claude-sonnet-4-5')
    model_validator_synth: str = os.getenv('MODEL_VALIDATOR_SYNTH', 'claude-opus-4-5')
    model_synth: str = os.getenv('MODEL_SYNTH', 'claude-sonnet-4-5')
    model_reasoning: str = os.getenv('MODEL_REASONING', 'claude-sonnet-4-5')
    model_judge: str = os.getenv('MODEL_JUDGE', 'claude-haiku-4-5')
    n_per_seed: int = 2
    validator_k_seed: int = int(os.getenv('VALIDATOR_K_SEED', '1'))
    validator_k_synth: int = int(os.getenv('VALIDATOR_K_SYNTH', '3'))
    max_tokens_validate: int = int(os.getenv('MAX_TOKENS_VALIDATE', '1536'))
    max_tokens_synth: int = int(os.getenv('MAX_TOKENS_SYNTH', '2048'))
    max_tokens_reasoning: int = int(os.getenv('MAX_TOKENS_REASONING', '2048'))
    parse_retries: int = 1
    parse_retries_synth: int = 2
    mismatch_retry: int = 1
    concurrency_validate: int = int(os.getenv('CONCURRENCY_VALIDATE', '20'))
    concurrency_synth: int = int(os.getenv('CONCURRENCY_SYNTH', '20'))
    concurrency_reasoning: int = int(os.getenv('CONCURRENCY_REASONING', '25'))
    concurrency_judge: int = int(os.getenv('CONCURRENCY_JUDGE', '10'))
    min_seed_pass_rate: float = float(os.getenv('MIN_SEED_PASS_RATE', '0.4'))
    max_error_rate: float = float(os.getenv('MAX_ERROR_RATE', '0.10'))
    budget_usd: float = float(os.getenv('BUDGET_USD', '500'))
    budget_warn_ratio: float = 0.90
    ngram_n: int = int(os.getenv('NGRAM_N', '5'))
    dedup_threshold: float = float(os.getenv('DEDUP_THRESHOLD', '0.8'))
    num_perm: int = 128
    sympy_max_len: int = 200
    enable_synth_topup: bool = os.getenv('ENABLE_SYNTH_TOPUP', '0') == '1'
    sample_size: int = int(os.getenv('SAMPLE_SIZE', '0'))  # 0 = full run


def log(msg: str) -> None:
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def _hash(obj: Any, n: int = 10) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()[:n]


# ------------------------------------------------------------------- prompts
VALIDATE_PROMPT = (
    'You are a strict math reviewer. Judge two things independently.\n'
    '1) is_valid_question: is this a well-posed, self-consistent, solvable math '
    'problem (not a fragment, not non-math, no missing data, no contradiction)?\n'
    '2) is_answer_correct: solve it yourself, then decide whether the given answer '
    'is mathematically correct (formatting differences are acceptable).\n'
    'Keep any internal work brief; the reply must fit well within the token limit.\n\n'
    'QUESTION:\n%s\n\nGIVEN ANSWER:\n%s\n\n'
    'Reply with a single-line JSON object only:\n'
    '{"is_valid_question": true|false, "is_answer_correct": true|false, '
    '"validation_reason": "<one short sentence, no newlines>"}'
)

SYNTH_PROMPT = (
    'Using the seed problem below as inspiration, write %d NEW math problems.\n'
    'Rules:\n'
    '- same topic and roughly the same difficulty (at most +/-1 level of variation)\n'
    '- do NOT copy or lightly reword the seed; change the setup, numbers and framing\n'
    '- the new problems must differ substantially from EACH OTHER\n'
    '- each must be self-contained with a single unambiguous answer\n'
    '- solve each one and give the final answer only (no derivation)\n\n'
    'SEED QUESTION:\n%s\n\nSEED ANSWER:\n%s\n\n'
    'Reply with JSON only, no markdown fences:\n'
    '{"problems": [{"question": "...", "answer": "..."}]}'
)

# C2: tags instead of JSON so multi-line CoT cannot break parsing.
REASONING_PROMPT = (
    'Write a clear step-by-step reasoning trace for the math problem below.\n'
    'The known answer is authoritative; your derivation must genuinely lead to it. '
    'Do not assert the answer without justification. If the known answer looks wrong, '
    'put your own result in <final> instead of faking the steps.\n\n'
    'QUESTION:\n%s\n\nKNOWN ANSWER:\n%s\n\n'
    'Output format (plain text, no JSON, no markdown fences):\n'
    '<trace>\nStep 1: ...\nStep 2: ...\n</trace>\n'
    '<final>final answer only</final>'
)

JUDGE_PROMPT = (
    'Are these two math answers mathematically equivalent? Ignore formatting, unit '
    'placement and variable prefixes; compare value only.\n'
    'Answer A: %s\nAnswer B: %s\n'
    'Reply with a single-line JSON object only: {"equivalent": true|false}'
)


def stage_specs(cfg: Config) -> Dict[str, Any]:
    '''H2: everything that can change an LLM result feeds the checkpoint key.'''
    validate_common = [VALIDATE_PROMPT, cfg.max_tokens_validate, cfg.parse_retries]
    synth_common = [SYNTH_PROMPT, cfg.n_per_seed, cfg.max_tokens_synth, cfg.parse_retries_synth]
    return {
        'validate_seed': [cfg.model_validator_seed, cfg.validator_k_seed] + validate_common,
        'synthesize': [cfg.model_synth] + synth_common,
        'validate_synth': [cfg.model_validator_synth, cfg.validator_k_synth] + validate_common,
        'synthesize_topup': [cfg.model_synth, 'topup'] + synth_common,
        'validate_synth_topup': [cfg.model_validator_synth, cfg.validator_k_synth, 'topup'] + validate_common,
        'reasoning': [cfg.model_reasoning, REASONING_PROMPT, cfg.max_tokens_reasoning,
                      cfg.mismatch_retry, cfg.parse_retries],
        'judge': [cfg.model_judge, JUDGE_PROMPT],
    }


def stage_fp(cfg: Config, stage: str) -> str:
    return _hash(stage_specs(cfg)[stage])


def config_fp(cfg: Config) -> str:
    return _hash(stage_specs(cfg), 12)


def idempotency_key(cfg: Config) -> str:
    return 'pipeline_math_qa_%s_%s' % (PIPELINE_VERSION, config_fp(cfg))


# ---------------------------------------------------------------- cost guard
class CostTracker:
    def __init__(self, budget_usd: float, warn_ratio: float = 0.9) -> None:
        self._lock = threading.Lock()
        self.budget = budget_usd
        self.warn_ratio = warn_ratio
        self.total = 0.0
        self.calls = 0
        self.by_stage: Dict[str, float] = {}
        self.tokens: Dict[str, int] = {'input': 0, 'output': 0}
        self._warned = False

    def add(self, stage: str, model: str, in_tok: int, out_tok: int) -> float:
        p_in, p_out = price_of(model)
        cost = in_tok / 1e6 * p_in + out_tok / 1e6 * p_out
        with self._lock:
            self.total += cost
            self.calls += 1
            self.by_stage[stage] = self.by_stage.get(stage, 0.0) + cost
            self.tokens['input'] += in_tok
            self.tokens['output'] += out_tok
            total = self.total
            warn = (not self._warned) and total >= self.budget * self.warn_ratio
            if warn:
                self._warned = True
        if warn:
            log('COST WARNING: $%.2f / $%.2f (%.0f%%)'
                % (total, self.budget, 100 * total / self.budget))
        if total >= self.budget:
            raise BudgetExceeded('budget cap hit: $%.2f >= $%.2f' % (total, self.budget))
        return cost

    def report(self) -> Dict[str, Any]:
        with self._lock:
            return {'total_usd': round(self.total, 4), 'budget_usd': self.budget,
                    'llm_calls': self.calls, 'tokens': dict(self.tokens),
                    'by_stage_usd': {k: round(v, 4) for k, v in self.by_stage.items()}}


# ------------------------------------------------------- checkpoint / state
class StateStore:
    '''append-only jsonl checkpoint keyed by (stage, stage_fingerprint, item_id).'''

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._cache[rec['key']] = rec['value']
        self._fh = self.path.open('a', encoding='utf-8')

    def get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value
            self._fh.write(json.dumps({'key': key, 'value': value}, ensure_ascii=False) + '\n')
            self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


# ------------------------------------------------------------------ llm i/o
@dataclass
class LLMResult:
    text: str
    stop_reason: str


class LLM:
    def __init__(self, tracker: CostTracker) -> None:
        import anthropic  # lazy: --selftest runs without the SDK installed
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise RuntimeError('ANTHROPIC_API_KEY not set')
        kwargs: Dict[str, Any] = {'api_key': api_key}
        base_url = os.getenv('ANTHROPIC_BASE_URL')
        if base_url:
            kwargs['base_url'] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.tracker = tracker

    def call(self, stage: str, model: str, prompt: str, max_tokens: int,
             temperature: float = 0.0, attempts: int = 3) -> LLMResult:
        last_err: Optional[Exception] = None
        for i in range(attempts):
            try:
                resp = self.client.messages.create(
                    model=model, max_tokens=max_tokens, temperature=temperature,
                    messages=[{'role': 'user', 'content': prompt}])
                self.tracker.add(stage, model, resp.usage.input_tokens, resp.usage.output_tokens)
                text = ''.join(b.text for b in resp.content
                               if getattr(b, 'type', '') == 'text')
                return LLMResult(text, str(getattr(resp, 'stop_reason', '') or ''))
            except BudgetExceeded:
                raise
            except Exception as exc:  # rate limit / network / transient
                last_err = exc
                time.sleep(min(2 ** i, 20))
        raise RuntimeError('llm_call_failed: %s' % last_err)


def extract_json(text: str) -> Any:
    body = text.strip()
    fence = re.search(r'```(?:json)?\s*(.*?)```', body, re.S)
    if fence:
        body = fence.group(1).strip()
    for opener, closer in (('{', '}'), ('[', ']')):
        start, end = body.find(opener), body.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(body[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError('unparseable_llm_json')


def call_with_parse(llm: Any, stage: str, model: str, prompt: str, max_tokens: int,
                    temperature: float, parser: Callable[[str], Any],
                    retries: int) -> Tuple[Any, List[str]]:
    '''C1/C2: parsing lives inside the retry loop. Truncation (stop_reason ==
    max_tokens) is retried with a larger budget, not treated as a bad item.
    Exhausted retries raise ParseFailure -> counted as a stage error, never as
    an invalid question.'''
    problems: List[str] = []
    for attempt in range(max(0, retries) + 1):
        budget = int(max_tokens * (1.5 ** attempt))
        temp = temperature if attempt == 0 else min(1.0, temperature + 0.2)
        res = llm.call(stage, model, prompt, budget, temp)
        if res.stop_reason == 'max_tokens':
            problems.append('truncated@%d' % budget)
            continue
        try:
            return parser(res.text), problems
        except (ValueError, KeyError, TypeError) as exc:
            problems.append('parse_error:%s' % str(exc)[:80])
    raise ParseFailure('|'.join(problems) or 'unknown_parse_failure')


# ---------------------------------------------------------- normalization
_WS = re.compile(r'\s+')
_LATEX_DROP = (r'\left', r'\right', r'\!', r'\ ')
_LATEX_MAP = ((r'\dfrac', r'\frac'), (r'\tfrac', r'\frac'), (r'\,', ' '), (r'\;', ' '))
_ANSWER_PREFIX = re.compile(r'^[A-Za-z]\s*=\s*')


def collapse_whitespace(text: str) -> str:
    return _WS.sub(' ', text).strip()


def normalize_latex(text: str) -> str:
    out = str(text)
    for tok in _LATEX_DROP:
        out = out.replace(tok, '')
    for src, dst in _LATEX_MAP:
        out = out.replace(src, dst)
    out = re.sub(r'\$+', '$', out)
    return collapse_whitespace(out)


def _strip_answer_noise(value: Any) -> str:
    out = normalize_latex(value).replace('$', '')
    out = _ANSWER_PREFIX.sub('', out.strip())
    return out.rstrip('.').strip()


def _to_float(text: str) -> Optional[float]:
    '''L3: percentages are values, not stripped noise ('50%' == 0.5).'''
    raw = text.strip()
    percent = raw.endswith('%')
    candidate = raw[:-1].strip() if percent else raw
    candidate = candidate.replace(',', '')
    scale = 0.01 if percent else 1.0
    try:
        return float(candidate) * scale
    except ValueError:
        pass
    frac = re.match(r'^\\frac\{(-?[\d\.]+)\}\{(-?[\d\.]+)\}$', candidate)
    if not frac:
        frac = re.match(r'^(-?[\d\.]+)\s*/\s*(-?[\d\.]+)$', candidate)
    if frac:
        try:
            denom = float(frac.group(2))
            if denom != 0:
                return float(frac.group(1)) / denom * scale
        except ValueError:
            return None
    return None


def _sympy_pair(left: str, right: str, cfg: Config):
    '''M5/M6: lazy import, length cap, graceful degradation to None.'''
    if max(len(left), len(right)) > cfg.sympy_max_len:
        return None, None, None
    try:
        import sympy
        from sympy.parsing.sympy_parser import parse_expr
    except Exception:
        return None, None, None
    try:
        from sympy.parsing.latex import parse_latex
    except Exception:
        parse_latex = None

    def build(text: str):
        try:
            if '\\' in text or '{' in text:
                if parse_latex is None:
                    return None
                return parse_latex(text)
            return parse_expr(text.replace('^', '**'))
        except Exception:
            return None

    return sympy, build(left), build(right)


def math_equal_local(a: Any, b: Any, cfg: Config) -> Tuple[Optional[bool], str]:
    '''Local (free) equivalence: string -> numeric -> symbolic.
    Returns (True | False | None, method). None means undecided -> deferred to
    the separate judge stage, so no LLM call happens on the reasoning path.'''
    left, right = _strip_answer_noise(a), _strip_answer_noise(b)
    if left.lower() == right.lower():
        return True, 'string_exact'
    fl, fr = _to_float(left), _to_float(right)
    if fl is not None and fr is not None:
        tol = 1e-6 * max(1.0, abs(fl), abs(fr))
        return (abs(fl - fr) <= tol), 'numeric'
    sympy, el, er = _sympy_pair(left, right, cfg)
    if sympy is not None and el is not None and er is not None:
        try:
            diff = el - er
            num = sympy.N(diff)  # numeric first, cheap
            if num.is_number:
                return (abs(float(num)) < 1e-9), 'symbolic_numeric'
            if sympy.simplify(diff) == 0:
                return True, 'symbolic'
        except Exception:
            pass
    return None, 'undecided'


# ------------------------------------------------------------- parallel map
def _guarded(fn: Callable[[Dict[str, Any]], Dict[str, Any]], item: Dict[str, Any],
             stop: threading.Event) -> Dict[str, Any]:
    if stop.is_set():
        return {'ok': False, 'error': 'aborted'}
    try:
        out = fn(item)
        out['ok'] = True
        return out
    except BudgetExceeded as exc:
        stop.set()
        return {'ok': False, 'error': 'budget_exceeded', 'message': str(exc)}
    except ParseFailure as exc:
        return {'ok': False, 'error': 'parse_error', 'message': str(exc)[:200]}
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'message': str(exc)[:200]}


def parallel_map(stage: str, items: List[Dict[str, Any]],
                 fn: Callable[[Dict[str, Any]], Dict[str, Any]], workers: int,
                 store: StateStore, key_fn: Callable[[Dict[str, Any]], str],
                 stop: threading.Event) -> List[Dict[str, Any]]:
    keys = [key_fn(it) for it in items]
    results: List[Optional[Dict[str, Any]]] = [None] * len(items)
    pending: List[Tuple[Dict[str, Any], str]] = []
    seen: set = set()
    for idx, key in enumerate(keys):
        cached = store.get(key)
        if cached is not None:
            results[idx] = cached
            continue
        if key in seen:
            continue  # L3: identical keys computed once per batch (judge de-dup)
        seen.add(key)
        pending.append((items[idx], key))
    log('%s: %d from checkpoint, %d unique to compute (%d rows)'
        % (stage, sum(1 for r in results if r is not None), len(pending), len(items)))
    computed: Dict[str, Dict[str, Any]] = {}
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_guarded, fn, item, stop): key for item, key in pending}
            for fut in as_completed(futures):
                key = futures[fut]
                out = fut.result()
                if out.get('ok'):
                    store.put(key, out)  # only successes are cached
                computed[key] = out
    if stop.is_set():
        raise BudgetExceeded('%s aborted on budget cap; completed work checkpointed' % stage)
    for idx, key in enumerate(keys):
        if results[idx] is None:
            results[idx] = computed.get(key) or store.get(key) or {'ok': False, 'error': 'missing'}
    return [r for r in results if r is not None]


def gate_metrics(total: int, errors: int, valid: int) -> Dict[str, float]:
    '''H3: pass_rate is measured on successfully validated rows only; infra
    failures show up as error_rate instead of masquerading as bad data.'''
    succeeded = total - errors
    return {'total': float(total), 'errors': float(errors), 'succeeded': float(succeeded),
            'valid': float(valid),
            'error_rate': (errors / total) if total else 0.0,
            'pass_rate': (valid / succeeded) if succeeded else 0.0}


# ------------------------------------------------------------------ parsers
def parse_validation(text: str) -> Dict[str, Any]:
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        raise ValueError('validation_not_object')
    if 'is_valid_question' not in parsed or 'is_answer_correct' not in parsed:
        raise ValueError('validation_missing_keys')
    return parsed


def parse_synth(text: str) -> List[Dict[str, str]]:
    parsed = extract_json(text)
    problems = parsed.get('problems') if isinstance(parsed, dict) else parsed
    if not isinstance(problems, list) or not problems:
        raise ValueError('synth_problems_not_list')
    out: List[Dict[str, str]] = []
    for prob in problems:
        if not isinstance(prob, dict):
            continue
        q, a = prob.get('question'), prob.get('answer')
        if isinstance(q, str) and q.strip() and isinstance(a, str) and a.strip():
            out.append({'question': q, 'answer': a})
    if not out:
        raise ValueError('synth_no_usable_problem')
    return out


_TRACE_RE = re.compile(r'<trace>(.*?)</trace>', re.S)
_FINAL_RE = re.compile(r'<final>(.*?)</final>', re.S)


def parse_reasoning(text: str) -> Dict[str, str]:
    t, f = _TRACE_RE.search(text), _FINAL_RE.search(text)
    if not t or not f:
        raise ValueError('missing_trace_or_final_tag')
    trace, final = t.group(1).strip(), f.group(1).strip()
    if not trace or not final:
        raise ValueError('empty_trace_or_final_tag')
    return {'reasoning_trace': trace, 'final_answer': final}


def parse_judge(text: str) -> Dict[str, Any]:
    parsed = extract_json(text)
    if not isinstance(parsed, dict) or 'equivalent' not in parsed:
        raise ValueError('judge_missing_key')
    return parsed


# ---------------------------------------------------------------- operators
def load_and_check_schema(cfg: Config) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    '''node: ingest (no LLM).'''
    path = Path(cfg.input_path)
    if not path.exists():
        raise FileNotFoundError('input not found: %s' % path)
    kept: List[Dict[str, Any]] = []
    rejects: List[Dict[str, Any]] = []
    seen_ids: set = set()
    seen_q: set = set()
    for lineno, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            rejects.append({'stage': 'ingest', 'lineno': lineno, 'reason': 'bad_json'})
            continue
        missing = [f for f in ('id', 'question', 'answer')
                   if not isinstance(row.get(f), str) or not row.get(f).strip()]
        if missing:
            rejects.append({'stage': 'ingest', 'lineno': lineno, 'id': row.get('id'),
                            'reason': 'missing_or_empty:' + ','.join(missing)})
            continue
        rid = row['id'].strip()
        if rid in seen_ids:
            rejects.append({'stage': 'ingest', 'id': rid, 'reason': 'duplicate_id'})
            continue
        q_norm = normalize_latex(row['question'])
        a_norm = normalize_latex(row['answer'])
        if q_norm.lower() in seen_q:
            rejects.append({'stage': 'ingest', 'id': rid, 'reason': 'exact_duplicate_question'})
            continue
        seen_ids.add(rid)
        seen_q.add(q_norm.lower())
        kept.append({'id': rid, 'question_norm': q_norm, 'answer_norm': a_norm,
                     'question_raw': row['question'], 'answer_raw': row['answer'],
                     'schema_ok': True})
    if cfg.sample_size:
        kept = kept[:cfg.sample_size]
        log('SAMPLE MODE: using first %d rows' % len(kept))
    log('ingest: %d kept, %d rejected' % (len(kept), len(rejects)))
    return kept, rejects


def make_validator(llm: Any, cfg: Config, stage: str, q_field: str, a_field: str,
                   model: str, k: int) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    '''node: validate_math_question. K>1 -> majority vote (self-consistency).'''

    def run(item: Dict[str, Any]) -> Dict[str, Any]:
        votes_q: List[bool] = []
        votes_a: List[bool] = []
        reasons: List[str] = []
        issues: List[str] = []
        prompt = VALIDATE_PROMPT % (item[q_field], item[a_field])
        for i in range(max(1, k)):
            parsed, probs = call_with_parse(
                llm, stage, model, prompt, cfg.max_tokens_validate,
                0.0 if i == 0 else 0.7, parse_validation, cfg.parse_retries)
            issues.extend(probs)
            votes_q.append(bool(parsed.get('is_valid_question')))
            votes_a.append(bool(parsed.get('is_answer_correct')))
            reasons.append(str(parsed.get('validation_reason', ''))[:280])
        half = len(votes_q) / 2.0
        return {'is_valid_question': sum(votes_q) > half,
                'is_answer_correct': sum(votes_a) > half,
                'validation_reason': reasons[0],
                'verification_k': len(votes_q),
                'answer_verified_by': model,
                'parse_issues': issues}

    return run


def make_synthesizer(llm: Any, cfg: Config, stage: str = 'synthesize',
                     id_suffix: str = '') -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    '''node: llm_synthesize_questions_n2. One call produces n problems so the
    model can differentiate them; 2 parse retries per plan params.'''

    def run(seed: Dict[str, Any]) -> Dict[str, Any]:
        prompt = SYNTH_PROMPT % (cfg.n_per_seed, seed['question_norm'], seed['answer_norm'])
        problems, issues = call_with_parse(
            llm, stage, cfg.model_synth, prompt, cfg.max_tokens_synth, 0.9,
            parse_synth, cfg.parse_retries_synth)
        out: List[Dict[str, Any]] = []
        for idx, prob in enumerate(problems[:cfg.n_per_seed], start=1):
            out.append({'id': '%s#s%d%s' % (seed['id'], idx, id_suffix),
                        'seed_id': seed['id'],
                        'synth_question': normalize_latex(prob['question']),
                        'synth_answer': normalize_latex(prob['answer']),
                        'provenance': 'synthetic'})
        return {'items': out, 'parse_issues': issues}

    return run


def union_datasets(valid_seeds: List[Dict[str, Any]],
                   valid_synth: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    '''node: merge. Explicit SELECT-AS renaming + constant columns.
    M2: contract violations raise a real exception class (not assert, which -O
    would strip), and the caller writes run_report before propagating.'''
    merged: List[Dict[str, Any]] = []
    for s in valid_seeds:
        merged.append({'id': s['id'], 'seed_id': s['id'],
                       'question': s['question_norm'], 'answer': s['answer_norm'],
                       'provenance': 'seed',
                       'answer_verified_by': s.get('answer_verified_by'),
                       'verification_k': s.get('verification_k')})
    for s in valid_synth:
        merged.append({'id': s['id'], 'seed_id': s['seed_id'],
                       'question': s['synth_question'], 'answer': s['synth_answer'],
                       'provenance': 'synthetic',
                       'answer_verified_by': s.get('answer_verified_by'),
                       'verification_k': s.get('verification_k')})
    ids = [m['id'] for m in merged]
    if len(set(ids)) != len(ids):
        raise MergeContractError('UNIQUE(id) violated after merge')
    seed_ids = {s['id'] for s in valid_seeds}
    orphans = [m['id'] for m in merged
               if m['provenance'] == 'synthetic' and m['seed_id'] not in seed_ids]
    if orphans:
        raise MergeContractError('seed_id FK violated for %d synthetic rows' % len(orphans))
    log('merge: %d rows (%d seed + %d synthetic)'
        % (len(merged), len(valid_seeds), len(valid_synth)))
    return merged


def make_reasoner(llm: Any, cfg: Config) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    '''node: llm_generate_reasoning_trace. Local consistency check only; ambiguous
    cases are flagged needs_judge and settled in the decoupled judge stage.'''

    def run(item: Dict[str, Any]) -> Dict[str, Any]:
        prompt = REASONING_PROMPT % (item['question'], item['answer'])
        issues: List[str] = []
        last: Dict[str, Any] = {}
        for attempt in range(cfg.mismatch_retry + 1):
            parsed, probs = call_with_parse(
                llm, 'reasoning', cfg.model_reasoning, prompt, cfg.max_tokens_reasoning,
                0.2 if attempt == 0 else 0.6, parse_reasoning, cfg.parse_retries)
            issues.extend(probs)
            verdict, method = math_equal_local(parsed['final_answer'], item['answer'], cfg)
            last = {'reasoning_trace': parsed['reasoning_trace'],
                    'trace_final_answer': parsed['final_answer'],
                    'trace_check': method,
                    'trace_ok': verdict is True,
                    'needs_judge': verdict is None,
                    'trace_attempts': attempt + 1,
                    'parse_issues': issues}
            if verdict is not False:
                return last
        return last

    return run


def make_judge(llm: Any, cfg: Config) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    '''M6: equivalence judging as its own batch stage (own concurrency, own
    checkpoint, de-duplicated by answer-pair key) so it never inflates the
    reasoning stage p99.'''

    def run(item: Dict[str, Any]) -> Dict[str, Any]:
        parsed, issues = call_with_parse(
            llm, 'judge', cfg.model_judge, JUDGE_PROMPT % (item['a'], item['b']),
            256, 0.0, parse_judge, cfg.parse_retries)
        return {'equivalent': bool(parsed.get('equivalent')), 'parse_issues': issues}

    return run


def ngram_shingles(text: str, n: int) -> set:
    tokens = re.findall(r'[a-z0-9]+|[^\sa-z0-9]', text.lower())
    if not tokens:
        return set()
    if len(tokens) < n:
        return {' '.join(tokens)}
    return {' '.join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def answer_conflict_check(items: List[Dict[str, Any]], cfg: Config) -> List[Dict[str, Any]]:
    '''M3: runs BEFORE dedup (in v1 it ran after and was dead code).'''
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(collapse_whitespace(item['question'].lower()), []).append(item)
    conflicts: List[Dict[str, Any]] = []
    for question, group in groups.items():
        if len(group) < 2:
            continue
        base = group[0]
        for other in group[1:]:
            verdict, method = math_equal_local(base['answer'], other['answer'], cfg)
            if verdict is not True:
                conflicts.append({'kind': 'exact_question_answer_conflict',
                                  'question': question[:160], 'method': method,
                                  'ids': [base['id'], other['id']],
                                  'answers': [base['answer'][:80], other['answer'][:80]]})
    return conflicts


def ngram_deduplicate(items: List[Dict[str, Any]], cfg: Config
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    '''node: dedup. MinHash+LSH when datasketch is available, brute-force Jaccard
    otherwise (keeps --selftest runnable). tie_breaker = prefer provenance seed.'''
    try:
        from datasketch import MinHash, MinHashLSH
        backend = 'minhash_lsh'
    except Exception:
        MinHash = MinHashLSH = None
        backend = 'bruteforce_jaccard'
        log('dedup: datasketch unavailable, falling back to brute-force Jaccard')
    order = sorted(range(len(items)),
                   key=lambda i: (0 if items[i]['provenance'] == 'seed' else 1,
                                  items[i]['seed_id'], items[i]['id']))
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    kept_by_id: Dict[str, Dict[str, Any]] = {}
    lsh = MinHashLSH(threshold=cfg.dedup_threshold, num_perm=cfg.num_perm) if MinHashLSH else None
    shingle_store: Dict[str, set] = {}
    for idx in order:
        item = items[idx]
        shingles = ngram_shingles(item['question'], cfg.ngram_n)
        if not shingles:
            dropped.append({'stage': 'dedup', 'id': item['id'], 'reason': 'empty_shingles'})
            continue
        if lsh is not None:
            mh = MinHash(num_perm=cfg.num_perm)
            for sh in shingles:
                mh.update(sh.encode('utf-8'))
            near = list(lsh.query(mh))
        else:
            near = [k for k, s in shingle_store.items()
                    if _jaccard(shingles, s) >= cfg.dedup_threshold]
        if near:
            other_id = near[0]
            other = kept_by_id[other_id]
            verdict, method = math_equal_local(item['answer'], other['answer'], cfg)
            if verdict is not True:
                conflicts.append({'kind': 'near_duplicate_answer_conflict',
                                  'kept_id': other_id, 'dropped_id': item['id'],
                                  'method': method,
                                  'answers': [other['answer'][:80], item['answer'][:80]]})
            dropped.append({'stage': 'dedup', 'id': item['id'],
                            'reason': 'near_duplicate', 'duplicate_of': other_id})
            continue
        if lsh is not None:
            lsh.insert(item['id'], mh)
        else:
            shingle_store[item['id']] = shingles
        kept.append(item)
        kept_by_id[item['id']] = item
    log('dedup(%s): %d kept, %d dropped, %d answer conflicts'
        % (backend, len(kept), len(dropped), len(conflicts)))
    return kept, dropped, conflicts


def _atomic_write(path: Path, lines: List[str]) -> None:
    '''M1: tmp file + os.replace, so a crash mid-write never leaves a partial or
    truncated dataset in place.'''
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as fh:
        for line in lines:
            fh.write(line + '\n')
    os.replace(str(tmp), str(path))


EXPORT_FIELDS = ('id', 'seed_id', 'question', 'answer', 'reasoning_trace', 'provenance')


def export_and_report(cfg: Config, final_items: List[Dict[str, Any]],
                      rejects: List[Dict[str, Any]], funnel: Dict[str, Any],
                      tracker: CostTracker, conflicts: Optional[List[Dict[str, Any]]] = None,
                      write_dataset: bool = True, status: str = 'ok') -> Dict[str, Any]:
    '''node: report. write_dataset=False on halt/failure so a previous good
    final_dataset.jsonl is never truncated (M1).'''
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / 'final_dataset.jsonl'
    if write_dataset:
        lines: List[str] = []
        for item in final_items:
            row = {}
            for f in EXPORT_FIELDS:
                value = item.get(f)
                if not isinstance(value, str) or not value.strip():
                    raise ExportContractError('null/empty %s on row %r' % (f, item.get('id')))
                row[f] = value
            lines.append(json.dumps(row, ensure_ascii=False))
        _atomic_write(data_path, lines)
    _atomic_write(out_dir / 'reject_log.jsonl',
                  [json.dumps(r, ensure_ascii=False) for r in rejects])
    per_seed: Dict[str, int] = {}
    for item in final_items:
        if item.get('provenance') == 'synthetic':
            per_seed[item['seed_id']] = per_seed.get(item['seed_id'], 0) + 1
    histogram: Dict[str, int] = {}
    for count in per_seed.values():
        histogram[str(count)] = histogram.get(str(count), 0) + 1
    reason_hist: Dict[str, int] = {}
    for rec in rejects:
        key = '%s:%s' % (rec.get('stage'), str(rec.get('reason'))[:60])
        reason_hist[key] = reason_hist.get(key, 0) + 1
    report = {
        'run_id': RUN_ID,
        'pipeline_version': PIPELINE_VERSION,
        'status': status,
        'idempotency_key': idempotency_key(cfg),
        'config': asdict(cfg),
        'funnel_report': funnel,
        'surviving_synth_per_seed_histogram': histogram,
        'reject_reason_histogram': reason_hist,
        'answer_conflicts': (conflicts or [])[:50],
        'answer_conflict_count': len(conflicts or []),
        'known_deviations': [
            'synthesis_count_per_seed=2 is best-effort unless ENABLE_SYNTH_TOPUP=1',
        ],
        'cost_report': tracker.report(),
        'dataset_written': write_dataset,
        'output_path': str(data_path),
    }
    _atomic_write(out_dir / 'run_report.json',
                  [json.dumps(report, ensure_ascii=False, indent=2)])
    return report


def preflight(cfg: Config) -> None:
    for model in (cfg.model_validator_seed, cfg.model_validator_synth, cfg.model_synth,
                  cfg.model_reasoning, cfg.model_judge):
        price_of(model)  # H1: unpriced model aborts before any spend


# ----------------------------------------------------------------- pipeline
def pipeline(cfg: Optional[Config] = None, llm: Optional[Any] = None) -> Dict[str, Any]:
    cfg = cfg or Config()
    preflight(cfg)
    log('idempotency_key=%s' % idempotency_key(cfg))
    tracker = CostTracker(cfg.budget_usd, cfg.budget_warn_ratio)
    llm = llm or LLM(tracker)
    stop = threading.Event()
    ckpt_dir = Path(cfg.output_dir) / 'checkpoints' / config_fp(cfg)
    stage_names = ('validate_seed', 'synthesize', 'validate_synth', 'synthesize_topup',
                   'validate_synth_topup', 'reasoning', 'judge')
    stores = {name: StateStore(str(ckpt_dir / ('%s.jsonl' % name))) for name in stage_names}
    fps = {name: stage_fp(cfg, name) for name in stage_names}
    rejects: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    funnel: Dict[str, Any] = {}

    def keyer(stage: str) -> Callable[[Dict[str, Any]], str]:
        return lambda it: '%s:%s:%s' % (stage, fps[stage], it['id'])

    def collect_validation(stage: str, items: List[Dict[str, Any]], results: List[Dict[str, Any]],
                           carry: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
                           ) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
        valid: List[Dict[str, Any]] = []
        errors = 0
        for item, res in zip(items, results):
            if not res.get('ok'):
                errors += 1
                rejects.append({'stage': stage, 'id': item['id'], 'reason': res.get('error'),
                                'message': res.get('message')})
                continue
            if res['is_valid_question'] and res['is_answer_correct']:
                valid.append(carry(item, res))
            else:
                rejects.append({'stage': stage.replace('validate', 'filter'), 'id': item['id'],
                                'reason': ('invalid_question' if not res['is_valid_question']
                                           else 'wrong_answer'),
                                'message': res.get('validation_reason')})
        return valid, gate_metrics(len(items), errors, len(valid))

    def carry_seed(item: Dict[str, Any], res: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(item)
        out['verification_k'] = res['verification_k']
        out['answer_verified_by'] = res['answer_verified_by']
        return out

    try:
        # 1. ingest
        seeds, ingest_rejects = load_and_check_schema(cfg)
        rejects.extend(ingest_rejects)
        funnel['input_rows_kept_after_ingest'] = len(seeds)
        if not seeds:
            raise QualityGateFailed('no valid rows after ingest')

        # 2/3. validate_seed + filter_seed
        seed_results = parallel_map(
            'validate_seed', seeds,
            make_validator(llm, cfg, 'validate_seed', 'question_norm', 'answer_norm',
                           cfg.model_validator_seed, cfg.validator_k_seed),
            cfg.concurrency_validate, stores['validate_seed'], keyer('validate_seed'), stop)
        valid_seeds, seed_metrics = collect_validation('validate_seed', seeds, seed_results,
                                                       carry_seed)
        funnel['validate_seed'] = seed_metrics
        funnel['valid_seeds'] = len(valid_seeds)

        # 4. gate_filter_rate (H3)
        log('gate: pass_rate=%.3f error_rate=%.3f'
            % (seed_metrics['pass_rate'], seed_metrics['error_rate']))
        if seed_metrics['error_rate'] > cfg.max_error_rate:
            raise QualityGateFailed(
                'validate_seed error_rate %.3f > %.2f: infrastructure issue (rate limits / '
                'parsing), not data quality. Inspect reject_log before re-running.'
                % (seed_metrics['error_rate'], cfg.max_error_rate))
        if seed_metrics['pass_rate'] < cfg.min_seed_pass_rate:
            raise QualityGateFailed(
                'seed pass rate %.3f < %.2f on successfully validated rows: halted before '
                'downstream LLM spend' % (seed_metrics['pass_rate'], cfg.min_seed_pass_rate))

        # 5. synthesize
        synth_results = parallel_map(
            'synthesize', valid_seeds, make_synthesizer(llm, cfg), cfg.concurrency_synth,
            stores['synthesize'], keyer('synthesize'), stop)
        synth_raw: List[Dict[str, Any]] = []
        synth_errors = 0
        for seed, res in zip(valid_seeds, synth_results):
            if not res.get('ok'):
                synth_errors += 1
                rejects.append({'stage': 'synthesize', 'id': seed['id'],
                                'reason': res.get('error'), 'message': res.get('message')})
                continue
            synth_raw.extend(res['items'])
        funnel['synthesize'] = {'seeds': len(valid_seeds), 'errors': synth_errors,
                               'problems': len(synth_raw)}

        # 6/7. validate_synth + filter_synth (M4: different model, K=3 by default)
        synth_check = parallel_map(
            'validate_synth', synth_raw,
            make_validator(llm, cfg, 'validate_synth', 'synth_question', 'synth_answer',
                           cfg.model_validator_synth, cfg.validator_k_synth),
            cfg.concurrency_validate, stores['validate_synth'], keyer('validate_synth'), stop)
        valid_synth, synth_metrics = collect_validation('validate_synth', synth_raw, synth_check,
                                                        carry_seed)
        funnel['validate_synth'] = synth_metrics

        # 7b. optional top-up round (L2, default off, needs user confirmation)
        if cfg.enable_synth_topup:
            survived: Dict[str, int] = {}
            for row in valid_synth:
                survived[row['seed_id']] = survived.get(row['seed_id'], 0) + 1
            short = [s for s in valid_seeds if survived.get(s['id'], 0) < cfg.n_per_seed]
            log('synth_topup: %d seeds below target' % len(short))
            if short:
                topup = parallel_map(
                    'synthesize_topup', short,
                    make_synthesizer(llm, cfg, 'synthesize_topup', 'r2'), cfg.concurrency_synth,
                    stores['synthesize_topup'], keyer('synthesize_topup'), stop)
                topup_raw: List[Dict[str, Any]] = []
                for seed, res in zip(short, topup):
                    if res.get('ok'):
                        topup_raw.extend(res['items'])
                topup_check = parallel_map(
                    'validate_synth_topup', topup_raw,
                    make_validator(llm, cfg, 'validate_synth_topup', 'synth_question',
                                   'synth_answer', cfg.model_validator_synth,
                                   cfg.validator_k_synth),
                    cfg.concurrency_validate, stores['validate_synth_topup'],
                    keyer('validate_synth_topup'), stop)
                extra, extra_metrics = collect_validation('validate_synth_topup', topup_raw,
                                                          topup_check, carry_seed)
                need = {s['id']: cfg.n_per_seed - survived.get(s['id'], 0) for s in short}
                for row in extra:
                    if need.get(row['seed_id'], 0) > 0:
                        valid_synth.append(row)
                        need[row['seed_id']] -= 1
                funnel['synth_topup'] = extra_metrics
        funnel['valid_synth'] = len(valid_synth)

        # 8. merge
        merged_qa = union_datasets(valid_seeds, valid_synth)
        funnel['merged_qa'] = len(merged_qa)

        # 9. reasoning
        trace_results = parallel_map(
            'reasoning', merged_qa, make_reasoner(llm, cfg), cfg.concurrency_reasoning,
            stores['reasoning'], keyer('reasoning'), stop)
        accepted: List[Dict[str, Any]] = []
        undecided: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        reasoning_errors = 0
        for item, res in zip(merged_qa, trace_results):
            if not res.get('ok'):
                reasoning_errors += 1
                rejects.append({'stage': 'reasoning', 'id': item['id'],
                                'reason': res.get('error'), 'message': res.get('message')})
                continue
            row = dict(item)
            row['reasoning_trace'] = res['reasoning_trace']
            row['trace_check'] = res.get('trace_check')
            row['trace_final_answer'] = res.get('trace_final_answer')
            if res.get('trace_ok'):
                accepted.append(row)
            elif res.get('needs_judge'):
                undecided.append((row, res))
            else:
                rejects.append({'stage': 'filter_trace', 'id': item['id'],
                                'reason': 'trace_answer_mismatch',
                                'message': str(res.get('trace_final_answer'))[:120]})
        funnel['reasoning'] = {'rows': len(merged_qa), 'errors': reasoning_errors,
                              'locally_ok': len(accepted), 'undecided': len(undecided)}

        # 9b. judge (decoupled equivalence batch, M6)
        if undecided:
            judge_items = []
            for row, res in undecided:
                pair = '|'.join(sorted([_strip_answer_noise(res['trace_final_answer']),
                                        _strip_answer_noise(row['answer'])]))
                judge_items.append({'id': _hash(pair, 24), 'a': res['trace_final_answer'],
                                    'b': row['answer']})
            judge_results = parallel_map(
                'judge', judge_items, make_judge(llm, cfg), cfg.concurrency_judge,
                stores['judge'], keyer('judge'), stop)
            for (row, res), verdict in zip(undecided, judge_results):
                if verdict.get('ok') and verdict.get('equivalent'):
                    row['trace_check'] = 'llm_judge'
                    accepted.append(row)
                else:
                    rejects.append({'stage': 'filter_trace', 'id': row['id'],
                                    'reason': 'trace_answer_mismatch_judged',
                                    'message': str(res.get('trace_final_answer'))[:120]})
        qa_with_trace = accepted
        funnel['after_reasoning_filter'] = len(qa_with_trace)

        # 10b. conflict check BEFORE dedup (M3)
        conflicts.extend(answer_conflict_check(qa_with_trace, cfg))

        # 11. dedup
        unique_qa, dedup_rejects, dedup_conflicts = ngram_deduplicate(qa_with_trace, cfg)
        rejects.extend(dedup_rejects)
        conflicts.extend(dedup_conflicts)
        funnel['final_after_dedup'] = len(unique_qa)
        funnel['dedup_drop_rate'] = (len(dedup_rejects) / float(len(qa_with_trace))
                                     if qa_with_trace else 0.0)

        # 12. report
        report = export_and_report(cfg, unique_qa, rejects, funnel, tracker, conflicts,
                                  write_dataset=True, status='ok')
        log('done: %d rows, cost $%.2f' % (len(unique_qa), tracker.total))
        return report
    except (BudgetExceeded, QualityGateFailed) as exc:
        funnel['halted'] = str(exc)
        report = export_and_report(cfg, [], rejects, funnel, tracker, conflicts,
                                  write_dataset=False, status='halted')
        log('HALTED: %s' % exc)
        return report
    except Exception as exc:  # M2: always leave a report behind, then propagate
        funnel['failed'] = '%s: %s' % (type(exc).__name__, exc)
        try:
            export_and_report(cfg, [], rejects, funnel, tracker, conflicts,
                              write_dataset=False, status='failed')
        except Exception as report_exc:
            log('report write failed: %s' % report_exc)
        log('FAILED: %s' % funnel['failed'])
        raise
    finally:
        for store in stores.values():
            store.close()


# ---------------------------------------------------------------- self test
class StubLLM:
    '''Offline stand-in for LLM (no API key, no network). Handlers are keyed by
    stage and receive (prompt, nth_call_for_this_stage).'''

    def __init__(self, handlers: Dict[str, Callable[[str, int], LLMResult]]) -> None:
        self.handlers = handlers
        self.calls: List[Dict[str, Any]] = []

    def call(self, stage: str, model: str, prompt: str, max_tokens: int,
             temperature: float = 0.0, attempts: int = 3) -> LLMResult:
        n = 1 + sum(1 for c in self.calls if c['stage'] == stage)
        self.calls.append({'stage': stage, 'model': model, 'max_tokens': max_tokens, 'n': n})
        handler = self.handlers.get(stage)
        if handler is None:
            raise AssertionError('no stub handler for stage %r' % stage)
        return handler(prompt, n)


def run_self_test() -> Dict[str, Any]:
    import tempfile
    results: List[Dict[str, Any]] = []

    def check(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            results.append({'case': name, 'status': 'pass'})
        except Exception as exc:
            results.append({'case': name, 'status': 'fail',
                            'error': '%s: %s' % (type(exc).__name__, exc)})

    workdir = Path(tempfile.mkdtemp(prefix='mathqa_selftest_'))
    cfg = Config(input_path=str(workdir / 'in.jsonl'), output_dir=str(workdir / 'out'),
                 sample_size=0)

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            raise AssertionError(msg)

    # 1. ingest
    def case_ingest() -> None:
        rows = [
            json.dumps({'id': 'a', 'question': 'What is 2+2?', 'answer': '4'}),
            json.dumps({'id': 'b', 'question': 'Compute 3*5.', 'answer': '15'}),
            json.dumps({'id': 'c', 'question': 'What is 2+2?', 'answer': '4'}),
            json.dumps({'id': 'd', 'question': 'no answer here', 'answer': '   '}),
            '{not json',
        ]
        Path(cfg.input_path).write_text('\n'.join(rows), encoding='utf-8')
        kept, rej = load_and_check_schema(cfg)
        expect(len(kept) == 2, 'expected 2 kept, got %d' % len(kept))
        reasons = sorted(r['reason'] for r in rej)
        expect(any('exact_duplicate_question' == r for r in reasons), 'dup not caught')
        expect(any(r.startswith('missing_or_empty') for r in reasons), 'empty answer not caught')
        expect('bad_json' in reasons, 'bad json not caught')

    # 2-4. validator verdicts
    def validator_stub(payload: Dict[str, Any], fenced: bool = False
                       ) -> Callable[[str, int], LLMResult]:
        body = json.dumps(payload)
        text = ('```json\n' + body + '\n```') if fenced else body
        return lambda prompt, n: LLMResult(text, 'end_turn')

    item = {'id': 'x1', 'question_norm': 'What is 2+2?', 'answer_norm': '4'}

    def run_validator(stub: StubLLM, k: int = 1) -> Dict[str, Any]:
        fn = make_validator(stub, cfg, 'validate_seed', 'question_norm', 'answer_norm',
                            cfg.model_validator_seed, k)
        return fn(dict(item))

    def case_validator_good() -> None:
        stub = StubLLM({'validate_seed': validator_stub(
            {'is_valid_question': True, 'is_answer_correct': True,
             'validation_reason': 'fine'}, fenced=True)})
        out = run_validator(stub)
        expect(out['is_valid_question'] and out['is_answer_correct'], 'good item rejected')

    def case_validator_non_math() -> None:
        stub = StubLLM({'validate_seed': validator_stub(
            {'is_valid_question': False, 'is_answer_correct': False,
             'validation_reason': 'not a math problem'})})
        out = run_validator(stub)
        expect(not out['is_valid_question'], 'non-math item accepted')

    def case_validator_wrong_answer() -> None:
        stub = StubLLM({'validate_seed': validator_stub(
            {'is_valid_question': True, 'is_answer_correct': False,
             'validation_reason': 'answer is 4 not 5'})})
        out = run_validator(stub)
        expect(out['is_valid_question'] and not out['is_answer_correct'],
               'wrong answer not detected')

    # 5. truncation is retried with a bigger budget, not scored as invalid
    def case_validator_truncation() -> None:
        good = json.dumps({'is_valid_question': True, 'is_answer_correct': True,
                           'validation_reason': 'ok'})

        def handler(prompt: str, n: int) -> LLMResult:
            if n == 1:
                return LLMResult('{"is_valid_question": tru', 'max_tokens')
            return LLMResult(good, 'end_turn')

        stub = StubLLM({'validate_seed': handler})
        out = run_validator(stub)
        expect(out['is_valid_question'], 'truncated first attempt poisoned the verdict')
        expect(stub.calls[1]['max_tokens'] > stub.calls[0]['max_tokens'],
               'retry did not raise max_tokens')
        expect(any(p.startswith('truncated') for p in out['parse_issues']),
               'truncation not recorded')

    # 6. synthesize: 2 parse errors then success (retry_on_parse_error=2)
    def case_synth_parse_retry() -> None:
        good = json.dumps({'problems': [{'question': 'Q1?', 'answer': '1'},
                                        {'question': 'Q2?', 'answer': '2'}]})

        def handler(prompt: str, n: int) -> LLMResult:
            if n <= 2:
                return LLMResult('sorry, here are some problems (no json)', 'end_turn')
            return LLMResult(good, 'end_turn')

        stub = StubLLM({'synthesize': handler})
        out = make_synthesizer(stub, cfg)({'id': 's1', 'question_norm': 'seed?',
                                           'answer_norm': '7'})
        expect(len(stub.calls) == 3, 'expected 3 attempts, got %d' % len(stub.calls))
        ids = [i['id'] for i in out['items']]
        expect(ids == ['s1#s1', 's1#s2'], 'unexpected synthetic ids: %r' % ids)
        expect(len([p for p in out['parse_issues'] if p.startswith('parse_error')]) == 2,
               'parse errors not recorded')

    # 7. multi-line CoT through tags, latex answer equivalence
    def case_reasoning_tags() -> None:
        trace = 'Step 1: halve it.\nStep 2: 1/2 = 0.5.\n\nStep 3: done.'
        text = 'Here you go\n<trace>\n' + trace + '\n</trace>\n<final>0.5</final>'
        stub = StubLLM({'reasoning': lambda prompt, n: LLMResult(text, 'end_turn')})
        row = {'id': 'r1', 'question': 'Half of one?', 'answer': r'\frac{1}{2}'}
        out = make_reasoner(stub, cfg)(row)
        expect(out['trace_ok'], 'latex vs decimal trace wrongly rejected')
        expect('Step 2' in out['reasoning_trace'] and '\n' in out['reasoning_trace'],
               'multi-line trace lost')
        expect(len(stub.calls) == 1, 'unexpected retries')

    def case_percent() -> None:
        verdict, method = math_equal_local('50%', '0.5', cfg)
        expect(verdict is True, 'percent vs decimal judged unequal (%s)' % method)

    def case_latex_fraction() -> None:
        verdict, _ = math_equal_local(r'x = \frac{3}{4}', '0.75', cfg)
        expect(verdict is True, 'latex fraction vs decimal judged unequal')

    # 10. dedup keeps the seed row and flags the answer conflict
    def case_dedup() -> None:
        base = ('A train travels 60 km in 1 hour and then 90 km in 2 hours. '
                'What is the average speed over the whole trip?')
        near = base.replace('average speed', 'average velocity')
        items = [
            {'id': 'n1', 'seed_id': 'n1', 'question': base, 'answer': '50 km/h',
             'provenance': 'synthetic', 'reasoning_trace': 't'},
            {'id': 's9', 'seed_id': 's9', 'question': near, 'answer': '60 km/h',
             'provenance': 'seed', 'reasoning_trace': 't'},
        ]
        kept, dropped, conf = ngram_deduplicate(items, cfg)
        expect(len(kept) == 1 and kept[0]['id'] == 's9',
               'seed row not preferred: %r' % [k['id'] for k in kept])
        expect(len(dropped) == 1, 'near duplicate not dropped')
        expect(len(conf) == 1, 'answer conflict inside near-dup cluster not flagged')

    def case_gate_metrics() -> None:
        m = gate_metrics(10, 5, 5)
        expect(abs(m['pass_rate'] - 1.0) < 1e-9,
               'pass_rate must exclude infra errors, got %.3f' % m['pass_rate'])
        expect(abs(m['error_rate'] - 0.5) < 1e-9, 'error_rate wrong')

    def case_export_atomic() -> None:
        tracker = CostTracker(10.0)
        row = {'id': 'e1', 'seed_id': 'e1', 'question': 'q', 'answer': 'a',
               'reasoning_trace': 'Step 1: ...', 'provenance': 'seed'}
        export_and_report(cfg, [row], [], {'stage': 'test'}, tracker, [], True, 'ok')
        data_path = Path(cfg.output_dir) / 'final_dataset.jsonl'
        expect(data_path.exists() and len(data_path.read_text(encoding='utf-8')
                                          .strip().splitlines()) == 1, 'export wrote no row')
        export_and_report(cfg, [], [], {'stage': 'halt'}, tracker, [], False, 'halted')
        expect(len(data_path.read_text(encoding='utf-8').strip().splitlines()) == 1,
               'halt path truncated the previous dataset')
        bad = dict(row)
        bad['reasoning_trace'] = ''
        try:
            export_and_report(cfg, [bad], [], {}, tracker, [], True, 'ok')
            raise AssertionError('empty reasoning_trace was exported')
        except ExportContractError:
            pass

    def case_pricing() -> None:
        expect(price_of('claude-sonnet-4-5-20250929') == (3.0, 15.0), 'prefix match broken')
        try:
            price_of('mystery-model-x')
            raise AssertionError('unpriced model did not fail closed')
        except UnpricedModel:
            pass

    check('ingest_schema_and_exact_dedup', case_ingest)
    check('validator_good_item_fenced_json', case_validator_good)
    check('validator_non_math_item', case_validator_non_math)
    check('validator_wrong_answer', case_validator_wrong_answer)
    check('validator_truncation_is_retried_not_rejected', case_validator_truncation)
    check('synthesize_two_parse_errors_then_success', case_synth_parse_retry)
    check('reasoning_multiline_cot_via_tags', case_reasoning_tags)
    check('math_equal_percent_vs_decimal', case_percent)
    check('math_equal_latex_fraction_vs_decimal', case_latex_fraction)
    check('dedup_prefers_seed_and_flags_answer_conflict', case_dedup)
    check('gate_metrics_exclude_infra_errors', case_gate_metrics)
    check('export_atomic_and_halt_does_not_truncate', case_export_atomic)
    check('price_lookup_prefix_match_and_fail_closed', case_pricing)

    failed = [r for r in results if r['status'] != 'pass']
    return {'cases': results, 'passed': len(results) - len(failed), 'failed': len(failed),
            'workdir': str(workdir)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Math QA clean/synthesize pipeline')
    parser.add_argument('--selftest', action='store_true',
                        help='run offline test cases (no API key, no network)')
    args = parser.parse_args(argv)
    if args.selftest:
        outcome = run_self_test()
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0 if outcome['failed'] == 0 else 1
    print(json.dumps(pipeline(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
