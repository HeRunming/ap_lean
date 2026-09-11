"""Record provider request and response telemetry without retaining secrets.

The audit payload contains only message sizes/digests, routing fields, and raw
usage identifiers. Message text, authorization headers, and API keys are never
written. Callers may attach a reservation id to correlate an audit with quota
settlement.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.utils import atomic_json_write


def _messages_digest(messages: Sequence[Mapping[str, Any]]) -> tuple[int, int, str]:
    """Return message count, UTF-8 content bytes, and canonical body digest."""
    normalized: list[dict[str, Any]] = []
    content_bytes = 0
    for message in messages:
        role = str(message.get("role", ""))
        content = message.get("content", "")
        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        content_bytes += len(text.encode("utf-8"))
        normalized.append({"role": role, "content": text})
    body = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return len(normalized), content_bytes, hashlib.sha256(body).hexdigest()


def audit_request(
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str = "",
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    provider: str = "",
    base_url: str = "",
    reservation_id: str = "",
) -> dict[str, Any]:
    """Build a secret-free audit record for the final SDK request payload."""
    count, content_bytes, digest = _messages_digest(messages)
    return {
        "kind": "provider_request",
        "provider": str(provider),
        "base_url": str(base_url),
        "model": str(model),
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
        "message_count": count,
        "message_content_utf8_bytes": content_bytes,
        "messages_sha256": digest,
        "reservation_id": str(reservation_id),
    }


def audit_response(response: Any) -> dict[str, Any]:
    """Extract response id, model, and raw provider usage without response text."""
    usage = getattr(response, "usage", None)

    def number(*names: str) -> int:
        for name in names:
            value = getattr(usage, name, None)
            if value is not None:
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    pass
        return 0

    prompt = number("prompt_tokens", "input_tokens")
    completion = number("completion_tokens", "output_tokens")
    total = number("total_tokens") or prompt + completion
    request_id = getattr(response, "_request_id", None) or getattr(response, "request_id", None)
    return {
        "kind": "provider_response",
        "response_id": str(getattr(response, "id", "") or ""),
        "request_id": str(request_id or ""),
        "model": str(getattr(response, "model", "") or ""),
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
    }


def write_audit(
    path: str | Path, *, request: Mapping[str, Any], response: Mapping[str, Any] | None = None
) -> None:
    """Atomically persist request/response audit data while preserving schema."""
    payload: dict[str, Any] = {"request": dict(request)}
    if response is not None:
        payload["response"] = dict(response)
    atomic_json_write(Path(path).expanduser().resolve(), payload)
