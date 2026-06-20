#!/usr/bin/env python3
"""UserPromptSubmit hook for Codex Agent Memory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from hook_debug import payload_summary, text_snippet, write_flow_log


INTERNAL_EXTRACT_ENV = "CODEX_AGENT_MEMORY_INTERNAL_EXTRACT"


def filter_reason(prompt: str) -> Optional[str]:
    stripped = prompt.lstrip()
    if "<hook_prompt" in prompt or "</hook_prompt>" in prompt:
        return "hook_prompt_injection"
    memory_prompt_markers = [
        "# Codex Agent Memory Structure",
        "# Codex Agent Memory Extraction Rules",
        "Existing canonical memory:",
        "Inbox entries:",
        "Return only JSON with top-level keys `candidates` and `ignored`",
    ]
    marker_count = sum(1 for marker in memory_prompt_markers if marker in prompt)
    if stripped.startswith("# Codex Agent Memory Structure") and marker_count >= 3:
        return "codex_agent_memory_extraction_prompt"
    if marker_count >= 4:
        return "codex_agent_memory_extraction_prompt"
    return None


def main() -> int:
    started = time.monotonic()
    payload = json.loads(sys.stdin.read() or "{}")
    log_record = {
        "hook": "UserPromptSubmit",
        "script": str(Path(__file__).resolve()),
        "action": "inbox_append",
        **payload_summary(payload),
    }
    if os.environ.get(INTERNAL_EXTRACT_ENV):
        log_record["status"] = "skipped"
        log_record["skip_reason"] = "internal_extract"
        log_record["duration_ms"] = round((time.monotonic() - started) * 1000)
        write_flow_log(log_record)
        return 0

    script_dir = Path(__file__).resolve().parent
    cli = script_dir / "codex_memory.py"
    prompt = payload.get("prompt", "")
    reason = filter_reason(prompt)
    if reason:
        log_record["status"] = "filtered"
        log_record["filter_reason"] = reason
        log_record["prompt_chars"] = len(prompt)
        log_record["duration_ms"] = round((time.monotonic() - started) * 1000)
        write_flow_log(log_record)
        return 0

    cmd = [
        sys.executable,
        str(cli),
        "inbox",
        "append",
        "--source",
        "user_prompt",
        "--text-stdin",
    ]
    if payload.get("session_id"):
        cmd.extend(["--session-id", payload["session_id"]])
        cmd.extend(["--codex-session-id", payload["session_id"]])
    if payload.get("turn_id"):
        cmd.extend(["--turn-id", payload["turn_id"]])
    if payload.get("cwd"):
        cmd.extend(["--cwd", payload["cwd"]])

    result = subprocess.run(cmd, input=prompt, text=True, capture_output=True, check=False)
    log_record["action_returncode"] = result.returncode
    log_record["stderr"] = text_snippet(result.stderr)
    if result.returncode != 0:
        log_record["status"] = "error"
        log_record["duration_ms"] = round((time.monotonic() - started) * 1000)
        write_flow_log(log_record)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": f"Codex Agent Memory inbox append failed: {result.stderr.strip()}",
                    }
                },
                ensure_ascii=False,
            )
        )
        return 0

    output = json.loads(result.stdout)
    log_record["status"] = "ok"
    log_record["entry_id"] = output.get("id")
    log_record["duration_ms"] = round((time.monotonic() - started) * 1000)
    write_flow_log(log_record)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": output["protocol"],
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
