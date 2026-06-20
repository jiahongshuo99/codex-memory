#!/usr/bin/env python3
"""UserPromptSubmit hook for Codex Agent Memory."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
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
    if payload.get("turn_id"):
        cmd.extend(["--turn-id", payload["turn_id"]])
    if payload.get("cwd"):
        cmd.extend(["--cwd", payload["cwd"]])

    result = subprocess.run(cmd, input=prompt, text=True, capture_output=True, check=False)
    if result.returncode != 0:
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
