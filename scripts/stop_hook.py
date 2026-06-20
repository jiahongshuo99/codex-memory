#!/usr/bin/env python3
"""Optional synchronous Stop hook extraction for Codex Agent Memory."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from hook_debug import text_snippet, write_flow_log


TRUE_VALUES = {"1", "true", "yes", "on"}


def enabled() -> bool:
    value = os.environ.get("CODEX_AGENT_MEMORY_EXTRACT_ON_STOP", "")
    return value.strip().lower() in TRUE_VALUES


def cli_command() -> str:
    configured = os.environ.get("CODEX_AGENT_MEMORY_CLI")
    if configured:
        return configured
    found = shutil.which("codex-memory")
    if found:
        return found
    return str(Path(__file__).resolve().parents[1] / "bin" / "codex-memory")


def main() -> int:
    started = time.monotonic()
    payload = json.loads(sys.stdin.read() or "{}")
    log_record = {
        "hook": "Stop",
        "script": str(Path(__file__).resolve()),
        "action": "extract_start",
        "turn_id": payload.get("turn_id"),
    }
    if not enabled():
        log_record["status"] = "skipped"
        log_record["extract_on_stop"] = False
        log_record["duration_ms"] = round((time.monotonic() - started) * 1000)
        write_flow_log(log_record)
        return 0

    limit = os.environ.get("CODEX_AGENT_MEMORY_EXTRACT_LIMIT", "50")
    cmd = [cli_command(), "extract", "start", "--limit", limit]
    codex_command = os.environ.get("CODEX_AGENT_MEMORY_CODEX_COMMAND")
    if codex_command:
        cmd.extend(["--codex-command", codex_command])

    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    log_record["extract_on_stop"] = True
    log_record["command"] = cmd
    log_record["action_returncode"] = result.returncode
    log_record["stdout"] = text_snippet(result.stdout)
    log_record["stderr"] = text_snippet(result.stderr)
    log_record["status"] = "ok" if result.returncode == 0 else "error"
    log_record["duration_ms"] = round((time.monotonic() - started) * 1000)
    write_flow_log(log_record)
    if result.returncode != 0:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "Stop",
                        "additionalContext": "Codex Agent Memory extraction failed: "
                        + (result.stderr.strip() or result.stdout.strip()),
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
