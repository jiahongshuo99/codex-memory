#!/usr/bin/env python3
"""Optional synchronous Stop hook extraction for Codex Agent Memory."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


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
    _payload = json.loads(sys.stdin.read() or "{}")
    if not enabled():
        return 0

    limit = os.environ.get("CODEX_AGENT_MEMORY_EXTRACT_LIMIT", "50")
    cmd = [cli_command(), "extract", "run", "--limit", limit]
    codex_command = os.environ.get("CODEX_AGENT_MEMORY_CODEX_COMMAND")
    if codex_command:
        cmd.extend(["--codex-command", codex_command])

    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
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
