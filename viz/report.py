#!/usr/bin/env python3
"""Build the trajectory report for any run, entirely from its evidence file.

Usage: python3 viz/report.py <run_id|evidence/path.json> [out.html]
"""
import html
import json
import pathlib
import re
import sys

E = html.escape

AGENT_ROLE = {
    "ResearchPlanner": ("数据工程架构师", "调研 · 算子拆解"),
    "FieldMapper": ("Schema 合约专家", "字段对齐"),
    "PipelineBuilder": ("DataFlow 开发者", "算子实现"),
    "Validator": ("质量工程师", "质量门禁"),
    "Reviewer": ("安全合规审查员", "风险审批"),
}
ORDER = list(AGENT_ROLE)
INTERNAL = ("raw_response", "attempts", "parse_note", "agent")

KEYWORDS = {
    "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del",
    "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in",
    "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
    "with", "yield",
}
CONSTS = {"True", "False", "None", "self", "cls"}
TOKEN_RE = re.compile(
    r"""(?P<str>[rbfu]{0,2}(?:'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"))
       |(?P<com>\#[^\n]*)
       |(?P<dec>@[A-Za-z_][\w.]*)
       |(?P<num>\b\d[\w.]*\b)
       |(?P<word>\b[A-Za-z_]\w*\b)""",
    re.VERBOSE | re.DOTALL,
)


def highlight(src):
    parts, pos = [], 0
    for m in TOKEN_RE.finditer(src):
        parts.append(E(src[pos:m.start()]))
        kind, text = m.lastgroup, E(m.group())
        if kind == "word":
            word, head = m.group(), src[:m.start()].rstrip()
            if word in KEYWORDS:
                parts.append(f'<span class="k">{text}</span>')
            elif word in CONSTS:
                parts.append(f'<span class="c">{text}</span>')
            elif head.endswith(("def", "class")):
                parts.append(f'<span class="fn">{text}</span>')
            else:
                parts.append(text)
        else:
            cls = {"str": "s", "com": "cm", "dec": "d", "num": "n"}[kind]
            parts.append(f'<span class="{cls}">{text}</span>')
        pos = m.end()
    parts.append(E(src[pos:]))
    return "".join(parts)


def secs(a, b):
    """Seconds between two ISO timestamps."""
    from datetime import datetime
    def p(t):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(t, fmt)
            except ValueError:
                continue
        raise ValueError(t)
    return (p(b) - p(a)).total_seconds()


def agent_spans(events):
    """Derive per-agent wall-clock spans from status_changed events.

    An agent that ran more than once (repair loop) yields one span per run.
    """
    spans, open_at = [], {}
    for e in events:
        if e["action"] != "status_changed":
            continue
        actor, status = e["actor"], e["payload"].get("status")
        if status == "running":
            open_at[actor] = e["ts"]
        elif status in ("completed", "failed") and actor in open_at:
            start = open_at.pop(actor)
            spans.append({
                "agent": actor, "start": start, "end": e["ts"],
                "dur": secs(start, e["ts"]), "outcome": status,
            })
    return spans


def attempt_summary(out):
    """One line per attempt an agent needed, from the recorded attempts list."""
    rows = []
    for a in out.get("attempts", []):
        rows.append({
            "n": a.get("attempt"),
            "outcome": a.get("outcome", ""),
            "reason": a.get("reason", ""),
            "stop": a.get("stop_reason", ""),
            "tokens": a.get("output_tokens"),
            "chars": a.get("chars"),
        })
    return rows


def verdict_of(name, out):
    """(css class, short label) for an agent's result — read, never assumed."""
    if not out:
        return "skip", "未执行"
    if "parse_error" in out:
        return "fail", "解析失败"
    attempts = out.get("attempts", [])
    retried = len(attempts) > 1
    if name == "Validator":
        v = out.get("verdict", "?")
        score = out.get("quality_score")
        label = f"{v}" + (f" · {score}" if score is not None else "")
        cls = {"pass": "pass", "pass_with_warnings": "warn", "fail": "fail"}.get(v, "skip")
        return cls, label
    if name == "Reviewer":
        v = out.get("verdict", "?")
        cls = {"approved": "pass", "needs_approval": "warn", "rejected": "fail"}.get(v, "skip")
        return cls, v
    if name == "FieldMapper":
        c = out.get("confidence")
        n = len(out.get("conflicts") or [])
        return ("pass" if n == 0 else "warn"), f"confidence {c} · {n} 冲突"
    if name == "PipelineBuilder":
        code = out.get("pipeline_code", "")
        lines = code.count("\n") + 1 if code else 0
        return "pass", f"{lines} 行代码" + (f" · {len(attempts)} 次尝试" if retried else "")
    if name == "ResearchPlanner":
        nodes = len((out.get("plan", {}).get("dag", {}) or {}).get("nodes", []))
        return "pass", f"{nodes} 算子" + (f" · {len(attempts)} 次尝试" if retried else "")
    return "pass", "完成"


# ---------------------------------------------------------------- fragments

def code_block(text):
    return f'<pre class="code"><code>{E(text)}</code></pre>'


def js(obj):
    return code_block(json.dumps(obj, ensure_ascii=False, indent=2))


def kv(pairs):
    items = "".join(f'<div class="kv"><dt>{E(k)}</dt><dd>{v}</dd></div>' for k, v in pairs)
    return f'<dl class="kvlist">{items}</dl>'


def details(summary, inner, open_=False):
    return f'<details{" open" if open_ else ""}><summary>{E(summary)}</summary>{inner}</details>'


def subh(text):
    return f'<h4 class="subh">{E(text)}</h4>'


def notes(items):
    lis = "".join(f"<li>{E(str(x))}</li>" for x in items)
    return f'<ul class="notes">{lis}</ul>'


def table(headers, rows):
    th = "".join(f"<th>{E(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (
        f'<div class="scroll"><table class="tbl"><thead><tr>{th}</tr></thead>'
        f"<tbody>{tr}</tbody></table></div>"
    )


def pill(text, cls=None):
    cls = cls or str(text).lower().replace("_", "-")
    return f'<span class="sev {E(cls)}">{E(str(text))}</span>'


# ------------------------------------------------------------------ sections

def render_timeline(spans):
    """Gantt of agent runs, one bar per execution, on a shared time scale."""
    if not spans:
        return ""
    t0, t1 = spans[0]["start"], spans[-1]["end"]
    total = max(secs(t0, t1), 1)
    rows = []
    for s in spans:
        off = secs(t0, s["start"]) / total * 100
        wid = max(s["dur"] / total * 100, 1.2)
        cls = "pass" if s["outcome"] == "completed" else "fail"
        rows.append(
            f'<li class="tlrow"><span class="tlname">{E(s["agent"])}</span>'
            f'<span class="tltrack"><span class="tlbar {cls}" '
            f'style="left:{off:.2f}%;width:{wid:.2f}%">'
            f'<span class="tldur">{s["dur"]:.0f}s</span></span></span>'
            f'<span class="tlclock">{E(s["start"][11:19])}</span></li>'
        )
    ticks = "".join(
        f'<span class="tick" style="left:{p}%">{"+" + str(int(total * p / 100)) + "s" if p else t0[11:19]}</span>'
        for p in (0, 25, 50, 75, 100)
    )
    return (
        f'<div class="timeline"><ol class="tllist">{"".join(rows)}</ol>'
        f'<div class="tlaxis">{ticks}</div>'
        f'<p class="tlfoot">总耗时 {total:.0f}s · {len(spans)} 次 Agent 执行</p></div>'
    )


def render_dag(plan):
    """Operator DAG as read from ResearchPlanner's plan."""
    nodes = (plan.get("dag") or {}).get("nodes") or []
    if not nodes:
        return '<p class="lede">plan 中未包含 dag.nodes。</p>'
    cells = []
    for i, n in enumerate(nodes, 1):
        par = n.get("parallelizable")
        tag = ('<span class="chip par">可并行</span>' if par
               else '<span class="chip seq">顺序</span>')
        ins = ", ".join(n.get("inputs") or []) or "—"
        outs = ", ".join(n.get("outputs") or []) or "—"
        params = n.get("params") or {}
        pstr = ("".join(
            f'<div><dt>{E(k)}</dt><dd><code>{E(str(v))}</code></dd></div>'
            for k, v in params.items()
        ) if params else "")
        cells.append(
            f'<li class="dagnode"><div class="dagtop">'
            f'<span class="step">{i}</span><code>{E(str(n.get("id","")))}</code>{tag}</div>'
            f'<div class="dagop">{E(str(n.get("operator","")))}</div>'
            f'<p class="dagdesc">{E(str(n.get("description","")))}</p>'
            f'<dl class="io"><div><dt>in</dt><dd><code>{E(ins)}</code></dd></div>'
            f'<div><dt>out</dt><dd><code>{E(outs)}</code></dd></div>{pstr}</dl></li>'
        )
    return f'<ol class="dag">{"".join(cells)}</ol>'


def render_attempts(rows):
    """Per-attempt table — the retry evidence the fix was made for."""
    if len(rows) <= 1:
        return ""
    trs = []
    for r in rows:
        trs.append([
            f'<code>#{r["n"]}</code>',
            pill(r["outcome"], "pass" if r["outcome"] == "usable" else "fail"),
            E(r["reason"]),
            f'<code>{E(str(r["stop"]))}</code>',
            f'{r["tokens"] or "—"}',
            f'{r["chars"]:,}' if r["chars"] is not None else "—",
        ])
    return (
        subh("重试记录")
        + table(["尝试", "结果", "原因", "stop_reason", "out_tokens", "字符"], trs)
    )


def render_risks(risks):
    if not risks:
        return '<p class="lede">未报告风险。</p>'
    rows = [[
        f'<code>{E(str(r.get("type","")))}</code>',
        pill(r.get("severity", "info")),
        E(str(r.get("description", ""))),
        E(str(r.get("mitigation", ""))),
    ] for r in risks]
    return table(["类型", "等级", "描述", "缓解"], rows)


def render_checks(checks):
    if not checks:
        return '<p class="lede">未报告检查项。</p>'
    rows = [[
        f'<code>{E(str(c.get("name","")))}</code>',
        pill(c.get("status", "")),
        E(str(c.get("message", ""))),
    ] for c in checks]
    return table(["检查项", "结果", "说明"], rows)


def render_hints(hints):
    if not hints:
        return ""
    lis = []
    for h in hints:
        pr = h.get("priority", "")
        lis.append(
            f'<li class="hint">{pill(pr) if pr else ""}<div>'
            f'<code>{E(str(h.get("check","")))}</code>'
            f'<p>{E(str(h.get("issue","")))}</p>'
            f'<p class="fix">→ {E(str(h.get("suggestion","")))}</p></div></li>'
        )
    return subh("回传 PipelineBuilder 的修复建议") + f'<ul class="hints">{"".join(lis)}</ul>'


def render_agent(name, out, spans, raw_dir):
    """One agent card, built only from what its output actually contains."""
    ident, role = AGENT_ROLE[name]
    cls, label = verdict_of(name, out)
    mine = [s for s in spans if s["agent"] == name]
    dur = sum(s["dur"] for s in mine)
    runs = f" · {len(mine)} 次执行" if len(mine) > 1 else ""

    body = []
    if not out:
        body.append('<p class="lede">该 Agent 未执行（上游中断）。</p>')
    else:
        body.append(render_attempts(attempt_summary(out)))

        if name == "ResearchPlanner":
            plan = out.get("plan") or {}
            body.append(render_dag(plan))
            meta = [(k, E(str(v))) for k, v in plan.items()
                    if k != "dag" and not isinstance(v, (dict, list))]
            if meta:
                body.append(subh("规模与成本") + kv(meta))
            for key, title in [("assumptions", "前置假设"), ("risks", "识别的风险")]:
                val = out.get(key)
                if val:
                    body.append(subh(title))
                    body.append(render_risks(val) if key == "risks" else notes(val))

        elif name == "FieldMapper":
            body.append(kv([
                ("confidence", f'<strong>{E(str(out.get("confidence","—")))}</strong>'),
                ("conflicts", str(len(out.get("conflicts") or []))),
                ("warnings", str(len(out.get("warnings") or []))),
            ]))
            if out.get("conflicts"):
                body.append(subh("冲突") + render_risks(out["conflicts"]))
            if out.get("warnings"):
                body.append(subh("告警") + render_risks([
                    {"type": w.get("type", ""), "severity": w.get("severity", "info"),
                     "description": w.get("message", ""), "mitigation": w.get("resolution", "")}
                    for w in out["warnings"]
                ]))
            if out.get("mapping"):
                body.append(details("完整 mapping", js(out["mapping"])))

        elif name == "PipelineBuilder":
            code = out.get("pipeline_code", "")
            body.append(kv([
                ("代码规模", f'{code.count(chr(10)) + 1} 行 · {len(code):,} 字符'),
                ("language", E(str(out.get("language", "—")))),
                ("idempotency_key", f'<code>{E(str(out.get("idempotency_key","—")))}</code>'),
            ]))
            for d in (out.get("diff") or {}).get("added_operators", []) or []:
                pass
            ops = (out.get("diff") or {}).get("added_operators")
            if ops and isinstance(ops, list):
                items = "".join(
                    f'<li class="opitem"><code>{E(str(o.get("name", o) if isinstance(o, dict) else o))}</code>'
                    + (f'<span class="oploc">{E(str(o.get("location","")))}</span>' if isinstance(o, dict) else "")
                    + (f'<p>{E(str(o.get("impact","")))}</p>' if isinstance(o, dict) else "")
                    + "</li>"
                    for o in ops
                )
                body.append(subh("实现的算子") + f'<ul class="oplist">{items}</ul>')
            deps = out.get("dependencies") or []
            if deps:
                body.append(subh("依赖") + kv([
                    (d.get("name", "?"), f'<code>{E(str(d.get("version","")))}</code> — {E(str(d.get("purpose","")))}')
                    if isinstance(d, dict) else (str(d), "")
                    for d in deps
                ]))
            for key, title in [("notes_for_validator", "向 Validator 声明的偏差")]:
                if out.get(key):
                    body.append(subh(title) + notes(out[key]))

        elif name == "Validator":
            body.append(kv([
                ("verdict", pill(out.get("verdict", "—"))),
                ("quality_score", f'<strong>{E(str(out.get("quality_score","—")))}</strong>'),
            ]))
            body.append(render_checks(out.get("checks") or []))
            body.append(render_hints(out.get("repair_hints") or []))
            if out.get("blockers"):
                body.append(subh("阻塞项") + js(out["blockers"]))

        elif name == "Reviewer":
            body.append(kv([
                ("verdict", pill(out.get("verdict", "—"))),
                ("approval_required", E(str(out.get("approval_required", "—")))),
                ("decision_reason", E(str(out.get("decision_reason", "—")))),
            ]))
            body.append(subh("风险清单") + render_risks(out.get("risks") or []))
            if out.get("rollback_plan"):
                body.append(subh("回滚预案") + js(out["rollback_plan"]))
            if out.get("audit_log"):
                body.append(details("审计日志", js(out["audit_log"])))

        raw = out.get("raw_response") or ""
        if raw:
            p = raw_dir / f"raw_{name}.txt"
            p.write_text(raw)
            body.append(details(f"原始响应 · {len(raw):,} 字符", code_block(raw)))

    return (
        f'<article class="agent" id="a-{name.lower()}"><header class="agenthead">'
        f'<div class="agentid"><h3>{E(name)}</h3>'
        f'<p class="agentrole">{E(ident)} · {E(role)}</p></div>'
        f'<div class="agentmeta"><span class="badge {cls}">{E(label)}</span>'
        f'<span class="span">{dur:.0f}s{runs}</span></div></header>'
        f'{"".join(body)}</article>'
    )


def render_strip(state, spans):
    """Top-line facts, each one read off the evidence rather than assumed."""
    out = state["outputs"]
    items = []

    rp = out.get("ResearchPlanner") or {}
    nodes = len(((rp.get("plan") or {}).get("dag") or {}).get("nodes") or [])
    items.append(("算子拆解", f"{nodes} 个节点", "pass" if nodes else "fail"))

    fm = out.get("FieldMapper") or {}
    conf = fm.get("confidence")
    nconf = len(fm.get("conflicts") or [])
    items.append(("字段契约", f"confidence {conf} · {nconf} 冲突" if conf is not None else "未产出",
                  "pass" if conf and nconf == 0 else "fail"))

    pb = out.get("PipelineBuilder") or {}
    code = pb.get("pipeline_code", "")
    lines = code.count("\n") + 1 if code else 0
    items.append(("算子代码", f"{lines} 行" if lines else "未产出", "pass" if lines else "fail"))

    va = out.get("Validator") or {}
    v = va.get("verdict")
    checks = va.get("checks") or []
    npass = sum(1 for c in checks if c.get("status") == "pass")
    items.append(("质量门禁", f"{v} · {npass}/{len(checks)} pass" if v else "未产出",
                  {"pass": "pass", "pass_with_warnings": "warn"}.get(v, "fail")))

    rv = out.get("Reviewer") or {}
    rvv = rv.get("verdict")
    items.append(("审批决策", rvv or "未产出",
                  {"approved": "pass", "needs_approval": "warn"}.get(rvv, "fail")))

    retries = sum(max(len((out.get(n) or {}).get("attempts") or []) - 1, 0) for n in ORDER)
    items.append(("重试次数", f"{retries} 次" if retries else "0 次（一次通过）",
                  "warn" if retries else "pass"))

    cells = "".join(
        f'<li class="vitem {c}"><span class="vk">{E(k)}</span>'
        f'<span class="vv">{E(str(v))}</span></li>'
        for k, v, c in items
    )
    return f'<ul class="vstrip">{cells}</ul>'


def render_code(state, out_dir):
    """The generated pipeline, with line numbers, plus the syntax verdict."""
    pb = state["outputs"].get("PipelineBuilder") or {}
    code = pb.get("pipeline_code", "")
    if not code:
        return '<p class="lede">本次运行未产出 pipeline 代码。</p>'

    path = out_dir / "math_qa_pipeline.py"
    path.write_text(code)

    import ast
    try:
        ast.parse(code)
        verdict = pill("ast.parse OK", "pass")
    except SyntaxError as exc:
        verdict = pill(f"SyntaxError line {exc.lineno}", "fail")

    lines = code.count("\n") + 1
    gutter = "\n".join(str(i) for i in range(1, lines + 1))
    cfg = pb.get("configuration") or {}
    envs = ", ".join(f"<code>{E(v)}</code>" for v in (cfg.get("env_vars") or [])) or "—"
    return (
        kv([
            ("输出文件", f"<code>{E(str(path))}</code>"),
            ("规模", f"{lines} 行 · {len(code):,} 字符"),
            ("语法检查", verdict),
            ("环境变量", envs),
        ])
        + f'<div class="codewrap"><pre class="gutter" aria-hidden="true">{gutter}</pre>'
        f'<pre class="code"><code>{highlight(code)}</code></pre></div>'
    )


def render_events(events):
    rows = []
    for e in events:
        pl = json.dumps(e["payload"], ensure_ascii=False)
        if len(pl) > 130:
            pl = pl[:127] + "…"
        rows.append([
            f'<span class="ts">{E(e["ts"][11:19])}</span>',
            f'<code>{E(e["actor"])}</code>',
            E(e["action"]),
            f'<span class="pl">{E(pl)}</span>',
        ])
    return table(["时间", "actor", "action", "payload"], rows)


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 1

    arg = sys.argv[1]
    ev = pathlib.Path(arg if arg.endswith(".json") else f"evidence/{arg}.json")
    if not ev.exists():
        print(f"evidence not found: {ev}")
        return 1

    state = json.loads(ev.read_text())
    out_dir = pathlib.Path("viz")
    out_dir.mkdir(exist_ok=True)
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else out_dir / "trace_report.html"

    spans = agent_spans(state["events"])
    task = state["task"]
    status = state["status"]
    status_cls = {"completed": "pass", "approval_required": "warn"}.get(status, "fail")
    total = secs(spans[0]["start"], spans[-1]["end"]) if spans else 0

    # The Reviewer's verdict is the authoritative terminal state. An older
    # orchestrator could seal a run as "completed" after a rejection; say so
    # rather than letting the masthead repeat the wrong word.
    rv_verdict = (state["outputs"].get("Reviewer") or {}).get("verdict")
    mismatch = ""
    if rv_verdict == "rejected" and status != "rejected":
        status_cls = "fail"
        mismatch = (
            '<p class="alert"><strong>终态不一致：</strong>'
            f'Reviewer 判定 <code>rejected</code>，但 run 被记为 <code>{E(status)}</code>。'
            "以 Reviewer 的判定为准——本次运行未通过审查。</p>"
        )
    elif rv_verdict == "needs_approval" and status != "approval_required":
        status_cls = "warn"
        mismatch = (
            '<p class="alert"><strong>终态不一致：</strong>'
            f'Reviewer 要求人工审批，但 run 被记为 <code>{E(status)}</code>。</p>'
        )

    head = (
        '<header class="masthead">'
        '<p class="kicker">DataFlow AgentTeams · 执行轨迹</p>'
        f'<h1>{E(task.get("title", "Pipeline Run"))}</h1>'
        f'<p class="dek">{E(task.get("prompt", "")[:170])}…</p>'
        '<dl class="runmeta">'
        f'<div><dt>run_id</dt><dd><code>{E(state["run_id"])}</code></dd></div>'
        f'<div><dt>耗时</dt><dd>{total:.0f}s · {len(spans)} 次执行</dd></div>'
        f'<div><dt>风险等级</dt><dd>{E(str(task.get("risk", "—")))}</dd></div>'
        f'<div><dt>终态</dt><dd>{pill(status, status_cls)}</dd></div>'
        "</dl></header>"
    )

    def sec(title, eyebrow, inner):
        return (
            f'<section class="sec"><div class="sechead">'
            f'<p class="eyebrow">{E(eyebrow)}</p><h2>{E(title)}</h2></div>{inner}</section>'
        )

    cards = "".join(
        render_agent(n, state["outputs"].get(n), spans, out_dir) for n in ORDER
    )

    body = [
        head,
        mismatch,
        render_strip(state, spans),
        sec("Agent 接力时序", f"{len(spans)} 次执行 · {total:.0f} 秒", render_timeline(spans)),
        sec("每个 Agent 的轨迹", "输入 · 判断 · 产出", cards),
        sec("完整 Pipeline 代码", "PipelineBuilder 产物", render_code(state, out_dir)),
        sec("状态机事件流", f'{len(state["events"])} 条 evidence 事件', render_events(state["events"])),
        '<footer class="foot"><p>本页由 <code>viz/report.py</code> 从 '
        f"<code>{E(str(ev))}</code> 生成，全部数值直接读自该文件，无手工填写。</p></footer>",
    ]

    css = (out_dir / "style.css").read_text()
    doc = (
        f"<title>{E(task.get('title', 'Pipeline Run'))}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=IBM+Plex+Sans+Condensed:wght@500;600;700&"
        "family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&"
        'family=IBM+Plex+Mono:wght@400;500;600&display=swap">\n'
        f"<style>\n{css}\n</style>\n"
        f'<main class="page">\n{"".join(body)}\n</main>\n'
    )
    out.write_text(doc)
    print(f"wrote {out} — {len(doc):,} chars")
    print(f"  status={status} spans={len(spans)} total={total:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
