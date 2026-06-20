#!/usr/bin/env python3
"""Local text memory CLI for Codex Agent Memory."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO


DEFAULT_ROOT = Path.home() / ".codex" / "codex-agent-memory"
INBOX_FILE = "user-prompts.jsonl"
EXTRACT_JOBS_FILE = "extract-jobs.jsonl"
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
WORKSPACE_KEY_PATTERN = re.compile(r"codex-agent-memory workspace-key:\s*([a-z0-9-]+)")
WORKSPACE_MARKER_TEMPLATE = "<!-- codex-agent-memory workspace-key: {key} -->"
PROTOCOL_TEMPLATE = (
    "Memory root: {root}\n"
    "If memory may help, read only relevant files under canonical/. "
    "Structure and routing rules are defined by the codex-agent-memory plugin. "
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


def read_stdin_text() -> str:
    return sys.stdin.read().strip()


def slugify_key(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "workspace"


def workspace_key(cwd: Optional[str]) -> Optional[str]:
    if not cwd:
        return None
    path = Path(cwd).expanduser().resolve()
    name = path.name or "workspace"
    key = slugify_key(name)
    if not path.exists() or not path.is_dir():
        return key

    agents_path = path / "AGENTS.md"
    if agents_path.exists():
        text = agents_path.read_text(encoding="utf-8")
        match = WORKSPACE_KEY_PATTERN.search(text)
        if match:
            return match.group(1)
        if text and not text.endswith("\n"):
            text += "\n"
        agents_path.write_text(text + "\n" + WORKSPACE_MARKER_TEMPLATE.format(key=key) + "\n", encoding="utf-8")
    else:
        agents_path.write_text(WORKSPACE_MARKER_TEMPLATE.format(key=key) + "\n", encoding="utf-8")
    return key


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
        "codex_session_id": args.codex_session_id or args.session_id,
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


def command_extract_log(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    rows = read_jsonl(root / "system" / "extraction-log.jsonl")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for row in rows:
            print(f"{row.get('source_id')} {row.get('status')} memories={row.get('memory_count')}")
    return 0


def append_job_event(root: Path, event: Dict[str, Any]) -> None:
    append_jsonl(root / "system" / EXTRACT_JOBS_FILE, event)


def latest_jobs(root: Path) -> List[Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for event in read_jsonl(root / "system" / EXTRACT_JOBS_FILE):
        job_id = event.get("job_id")
        if not job_id:
            continue
        if job_id not in latest:
            latest[job_id] = {"job_id": job_id}
            order.append(job_id)
        latest[job_id].update(event)
    return [latest[job_id] for job_id in order]


def command_extract_jobs(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    jobs = latest_jobs(root)
    counts: Dict[str, int] = {}
    for job in jobs:
        status = job.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    payload = {"counts": counts, "jobs": jobs}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for status, count in sorted(counts.items()):
            print(f"{status}: {count}")
        for job in jobs:
            print(f"{job.get('job_id')} {job.get('status')} pid={job.get('pid', '')}")
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


def memory_title(path: Path) -> str:
    titles = {
        "preferences": "偏好",
        "constraints": "约束",
        "profile": "用户资料",
        "overview": "概览",
        "principles": "原则",
        "workflows": "工作流",
        "standards": "标准",
        "stack": "技术栈",
        "stack-decisions": "技术栈决策",
        "gotchas": "易错点",
        "concepts": "概念",
        "rules": "规则",
        "decisions": "决策",
    }
    if path.stem in titles:
        return titles[path.stem]
    return path.stem.replace("-", " ").replace("_", " ").title()


def normalized_memory(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.lower())


def is_similar_memory(existing: str, candidate: str) -> bool:
    existing_norm = normalized_memory(existing)
    candidate_norm = normalized_memory(candidate)
    if not existing_norm or not candidate_norm:
        return False
    if existing_norm in candidate_norm or candidate_norm in existing_norm:
        return True
    return difflib.SequenceMatcher(None, existing_norm, candidate_norm).ratio() >= 0.86


def upsert_bullet(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {memory_title(path)}\n\n", encoding="utf-8")
    existing = path.read_text(encoding="utf-8")
    lines = existing.splitlines()
    candidate = content.strip()
    candidate_line = f"- {candidate}"

    for index, line in enumerate(lines):
        if not line.startswith("- "):
            continue
        current = line[2:].strip()
        if not is_similar_memory(current, candidate):
            continue
        if normalized_memory(candidate) in normalized_memory(current):
            return False
        end = index + 1
        while end < len(lines) and lines[end].startswith("  "):
            end += 1
        lines[index:end] = [candidate_line]
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return True

    with path.open("a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        if existing.strip():
            fh.write("\n")
        fh.write(candidate_line + "\n")
    return True


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


def write_extraction_log(root: Path, plan: Dict[str, Any]) -> None:
    summaries: Dict[str, Dict[str, Any]] = {}
    logged_at = iso_now()
    for candidate in plan.get("candidates", []):
        for source_id in candidate.get("source_ids", []):
            summary = summaries.setdefault(
                source_id,
                {
                    "source_id": source_id,
                    "status": "processed",
                    "memory_count": 0,
                    "candidate_kinds": set(),
                    "target_files": set(),
                    "logged_at": logged_at,
                },
            )
            summary["memory_count"] += 1
            summary["candidate_kinds"].add(candidate.get("kind"))
            summary["target_files"].add(candidate.get("target_file"))
    for item in plan.get("ignored", []):
        source_id = item.get("source_id")
        if source_id and source_id not in summaries:
            summaries[source_id] = {
                "source_id": source_id,
                "status": "ignored",
                "memory_count": 0,
                "candidate_kinds": set(),
                "target_files": set(),
                "reason": item.get("reason", ""),
                "logged_at": logged_at,
            }
    for summary in summaries.values():
        summary["candidate_kinds"] = sorted(value for value in summary["candidate_kinds"] if value)
        summary["target_files"] = sorted(value for value in summary["target_files"] if value)
        append_jsonl(root / "system" / "extraction-log.jsonl", summary)


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
        changed = upsert_bullet(path, candidate["content"].strip())
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
        if changed:
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
    write_extraction_log(root, plan)
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
        "所有写入 canonical/ 的记忆内容和 reason 必须使用中文；如果原文是英文，也要提炼成自然中文。\n"
        "生成候选前必须先检查下面的现有 canonical 记忆；如果已有相同或相近内容，不要重复输出候选。"
        "只有确实有新增信息时，才输出可合并后的新候选内容。\n"
        "Return only JSON with top-level keys `candidates` and `ignored`.\n"
        "Each candidate must use operation `append_bullet` and a target_file under canonical/.\n\n"
        "Existing canonical memory:\n"
        f"{canonical_snapshot(root)}\n\n"
        "Inbox entries:\n"
        f"{json.dumps(entries, ensure_ascii=False, indent=2)}\n"
    )


def canonical_snapshot(root: Path, *, limit: int = 20000) -> str:
    canonical = root / "canonical"
    if not canonical.exists():
        return "(empty)"
    parts: List[str] = []
    total = 0
    for path in sorted(canonical.rglob("*.md")):
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        chunk = f"## {rel}\n{text}\n"
        if total + len(chunk) > limit:
            parts.append("(truncated)")
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts) if parts else "(empty)"


def read_plugin_asset(name: str) -> str:
    path = Path(__file__).resolve().parents[1] / "assets" / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def command_extract_run(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    job_id = getattr(args, "job_id", None)
    if job_id:
        append_job_event(root, {"job_id": job_id, "status": "running", "running_at": iso_now()})
    if args.dry_run:
        entries = pending_entries(root)
        if args.limit:
            entries = entries[: args.limit]
    else:
        claim = claim_entries(root, args.limit)
        entries = claim["entries"]
    if not entries:
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "succeeded",
                    "finished_at": iso_now(),
                    "returncode": 0,
                    "message": "no_pending_entries",
                },
            )
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
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "finished_at": iso_now(),
                    "returncode": run.returncode,
                    "error": run.stderr.strip() or "codex extraction command failed",
                },
            )
        raise CliError(run.stderr.strip() or "codex extraction command failed")
    try:
        plan = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "finished_at": iso_now(),
                    "returncode": 1,
                    "error": f"Codex CLI did not return JSON: {exc}",
                },
            )
        raise CliError(f"Codex CLI did not return JSON: {exc}") from exc
    raw = json.dumps(plan, ensure_ascii=False)
    sys.stdin = _StringStdin(raw)
    result = command_plan_apply(argparse.Namespace(stdin=True, file=None))
    if job_id:
        append_job_event(
            root,
            {
                "job_id": job_id,
                "status": "succeeded" if result == 0 else "failed",
                "finished_at": iso_now(),
                "returncode": result,
            },
        )
    return result


def command_extract_start(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    job_id = make_batch_id().replace("batch_", "job_", 1)
    log_dir = root / "system" / "extract-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{job_id}.stdout.log"
    stderr_path = log_dir / f"{job_id}.stderr.log"
    cmd = [sys.executable, str(Path(__file__).resolve()), "extract", "run", "--job-id", job_id]
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.codex_command:
        cmd.extend(["--codex-command", args.codex_command])
    stdout_fh = stdout_path.open("a", encoding="utf-8")
    stderr_fh = stderr_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        cmd,
        stdout=stdout_fh,
        stderr=stderr_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )
    stdout_fh.close()
    stderr_fh.close()
    job = {
        "job_id": job_id,
        "pid": process.pid,
        "status": "started",
        "started_at": iso_now(),
        "command": cmd,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    append_job_event(root, job)
    print(json.dumps(job, ensure_ascii=False))
    return 0


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
    append.add_argument("--codex-session-id")
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
    log = extract_sub.add_parser("log")
    log.add_argument("--json", action="store_true")
    log.set_defaults(func=command_extract_log)
    run = extract_sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--limit", type=int)
    run.add_argument("--codex-command")
    run.add_argument("--job-id")
    run.set_defaults(func=command_extract_run)
    start = extract_sub.add_parser("start")
    start.add_argument("--limit", type=int)
    start.add_argument("--codex-command")
    start.set_defaults(func=command_extract_start)
    jobs = extract_sub.add_parser("jobs")
    jobs.add_argument("--json", action="store_true")
    jobs.set_defaults(func=command_extract_jobs)
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
