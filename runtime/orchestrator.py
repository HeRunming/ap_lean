"""Enhanced orchestrator with Claude API integration."""
import argparse
import json
import pathlib
import re
import time
from typing import Dict, Any, Optional

from .state import RunState
from .message_bus import MessageBus
from .claude_client import ClaudeClient, AgentSession
from .config import Config

# Keys an agent's output must carry to count as usable by the next stage.
REQUIRED_KEYS = {
    "ResearchPlanner": ["plan"],
    "FieldMapper": ["mapping"],
    "PipelineBuilder": ["pipeline_code"],
    "Validator": ["checks", "verdict"],
    "Reviewer": ["verdict"],
}


class AgentOrchestrator:
    """Orchestrates multi-agent execution using Claude API."""

    STAGES = [
        ("ResearchPlanner", "planned"),
        ("FieldMapper", "mapped"),
        ("PipelineBuilder", "drafted"),
        ("Validator", "validated"),
        ("Reviewer", "reviewed"),
    ]

    def __init__(self, config: Config):
        self.config = config
        self.client = ClaudeClient(
            api_key=config.anthropic_api_key,
            base_url=config.anthropic_base_url,
            model=config.claude_model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
        )
        self.message_bus = MessageBus(evidence_dir=config.evidence_dir)
        self.agent_sessions: Dict[str, AgentSession] = {}

    def load_agent_contract(self, agent_name: str) -> str:
        """Load agent system prompt from contract file."""
        contract_path = pathlib.Path("agents/contracts") / f"{self._to_snake_case(agent_name)}.md"
        if contract_path.exists():
            return contract_path.read_text()
        return f"You are the {agent_name} agent. Follow instructions and return structured JSON output."

    def _to_snake_case(self, name: str) -> str:
        """Convert CamelCase to snake_case."""
        import re
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    def get_or_create_session(self, agent_name: str) -> AgentSession:
        """Get or create an agent session."""
        if agent_name not in self.agent_sessions:
            system_prompt = self.load_agent_contract(agent_name)
            self.agent_sessions[agent_name] = AgentSession(
                agent_name=agent_name,
                system_prompt=system_prompt,
                client=self.client,
            )
        return self.agent_sessions[agent_name]

    def execute_agent(
        self,
        agent_name: str,
        context: Dict[str, Any],
        state: RunState,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Execute a single agent, retrying until its output is usable.

        A blank body, unparseable JSON, or a missing required key is a failed
        attempt, not a result: the agent is re-asked with feedback on what went
        wrong. Raises RuntimeError when every attempt fails.
        """
        if dry_run:
            return {
                "agent": agent_name,
                "mode": "dry-run",
                "summary": f"{agent_name} completed with shared context",
            }

        state.set_agent_status(agent_name, "running")
        session = self.get_or_create_session(agent_name)
        agent_cfg = self.config.get_agent_config(agent_name)
        max_tokens = agent_cfg.get("max_tokens", self.config.max_tokens)
        temperature = agent_cfg.get("temperature")

        prompt = self._build_agent_prompt(agent_name, context)
        attempts: list[dict] = []

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = session.send(
                    prompt, max_tokens=max_tokens, temperature=temperature
                )
            except Exception as exc:  # transport error — retry
                reason = f"{type(exc).__name__}: {exc}"
                attempts.append({"attempt": attempt, "outcome": "request_failed", "reason": reason})
                state.event(agent_name, "attempt_failed", attempts[-1])
                print(f"  ⟳ attempt {attempt}: request failed — {reason}", flush=True)
                prompt = self._retry_prompt(agent_name, context, reason)
                time.sleep(self.config.retry_delay)
                continue

            usable, result, reason = self._evaluate_response(agent_name, response)
            attempts.append({
                "attempt": attempt,
                "outcome": "usable" if usable else "unusable",
                "reason": reason,
                "stop_reason": response.stop_reason,
                "output_tokens": response.usage.get("output_tokens"),
                "chars": len(response.content),
            })
            state.event(agent_name, "attempt_completed", attempts[-1])

            if usable:
                result["attempts"] = attempts
                state.set_agent_status(agent_name, "completed")
                state.event(agent_name, "execution_completed", {
                    "output_keys": [k for k in result if k != "raw_response"],
                    "attempts_used": attempt,
                })
                return result

            print(f"  ⟳ attempt {attempt}: {reason}", flush=True)
            if response.stop_reason == "max_tokens":
                max_tokens = min(max_tokens * 2, 32000)
                print(f"    → raising max_tokens to {max_tokens}", flush=True)
            prompt = self._retry_prompt(agent_name, context, reason)
            time.sleep(self.config.retry_delay)

        detail = attempts[-1]["reason"] if attempts else "no attempts recorded"
        state.record_error(agent_name, f"unusable output after {len(attempts)} attempts", {
            "attempts": attempts,
        })
        state.set_agent_status(agent_name, "failed")
        raise RuntimeError(f"{agent_name}: {detail}")

    def _evaluate_response(self, agent_name: str, response) -> tuple[bool, Dict[str, Any], str]:
        """Decide whether a response is usable; return (ok, parsed, reason)."""
        text = response.content
        if not text.strip():
            return False, {}, f"empty body (stop_reason={response.stop_reason})"

        result = self._parse_agent_response(agent_name, text)
        if "parse_error" in result:
            return False, result, f"{result['parse_error']} (stop_reason={response.stop_reason})"

        missing = [k for k in REQUIRED_KEYS.get(agent_name, []) if k not in result]
        if missing:
            return False, result, f"output missing required key(s): {', '.join(missing)}"

        return True, result, "ok"

    def _retry_prompt(self, agent_name: str, context: Dict[str, Any], reason: str) -> str:
        """Re-ask the same turn, naming what was wrong with the last one."""
        return (
            f"Your previous response was unusable: {reason}\n\n"
            "Return ONE ```json code block and nothing else — no prose before or "
            "after it. Keep it complete and closed; if the content is long, cut "
            "explanatory text rather than truncating the JSON.\n\n"
            + self._build_agent_prompt(agent_name, context)
        )

    # The one output shape each agent is asked for — no cross-agent menu.
    OUTPUT_SHAPES = {
        "ResearchPlanner":
            '{"plan": {"dag": {"nodes": [...], "edges": [...]}, "estimated_duration": "...", '
            '"estimated_cost": "..."}, "assumptions": [...], "risks": [...], "evidence_ids": [...]}',
        "FieldMapper":
            '{"mapping": {...}, "confidence": 0.0-1.0, "conflicts": [...], "warnings": [...], '
            '"evidence_ids": [...]}',
        "PipelineBuilder":
            '{"pipeline_code": "<complete runnable source, \\n-escaped>", "language": "python", '
            '"diff": {...}, "dependencies": [...], "configuration": {...}, '
            '"idempotency_key": "...", "evidence_ids": [...]}',
        "Validator":
            '{"checks": [{"name": "...", "status": "pass|warning|fail|skipped", "message": "..."}], '
            '"verdict": "pass|pass_with_warnings|fail", "quality_score": 0.0-1.0, '
            '"repair_hints": [...], "evidence_ids": [...]}',
        "Reviewer":
            '{"verdict": "approved|needs_approval|rejected", "decision_reason": "...", '
            '"risks": [...], "approval_required": true|false, "rollback_plan": {...}, '
            '"audit_log": {...}}',
    }

    def _build_agent_prompt(self, agent_name: str, context: Dict[str, Any]) -> str:
        """Build prompt for an agent."""
        ctx = json.dumps(context, ensure_ascii=False, indent=2)
        limit = self.config.max_context_chars
        if len(ctx) > limit:
            ctx = ctx[:limit] + f"\n… [context truncated at {limit} chars]"

        shape = self.OUTPUT_SHAPES.get(agent_name, "{...}")
        return f"""## Context

{ctx}

## Task

Based on the above context, perform your designated role as {agent_name},
following your contract.

## Output Format

Return exactly ONE ```json code block and no prose outside it. Required shape:

{shape}

The JSON must be complete and closed. If you are running long, shorten
descriptive text — never truncate the structure. Inside string values, escape
newlines as \\n so the block stays parseable.
"""

    def _parse_agent_response(self, agent_name: str, response: str) -> Dict[str, Any]:
        """Parse agent response, extracting JSON.

        Prefers the last fenced ```json block (a retry may quote the failed one
        first). Falls back to strict=False, which tolerates the raw newlines a
        model emits inside long code strings.
        """
        blocks = re.findall(r"```json\s*(.*?)\s*```", response, re.DOTALL)
        candidates = [blocks[-1]] if blocks else []
        candidates.append(response.strip())

        for candidate in candidates:
            for strict in (True, False):
                try:
                    result = json.loads(candidate, strict=strict)
                except json.JSONDecodeError:
                    continue
                if not isinstance(result, dict):
                    continue
                result["agent"] = agent_name
                result["raw_response"] = response
                if not strict:
                    result["parse_note"] = "parsed with strict=False (raw control chars in strings)"
                return result

        return {
            "agent": agent_name,
            "raw_response": response,
            "parse_error": "Could not extract JSON from response",
        }

    def _repair_loop(self, state: RunState, verdict_result: Dict[str, Any]) -> Dict[str, Any]:
        """On a Validator fail, send repair_hints back to PipelineBuilder and re-validate.

        Returns the last Validator result — the repaired one if a round improved
        it, otherwise the original failing one. Never silently claims success.
        """
        rounds = self.config.max_repair_rounds
        for round_no in range(1, rounds + 1):
            if verdict_result.get("verdict") != "fail":
                return verdict_result

            hints = verdict_result.get("repair_hints", [])
            print(f"\n  ↻ repair round {round_no}: Validator fail → PipelineBuilder ({len(hints)} hints)", flush=True)
            state.event("Orchestrator", "repair_requested", {
                "round": round_no,
                "hint_count": len(hints),
            })

            try:
                context = state.get_context_for_agent("PipelineBuilder")
                context["repair_hints"] = hints
                context["validator_verdict"] = verdict_result.get("verdict")
                rebuilt = self.execute_agent("PipelineBuilder", context, state)
                state.outputs["PipelineBuilder"] = rebuilt
                print("  ✓ PipelineBuilder re-drafted", flush=True)

                revalidated = self.execute_agent(
                    "Validator", state.get_context_for_agent("Validator"), state
                )
                print(f"  ✓ Validator re-ran → {revalidated.get('verdict')}", flush=True)
                state.event("Orchestrator", "repair_completed", {
                    "round": round_no,
                    "new_verdict": revalidated.get("verdict"),
                    "quality_score": revalidated.get("quality_score"),
                })
                verdict_result = revalidated
            except Exception as exc:
                print(f"  ✗ repair round {round_no} failed: {exc}", flush=True)
                state.event("Orchestrator", "repair_failed", {
                    "round": round_no,
                    "error": str(exc),
                })
                return verdict_result

        return verdict_result

    def run(
        self,
        task: Dict[str, Any],
        evidence_dir: str = "evidence",
        dry_run: bool = False,
    ) -> tuple[RunState, pathlib.Path]:
        """Run the orchestration pipeline."""
        state = RunState(task=task)
        pathlib.Path(evidence_dir).mkdir(parents=True, exist_ok=True)

        state.event("Orchestrator", "task_received", {"kind": task.get("kind")})

        for agent_name, next_status in self.STAGES:
            print(f"\n=== Executing {agent_name} ===", flush=True)

            # Build context for this agent
            context = state.get_context_for_agent(agent_name)

            # Execute agent
            try:
                result = self.execute_agent(agent_name, context, state, dry_run)
                state.outputs[agent_name] = result
                state.status = next_status

                print(f"✓ {agent_name} completed", flush=True)

                # Validator failure feeds repair_hints back to PipelineBuilder.
                if agent_name == "Validator" and not dry_run:
                    result = self._repair_loop(state, result)
                    state.outputs["Validator"] = result

                # The Reviewer's verdict is the run's terminal state — a
                # rejection must not be reported as a completed run.
                if agent_name == "Reviewer":
                    verdict = result.get("verdict", "")
                    if verdict == "rejected":
                        state.status = "rejected"
                        state.event("Orchestrator", "review_rejected", {
                            "reason": result.get("decision_reason", ""),
                            "blockers": result.get("blockers", []),
                        })
                        print("⛔ Rejected by Reviewer", flush=True)
                        break
                    if verdict == "needs_approval" or task.get("risk") in {"high", "critical"}:
                        state.pending_approval = {
                            "reason": result.get("decision_reason") or "high-risk deployment",
                            "required_role": (result.get("approval_context") or {}).get(
                                "required_role", "human_change_approver"
                            ),
                        }
                        state.status = "approval_required"
                        state.event("Orchestrator", "approval_requested", state.pending_approval)
                        print("⚠ Approval required", flush=True)
                        break

            except Exception as e:
                print(f"✗ {agent_name} failed: {e}", flush=True)
                state.status = "failed"
                state.event("Orchestrator", "run_aborted", {
                    "failed_agent": agent_name,
                    "error": str(e),
                })
                break

        if state.status == "reviewed":
            state.status = "completed"
            state.event("Orchestrator", "evidence_sealed", {
                "rollback": "pipeline previous version"
            })

        # Save evidence
        out = pathlib.Path(evidence_dir) / f"{state.run_id}.json"
        state.save(out)

        # Save messages
        self.message_bus.save(state.run_id)

        return state, out

    def close(self):
        """Clean up resources."""
        self.client.close()


def main():
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="DataFlow AgentTeams Orchestrator")
    ap.add_argument("--task", required=True, help="Path to task JSON file")
    ap.add_argument("--dry-run", action="store_true", help="Dry run without calling Claude API")
    ap.add_argument("--config", default="config/config.yaml", help="Path to config file")
    ap.add_argument("--evidence-dir", default="evidence", help="Evidence directory")

    args = ap.parse_args()

    # Load config
    config = Config(args.config)

    # Load task
    task = json.loads(pathlib.Path(args.task).read_text())

    # Run orchestrator
    orchestrator = AgentOrchestrator(config)
    try:
        state, path = orchestrator.run(task, evidence_dir=args.evidence_dir, dry_run=args.dry_run)

        # Print summary
        result = {
            "run_id": state.run_id,
            "status": state.status,
            "evidence": str(path),
            "agent_states": state.agent_states,
        }
        print("\n" + "=" * 60, flush=True)
        print("EXECUTION SUMMARY", flush=True)
        print("=" * 60, flush=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    finally:
        orchestrator.close()


if __name__ == "__main__":
    main()
