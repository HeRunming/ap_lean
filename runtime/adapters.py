"""Stable adapter seams: replace these stubs with MCP clients or DataFlow SDK calls."""
import json, os, subprocess

def call_codex(agent, context, dry_run=False):
    if dry_run or not os.getenv("CODEX_BIN"):
        return {"agent": agent, "mode": "dry-run", "summary": f"{agent} completed with shared context"}
    prompt = json.dumps({"agent": agent, "context": context}, ensure_ascii=False)
    p = subprocess.run([os.environ["CODEX_BIN"], "exec", "-"], input=prompt, text=True, capture_output=True, check=False)
    return {"agent": agent, "stdout": p.stdout, "stderr": p.stderr, "ok": p.returncode == 0}

def mcp_call(tool, args, idempotency_key):
    # Contract: {ok,data,error,request_id}; production implementation may be any MCP client.
    return {"ok": False, "error": "MCP adapter not configured", "tool": tool, "idempotency_key": idempotency_key}

