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
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO


DEFAULT_ROOT = Path.home() / ".codex" / "codex-agent-memory"
INBOX_FILE = "user-prompts.jsonl"
EXTRACT_JOBS_FILE = "extract-jobs.jsonl"
DEFAULT_EXTRACT_MODEL = "gpt-5.4"
DEFAULT_EXTRACT_EFFORT = "medium"
DEFAULT_EXTRACT_MAX_BATCH_CHARS = 100_000
DEFAULT_EXTRACT_TIMEOUT_SEC = 900
INTERNAL_EXTRACT_ENV = "CODEX_AGENT_MEMORY_INTERNAL_EXTRACT"
LOG_SNIPPET_CHARS = 1200
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


BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


def local_now() -> dt.datetime:
    return dt.datetime.now(BEIJING_TZ)


def iso_now() -> str:
    return local_now().isoformat(timespec="seconds")


def ensure_base(root: Path) -> None:
    for rel in [
        "inbox",
        "canonical/user",
        "canonical/workspaces",
        "system/locks",
        "tmp",
    ]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    ensure_git_repo(root)


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
    compact_ts = timestamp.replace("-", "").replace(":", "").replace("+08:00", "+0800")
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


def text_snippet(text: Optional[str], *, limit: int = LOG_SNIPPET_CHARS) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit]


def text_tail(text: Optional[str], *, limit: int = LOG_SNIPPET_CHARS) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[-limit:]


def error_code_from_text(text: str) -> str:
    lowered = text.lower()
    if "input exceeds the maximum length" in lowered or "input_too_large" in lowered:
        return "input_too_large"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "json" in lowered:
        return "invalid_json"
    return "codex_failed"


def error_event(error_code: str, stderr: Optional[str] = None, stdout: Optional[str] = None) -> Dict[str, Any]:
    return {
        "error_code": error_code,
        "stderr_chars": len(stderr or ""),
        "stdout_chars": len(stdout or ""),
        "stderr_head": text_snippet(stderr),
        "stderr_tail": text_tail(stderr),
        "stdout_head": text_snippet(stdout),
        "stdout_tail": text_tail(stdout),
    }


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def ensure_git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        init = run_git(root, "init")
        if init.returncode != 0:
            raise CliError(init.stderr.strip() or "failed to initialize memory git repo")
    gitignore = root / ".gitignore"
    desired = "tmp/\nsystem/locks/\n"
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8")
        additions = [line for line in desired.splitlines() if line and line not in text.splitlines()]
        if additions:
            if text and not text.endswith("\n"):
                text += "\n"
            gitignore.write_text(text + "\n".join(additions) + "\n", encoding="utf-8")
    else:
        gitignore.write_text(desired, encoding="utf-8")
    if run_git(root, "config", "--get", "user.name").returncode != 0:
        run_git(root, "config", "user.name", "Codex Agent Memory")
    if run_git(root, "config", "--get", "user.email").returncode != 0:
        run_git(root, "config", "user.email", "codex-agent-memory@local")


def git_has_changes(root: Path) -> bool:
    status = run_git(root, "status", "--porcelain")
    if status.returncode != 0:
        raise CliError(status.stderr.strip() or "failed to inspect memory git status")
    return bool(status.stdout.strip())


def commit_memory_changes(root: Path, *, job_id: Optional[str], reason: str) -> Optional[str]:
    ensure_git_repo(root)
    add = run_git(root, "add", "-A")
    if add.returncode != 0:
        raise CliError(add.stderr.strip() or "failed to stage memory changes")
    if not git_has_changes(root):
        return None
    message = f"Memory extraction {job_id or 'manual'}: {reason}"
    commit = run_git(root, "commit", "-m", message)
    if commit.returncode != 0:
        raise CliError(commit.stderr.strip() or "failed to commit memory changes")
    rev = run_git(root, "rev-parse", "--short", "HEAD")
    if rev.returncode != 0:
        raise CliError(rev.stderr.strip() or "failed to read memory commit")
    return rev.stdout.strip()


def processed_ids(root: Path) -> set[str]:
    ids = set()
    for row in read_jsonl(root / "system" / "processed.jsonl"):
        if row.get("id"):
            ids.add(row["id"])
    return ids


class FileLock:
    def __init__(self, path: Path, *, blocking: bool = True):
        self.path = path
        self.blocking = blocking
        self.handle: Optional[TextIO] = None
        self.acquired = False

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8")
        flags = fcntl.LOCK_EX
        if not self.blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(self.handle.fileno(), flags)
            self.acquired = True
        except BlockingIOError:
            self.acquired = False
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.handle and self.acquired:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        if self.handle:
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
    timestamp = iso_now().replace("-", "").replace(":", "").replace("+08:00", "+0800")
    digest = hashlib.sha256(os.urandom(32)).hexdigest()[:8]
    return f"batch_{timestamp}_{digest}"


def entry_size(entry: Dict[str, Any]) -> int:
    return len(json.dumps(entry, ensure_ascii=False))


def mark_entries_failed(root: Path, entries: Iterable[Dict[str, Any]], reason: str, *, job_id: Optional[str] = None) -> None:
    failed_at = iso_now()
    for entry in entries:
        if not entry.get("id"):
            continue
        payload = {
            "id": entry["id"],
            "status": "failed",
            "reason": reason,
            "failed_at": failed_at,
        }
        if job_id:
            payload["job_id"] = job_id
        append_jsonl(root / "system" / "processed.jsonl", payload)


def claim_entries(root: Path, limit: Optional[int], max_batch_chars: Optional[int] = None) -> Dict[str, Any]:
    with FileLock(root / "system" / "locks" / "extract-claim.lock"):
        pending = pending_entries(root)
        entries: List[Dict[str, Any]] = []
        oversized: List[Dict[str, Any]] = []
        total_chars = 0
        for entry in pending:
            if limit and len(entries) >= limit:
                break
            size = entry_size(entry)
            if max_batch_chars and size > max_batch_chars:
                if entries:
                    break
                oversized.append(entry)
                append_jsonl(
                    root / "system" / "processed.jsonl",
                    {
                        "id": entry["id"],
                        "status": "failed",
                        "reason": "entry_exceeds_max_batch_chars",
                        "failed_at": iso_now(),
                        "entry_chars": size,
                        "max_batch_chars": max_batch_chars,
                    },
                )
                continue
            if max_batch_chars and entries and total_chars + size > max_batch_chars:
                break
            entries.append(entry)
            total_chars += size
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
        return {
            "batch_id": batch_id,
            "entries": entries,
            "entry_count": len(entries),
            "entry_chars": total_chars,
            "oversized": oversized,
        }


def select_entries_for_batch(entries: List[Dict[str, Any]], limit: Optional[int], max_batch_chars: int) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    total_chars = 0
    for entry in entries:
        if limit and len(selected) >= limit:
            break
        size = entry_size(entry)
        if size > max_batch_chars:
            if selected:
                break
            continue
        if selected and total_chars + size > max_batch_chars:
            break
        selected.append(entry)
        total_chars += size
    return selected


def command_extract_claim(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    print(json.dumps(claim_entries(root, args.limit, args.max_batch_chars), ensure_ascii=False))
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


def extract_model(args: argparse.Namespace) -> str:
    return args.model or os.environ.get("CODEX_AGENT_MEMORY_EXTRACT_MODEL") or DEFAULT_EXTRACT_MODEL


def extract_effort(args: argparse.Namespace) -> str:
    return args.effort or os.environ.get("CODEX_AGENT_MEMORY_EXTRACT_EFFORT") or DEFAULT_EXTRACT_EFFORT


def extract_max_batch_chars(args: argparse.Namespace) -> int:
    configured = args.max_batch_chars or os.environ.get("CODEX_AGENT_MEMORY_EXTRACT_MAX_BATCH_CHARS")
    return int(configured or DEFAULT_EXTRACT_MAX_BATCH_CHARS)


def extract_timeout_sec(args: argparse.Namespace) -> int:
    configured = args.timeout_sec or os.environ.get("CODEX_AGENT_MEMORY_EXTRACT_TIMEOUT_SEC")
    return int(configured or DEFAULT_EXTRACT_TIMEOUT_SEC)


def run_codex_extraction(
    *,
    root: Path,
    job_id: Optional[str],
    batch_index: int,
    batch_id: str,
    entries: List[Dict[str, Any]],
    codex_cmd: str,
    model: str,
    effort: str,
    timeout_sec: int,
) -> int:
    prompt = extraction_prompt(entries, root)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(prompt)
        prompt_path = fh.name
    output_path = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
    output_path.close()
    command = [
        codex_cmd,
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--output-last-message",
        output_path.name,
        "-",
    ]
    if job_id:
        append_job_event(
            root,
            {
                "job_id": job_id,
                "status": "running",
                "phase": "codex_start",
                "batch_index": batch_index,
                "batch_id": batch_id,
                "entry_count": len(entries),
                "entry_ids": [entry.get("id") for entry in entries],
                "prompt_chars": len(prompt),
                "model": model,
                "effort": effort,
                "timeout_sec": timeout_sec,
                "phase_at": iso_now(),
            },
        )
    started = time.monotonic()
    env = os.environ.copy()
    env[INTERNAL_EXTRACT_ENV] = "1"
    try:
        run = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        mark_entries_failed(root, entries, "codex_timeout", job_id=job_id)
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "phase": "codex_timeout",
                    "batch_index": batch_index,
                    "batch_id": batch_id,
                    "finished_at": iso_now(),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "returncode": None,
                    **error_event("timeout", stderr, stdout),
                },
            )
        raise CliError(f"Codex extraction timed out after {timeout_sec}s")
    finally:
        Path(prompt_path).unlink(missing_ok=True)
    if run.returncode != 0:
        Path(output_path.name).unlink(missing_ok=True)
        stderr = run.stderr.strip()
        stdout = run.stdout.strip()
        code = error_code_from_text(stderr or stdout)
        mark_entries_failed(root, entries, code, job_id=job_id)
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "phase": "codex_failed",
                    "batch_index": batch_index,
                    "batch_id": batch_id,
                    "finished_at": iso_now(),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "returncode": run.returncode,
                    **error_event(code, stderr, stdout),
                },
            )
        raise CliError(f"Codex extraction failed: {code}")
    try:
        final_output = Path(output_path.name).read_text(encoding="utf-8").strip()
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "running",
                    "phase": "codex_finished",
                    "batch_index": batch_index,
                    "batch_id": batch_id,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "stdout_chars": len(run.stdout or ""),
                    "stderr_chars": len(run.stderr or ""),
                    "final_output_chars": len(final_output),
                    "phase_at": iso_now(),
                },
            )
        plan = json.loads(final_output)
    except json.JSONDecodeError as exc:
        mark_entries_failed(root, entries, "invalid_json", job_id=job_id)
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "phase": "invalid_json",
                    "batch_index": batch_index,
                    "batch_id": batch_id,
                    "finished_at": iso_now(),
                    "returncode": 1,
                    "error_code": "invalid_json",
                    "json_error": str(exc),
                    "final_output_chars": len(final_output if "final_output" in locals() else ""),
                    "final_output_head": text_snippet(final_output if "final_output" in locals() else ""),
                    "final_output_tail": text_tail(final_output if "final_output" in locals() else ""),
                },
            )
        raise CliError(f"Codex CLI did not return JSON: {exc}") from exc
    finally:
        Path(output_path.name).unlink(missing_ok=True)
    covered_ids = {
        source_id
        for candidate in plan.get("candidates", [])
        for source_id in candidate.get("source_ids", [])
    }
    covered_ids.update(item.get("source_id") for item in plan.get("ignored", []) if item.get("source_id"))
    missing_ids = [entry["id"] for entry in entries if entry.get("id") not in covered_ids]
    if missing_ids:
        ignored = plan.setdefault("ignored", [])
        for source_id in missing_ids:
            ignored.append(
                {
                    "source_id": source_id,
                    "reason": "提取结果未覆盖该条已认领内容，自动标记为 ignored，避免长期停留在 processing 状态。",
                }
            )
    raw = json.dumps(plan, ensure_ascii=False)
    sys.stdin = _StringStdin(raw)
    if job_id:
        append_job_event(
            root,
            {
                "job_id": job_id,
                "status": "running",
                "phase": "plan_apply_start",
                "batch_index": batch_index,
                "batch_id": batch_id,
                "candidate_count": len(plan.get("candidates", [])),
                "ignored_count": len(plan.get("ignored", [])),
                "phase_at": iso_now(),
            },
        )
    result = command_plan_apply(argparse.Namespace(stdin=True, file=None))
    if job_id:
        append_job_event(
            root,
            {
                "job_id": job_id,
                "status": "running" if result == 0 else "failed",
                "phase": "batch_done" if result == 0 else "plan_apply_failed",
                "batch_index": batch_index,
                "batch_id": batch_id,
                "phase_at": iso_now(),
                "returncode": result,
            },
        )
    if result != 0:
        mark_entries_failed(root, entries, "plan_apply_failed", job_id=job_id)
    return result


def run_extraction_batches(
    *,
    root: Path,
    args: argparse.Namespace,
    job_id: Optional[str],
    max_batch_chars: int,
    timeout_sec: int,
) -> Dict[str, int]:
    codex_cmd = args.codex_command or "codex"
    model = extract_model(args)
    effort = extract_effort(args)
    processed_batches = 0
    processed_entries = 0
    remaining_limit = args.limit

    while remaining_limit is None or remaining_limit > 0:
        claim = claim_entries(root, remaining_limit, max_batch_chars)
        entries = claim["entries"]
        oversized = claim.get("oversized", [])
        if job_id and oversized:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "running",
                    "phase": "oversized_entries_failed",
                    "entry_ids": [entry.get("id") for entry in oversized],
                    "entry_chars": [entry_size(entry) for entry in oversized],
                    "max_batch_chars": max_batch_chars,
                    "phase_at": iso_now(),
                },
            )
        if not entries:
            break
        batch_index = processed_batches + 1
        if job_id:
            append_job_event(
                root,
                {
                    "job_id": job_id,
                    "status": "running",
                    "phase": "batch_claimed",
                    "batch_index": batch_index,
                    "batch_id": claim["batch_id"],
                    "entry_count": len(entries),
                    "entry_chars": claim.get("entry_chars"),
                    "entry_ids": [entry.get("id") for entry in entries],
                    "phase_at": iso_now(),
                },
            )
        result = run_codex_extraction(
            root=root,
            job_id=job_id,
            batch_index=batch_index,
            batch_id=claim["batch_id"],
            entries=entries,
            codex_cmd=codex_cmd,
            model=model,
            effort=effort,
            timeout_sec=timeout_sec,
        )
        if result != 0:
            if job_id:
                append_job_event(
                    root,
                    {
                        "job_id": job_id,
                        "status": "failed",
                        "finished_at": iso_now(),
                        "returncode": result,
                    },
            )
            return {"returncode": result, "batch_count": processed_batches, "entry_count": processed_entries}
        processed_batches += 1
        processed_entries += len(entries)
        if remaining_limit is not None:
            remaining_limit -= len(entries)

    if job_id:
        append_job_event(
            root,
            {
                "job_id": job_id,
                "status": "succeeded",
                "finished_at": iso_now(),
                "returncode": 0,
                "batch_count": processed_batches,
                "entry_count": processed_entries,
                "message": "no_pending_entries" if processed_entries == 0 else "completed",
            },
        )
    if processed_entries == 0:
        print(json.dumps({"status": "no_pending_entries"}, ensure_ascii=False))
    return {"returncode": 0, "batch_count": processed_batches, "entry_count": processed_entries}


def command_extract_run(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    job_id = getattr(args, "job_id", None)
    max_batch_chars = extract_max_batch_chars(args)
    timeout_sec = extract_timeout_sec(args)
    if job_id:
        append_job_event(
            root,
            {
                "job_id": job_id,
                "status": "running",
                "phase": "run_start",
                "running_at": iso_now(),
                "max_batch_chars": max_batch_chars,
                "timeout_sec": timeout_sec,
            },
        )
    if args.dry_run:
        entries = select_entries_for_batch(pending_entries(root), args.limit, max_batch_chars)
        if not entries:
            print(json.dumps({"status": "no_pending_entries"}, ensure_ascii=False))
            return 0
        print(extraction_prompt(entries, root))
        return 0

    commit_reason = "completed"
    with FileLock(root / "system" / "locks" / "extract-job.lock", blocking=False) as job_lock:
        if not job_lock.acquired:
            if job_id:
                append_job_event(
                    root,
                    {
                        "job_id": job_id,
                        "status": "skipped",
                        "phase": "already_running",
                        "finished_at": iso_now(),
                        "returncode": 0,
                        "message": "another_extraction_job_is_running",
                    },
                )
            print(json.dumps({"status": "skipped", "reason": "another_extraction_job_is_running"}, ensure_ascii=False))
            return 0
        try:
            result = run_extraction_batches(
                root=root,
                args=args,
                job_id=job_id,
                max_batch_chars=max_batch_chars,
                timeout_sec=timeout_sec,
            )
            if result["returncode"] != 0:
                commit_reason = "failed"
            else:
                commit_reason = "no_pending_entries" if result["entry_count"] == 0 else "completed"
            return result["returncode"]
        except Exception:
            commit_reason = "failed"
            raise
        finally:
            try:
                if job_id:
                    append_job_event(
                        root,
                        {
                            "job_id": job_id,
                            "status": "failed" if commit_reason == "failed" else "succeeded",
                            "phase": "git_commit",
                            "commit_reason": commit_reason,
                            "returncode": 1 if commit_reason == "failed" else 0,
                            "finished_at": iso_now(),
                            "phase_at": iso_now(),
                        },
                    )
                commit_memory_changes(root, job_id=job_id, reason=commit_reason)
            except Exception as exc:
                if job_id:
                    append_job_event(
                        root,
                        {
                            "job_id": job_id,
                            "status": "failed",
                            "phase": "git_commit_failed",
                            "error_code": "git_commit_failed",
                            "stderr_head": text_snippet(str(exc)),
                            "finished_at": iso_now(),
                            "returncode": 1,
                        },
                    )
                raise


def command_extract_start(args: argparse.Namespace) -> int:
    root = memory_root()
    ensure_base(root)
    job_id = make_batch_id().replace("batch_", "job_", 1)
    log_dir = root / "system" / "extract-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{job_id}.stdout.log"
    stderr_path = log_dir / f"{job_id}.stderr.log"
    model = extract_model(args)
    effort = extract_effort(args)
    max_batch_chars = extract_max_batch_chars(args)
    timeout_sec = extract_timeout_sec(args)
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "extract",
        "run",
        "--job-id",
        job_id,
        "--model",
        model,
        "--effort",
        effort,
        "--max-batch-chars",
        str(max_batch_chars),
        "--timeout-sec",
        str(timeout_sec),
    ]
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
    claim.add_argument("--max-batch-chars", type=int)
    claim.set_defaults(func=command_extract_claim)
    log = extract_sub.add_parser("log")
    log.add_argument("--json", action="store_true")
    log.set_defaults(func=command_extract_log)
    run = extract_sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--limit", type=int)
    run.add_argument("--codex-command")
    run.add_argument("--model")
    run.add_argument("--effort")
    run.add_argument("--max-batch-chars", type=int)
    run.add_argument("--timeout-sec", type=int)
    run.add_argument("--job-id")
    run.set_defaults(func=command_extract_run)
    start = extract_sub.add_parser("start")
    start.add_argument("--limit", type=int)
    start.add_argument("--codex-command")
    start.add_argument("--model")
    start.add_argument("--effort")
    start.add_argument("--max-batch-chars", type=int)
    start.add_argument("--timeout-sec", type=int)
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
