#!/usr/bin/env python3
"""Local text memory CLI for Codex Agent Memory."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO


DEFAULT_ROOT = Path.home() / ".codex" / "codex-agent-memory"
INBOX_FILE = "user-prompts.jsonl"
ALLOWED_KINDS = {
    "user_preference",
    "user_constraint",
    "user_profile",
    "engineering_principle",
    "engineering_workflow",
    "engineering_standard",
    "engineering_gotcha",
    "engineering_stack_decision",
    "workspace_overview",
    "workspace_principle",
    "workspace_workflow",
    "workspace_standard",
    "workspace_stack",
    "workspace_gotcha",
    "domain_concept",
    "domain_rule",
    "domain_decision",
    "domain_gotcha",
}
ALLOWED_OPERATIONS = {"append_bullet"}
PROTOCOL_TEMPLATE = (
    "Memory root: {root}\n"
    "If memory may help, inspect memory/index.md and read only relevant files. "
    "Use memory as background context; current user instructions override memory. "
    "Do not write canonical memory directly unless explicitly asked."
)


class CliError(Exception):
    pass


def memory_root() -> Path:
    return Path(os.environ.get("CODEX_AGENT_MEMORY_ROOT", DEFAULT_ROOT)).expanduser()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def ensure_base(root: Path) -> None:
    for rel in [
        "inbox",
        "canonical/user",
        "canonical/workspaces",
        "system/locks",
        "tmp",
    ]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    index = root / "index.md"
    if not index.exists():
        index.write_text(
            "# Codex Agent Memory Index\n\n"
            "## User\n\n"
            "- `canonical/user/preferences.md`: Durable user collaboration preferences.\n"
            "- `canonical/user/constraints.md`: User boundaries and permission preferences.\n"
            "- `canonical/user/profile.md`: Stable user background explicitly worth remembering.\n\n"
            "## Workspaces\n\n"
            "- `canonical/workspaces/<workspace-key>/principles.md`: Durable engineering judgment standards.\n"
            "- `canonical/workspaces/<workspace-key>/workflows.md`: Reusable development workflows.\n"
            "- `canonical/workspaces/<workspace-key>/standards.md`: Testing, review, release, and style standards.\n"
            "- `canonical/workspaces/<workspace-key>/gotchas.md`: Reusable failure patterns and caveats.\n",
            encoding="utf-8",
        )


def read_stdin_text() -> str:
    return sys.stdin.read().strip()


def workspace_key(cwd: Optional[str]) -> Optional[str]:
    if not cwd:
        return None
    name = Path(cwd).expanduser().resolve().name
    return name or None


def make_id(source: str, text: str, timestamp: str, session_id: Optional[str], turn_id: Optional[str]) -> str:
    prefix = "up" if source == "user_prompt" else "ev"
    compact_ts = timestamp.replace("-", "").replace(":", "").replace("+00:00", "Z")
    digest = hashlib.sha256(f"{source}\0{timestamp}\0{session_id}\0{turn_id}\0{text}".encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{compact_ts}_{digest}"


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def processed_ids(root: Path) -> set[str]:
    ids = set()
    for row in read_jsonl(root / "system" / "processed.jsonl"):
        if row.get("id"):
            ids.add(row["id"])
    return ids


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Optional[TextIO] = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def command_inbox_append(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    text = read_stdin_text() if args.text_stdin or not args.text else args.text
    if not text:
        raise CliError("inbox append requires prompt text on stdin or --text")
    timestamp = args.timestamp or iso_now()
    entry_id = args.id or make_id(args.source, text, timestamp, args.session_id, args.turn_id)
    entry = {
        "id": entry_id,
        "ts": timestamp,
        "source": args.source,
        "session_id": args.session_id,
        "turn_id": args.turn_id,
        "cwd": args.cwd,
        "workspace_key": args.workspace_key or workspace_key(args.cwd),
        "text": text,
    }
    append_jsonl(root / "inbox" / INBOX_FILE, entry)
    print(json.dumps({"id": entry_id, "protocol": PROTOCOL_TEMPLATE.format(root=root)}, ensure_ascii=False))
    return 0


def pending_entries(root: Path) -> List[Dict[str, Any]]:
    done = processed_ids(root)
    return [row for row in read_jsonl(root / "inbox" / INBOX_FILE) if row.get("id") not in done]


def command_inbox_pending(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    entries = pending_entries(root)
    if args.json:
        print(json.dumps(entries, ensure_ascii=False))
    else:
        for entry in entries:
            print(f"{entry.get('id')} {entry.get('ts')} {entry.get('source')}")
    return 0


def make_batch_id() -> str:
    timestamp = iso_now().replace("-", "").replace(":", "").replace("+00:00", "Z")
    digest = hashlib.sha256(os.urandom(32)).hexdigest()[:8]
    return f"batch_{timestamp}_{digest}"


def claim_entries(root: Path, limit: Optional[int]) -> Dict[str, Any]:
    with FileLock(root / "system" / "locks" / "extract-claim.lock"):
        entries = pending_entries(root)
        if limit:
            entries = entries[:limit]
        batch_id = make_batch_id()
        claimed_at = iso_now()
        for entry in entries:
            append_jsonl(
                root / "system" / "processed.jsonl",
                {
                    "id": entry["id"],
                    "status": "processing",
                    "batch_id": batch_id,
                    "claimed_at": claimed_at,
                },
            )
        return {"batch_id": batch_id, "entries": entries}


def command_extract_claim(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    print(json.dumps(claim_entries(root, args.limit), ensure_ascii=False))
    return 0


def canonical_path(root: Path, target_file: str) -> Path:
    if target_file.startswith("/") or ".." in Path(target_file).parts:
        raise CliError(f"target_file is outside canonical tree: {target_file}")
    path = root / target_file
    canonical_root = (root / "canonical").resolve()
    resolved = path.resolve()
    if canonical_root not in [resolved, *resolved.parents]:
        raise CliError(f"target_file is outside canonical tree: {target_file}")
    if path.suffix != ".md":
        raise CliError(f"target_file must be markdown: {target_file}")
    return path


def validate_candidate(candidate: Dict[str, Any], root: Path) -> Path:
    kind = candidate.get("kind")
    operation = candidate.get("operation")
    if kind not in ALLOWED_KINDS:
        raise CliError(f"unsupported candidate kind: {kind}")
    if operation not in ALLOWED_OPERATIONS:
        raise CliError(f"unsupported operation: {operation}")
    content = candidate.get("content", "").strip()
    if not content:
        raise CliError("candidate content is empty")
    if len(content) > 500:
        raise CliError("candidate content is too long")
    sources = candidate.get("source_ids") or []
    if not isinstance(sources, list) or not sources:
        raise CliError("candidate source_ids must be a non-empty list")
    return canonical_path(root, candidate.get("target_file", ""))


def append_bullet(path: Path, content: str, source_ids: Iterable[str], reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        title = path.stem.replace("-", " ").replace("_", " ").title()
        path.write_text(f"# {title}\n\n", encoding="utf-8")
    existing = path.read_text(encoding="utf-8")
    bullet = f"- {content}\n  Source: {', '.join(source_ids)}"
    if reason:
        bullet += f"\n  Reason: {reason}"
    bullet += "\n"
    if bullet not in existing:
        with path.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(bullet)


def write_checkpoint(root: Path, processed_source_ids: List[str]) -> None:
    if not processed_source_ids:
        return
    checkpoint_path = root / "system" / "checkpoint.json"
    prior: Dict[str, Any] = {}
    if checkpoint_path.exists():
        prior = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint = {
        "version": 1,
        "last_processed_id": processed_source_ids[-1],
        "updated_at": iso_now(),
        "processed_count": int(prior.get("processed_count", 0)) + len(processed_source_ids),
    }
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command_plan_apply(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    raw = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")
    plan = json.loads(raw)
    applied = []
    ignored = []
    processed_source_ids: List[str] = []
    for candidate in plan.get("candidates", []):
        path = validate_candidate(candidate, root)
        append_bullet(
            path,
            candidate["content"].strip(),
            candidate["source_ids"],
            candidate.get("reason", "").strip(),
        )
        for source_id in candidate["source_ids"]:
            processed_source_ids.append(source_id)
            append_jsonl(
                root / "system" / "processed.jsonl",
                {
                    "id": source_id,
                    "status": "processed",
                    "kind": candidate["kind"],
                    "target_file": candidate["target_file"],
                    "processed_at": iso_now(),
                },
            )
        applied.append(candidate)
    for item in plan.get("ignored", []):
        source_id = item.get("source_id")
        if source_id:
            processed_source_ids.append(source_id)
            append_jsonl(
                root / "system" / "processed.jsonl",
                {
                    "id": source_id,
                    "status": "ignored",
                    "reason": item.get("reason", ""),
                    "processed_at": iso_now(),
                },
            )
            ignored.append(item)
    write_checkpoint(root, processed_source_ids)
    print(json.dumps({"applied": len(applied), "ignored": len(ignored)}, ensure_ascii=False))
    return 0


def extraction_prompt(entries: List[Dict[str, Any]], root: Path) -> str:
    structure = read_plugin_asset("memory-structure.md")
    rules_path = root / "system" / "extraction-rules.md"
    rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else read_plugin_asset("extraction-rules.md")
    return (
        f"{structure}\n\n"
        f"{rules}\n\n"
        "Return only JSON with top-level keys `candidates` and `ignored`.\n"
        "Each candidate must use operation `append_bullet` and a target_file under canonical/.\n\n"
        "Inbox entries:\n"
        f"{json.dumps(entries, ensure_ascii=False, indent=2)}\n"
    )


def read_plugin_asset(name: str) -> str:
    path = Path(__file__).resolve().parents[1] / "assets" / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def command_extract_run(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    if args.dry_run:
        entries = pending_entries(root)
        if args.limit:
            entries = entries[: args.limit]
    else:
        claim = claim_entries(root, args.limit)
        entries = claim["entries"]
    if not entries:
        print(json.dumps({"status": "no_pending_entries"}, ensure_ascii=False))
        return 0
    prompt = extraction_prompt(entries, root)
    if args.dry_run:
        print(prompt)
        return 0
    codex_cmd = args.codex_command or "codex"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(prompt)
        prompt_path = fh.name
    try:
        run = subprocess.run(
            [codex_cmd, "exec", "--full-auto", "--skip-git-repo-check", "-"],
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        Path(prompt_path).unlink(missing_ok=True)
    if run.returncode != 0:
        raise CliError(run.stderr.strip() or "codex extraction command failed")
    try:
        plan = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise CliError(f"Codex CLI did not return JSON: {exc}") from exc
    raw = json.dumps(plan, ensure_ascii=False)
    sys.stdin = _StringStdin(raw)
    return command_plan_apply(argparse.Namespace(stdin=True, file=None))


class _StringStdin:
    def __init__(self, value: str):
        self.value = value

    def read(self) -> str:
        return self.value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-memory")
    sub = parser.add_subparsers(dest="command", required=True)

    inbox = sub.add_parser("inbox")
    inbox_sub = inbox.add_subparsers(dest="inbox_command", required=True)
    append = inbox_sub.add_parser("append")
    append.add_argument("--source", default="user_prompt")
    append.add_argument("--session-id")
    append.add_argument("--turn-id")
    append.add_argument("--cwd")
    append.add_argument("--workspace-key")
    append.add_argument("--timestamp")
    append.add_argument("--id")
    append.add_argument("--text")
    append.add_argument("--text-stdin", action="store_true")
    append.set_defaults(func=command_inbox_append)
    pending = inbox_sub.add_parser("pending")
    pending.add_argument("--json", action="store_true")
    pending.set_defaults(func=command_inbox_pending)

    plan = sub.add_parser("plan")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    apply = plan_sub.add_parser("apply")
    apply.add_argument("--stdin", action="store_true")
    apply.add_argument("--file")
    apply.set_defaults(func=command_plan_apply)

    extract = sub.add_parser("extract")
    extract_sub = extract.add_subparsers(dest="extract_command", required=True)
    claim = extract_sub.add_parser("claim")
    claim.add_argument("--limit", type=int)
    claim.set_defaults(func=command_extract_claim)
    run = extract_sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--limit", type=int)
    run.add_argument("--codex-command")
    run.set_defaults(func=command_extract_run)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
