#!/usr/bin/env python3
"""Debug flow logging helpers for Codex Agent Memory hooks."""

from __future__ import annotations

import json
import os
import datetime as dt
from pathlib import Path
from typing import Any, Dict, Optional

from codex_memory import append_jsonl, memory_root, workspace_key


TRUE_VALUES = {"1", "true", "yes", "on"}
FLOW_LOG_FILE = "hook-flow.jsonl"
BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


def debug_enabled() -> bool:
    value = os.environ.get("CODEX_AGENT_MEMORY_DEBUG", "")
    return value.strip().lower() in TRUE_VALUES


def payload_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    cwd = payload.get("cwd")
    return {
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "cwd": cwd,
        "workspace_key": workspace_key(cwd),
    }


def text_snippet(text: Optional[str], *, limit: int = 500) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3] + "..."


def beijing_now() -> str:
    return dt.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def write_flow_log(record: Dict[str, Any]) -> None:
    if not debug_enabled():
        return

    try:
        root = memory_root()
        payload = {
            "ts": beijing_now(),
            "debug": True,
            **record,
        }
        append_jsonl(root / "system" / FLOW_LOG_FILE, payload)
    except Exception:
        # Debug logging must never break hook execution.
        return
