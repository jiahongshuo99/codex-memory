#!/usr/bin/env python3
"""UserPromptSubmit hook for Codex Agent Memory."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from hook_debug import payload_summary, text_snippet, write_flow_log


def main() -> int:
    started = time.monotonic()
    payload = json.loads(sys.stdin.read() or "{}")
    log_record = {
        "hook": "UserPromptSubmit",
        "script": str(Path(__file__).resolve()),
        "action": "inbox_append",
        **payload_summary(payload),
    }
    script_dir = Path(__file__).resolve().parent
    cli = script_dir / "codex_memory.py"
    prompt = payload.get("prompt", "")
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
