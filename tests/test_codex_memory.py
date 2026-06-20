import json
import fcntl
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CLI = PLUGIN_ROOT / "scripts" / "codex_memory.py"
BIN = PLUGIN_ROOT / "bin" / "codex-memory"
INSTALLER = PLUGIN_ROOT / "scripts" / "install_cli.py"
STOP_HOOK = PLUGIN_ROOT / "scripts" / "stop_hook.py"
USER_PROMPT_HOOK = PLUGIN_ROOT / "scripts" / "user_prompt_submit_hook.py"


def run_cli(tmp_path, *args, input_text=None):
    env = os.environ.copy()
    env["CODEX_AGENT_MEMORY_ROOT"] = str(tmp_path / "memory")
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        input=input_text,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def run_bin(tmp_path, *args, input_text=None):
    env = os.environ.copy()
    env["CODEX_AGENT_MEMORY_ROOT"] = str(tmp_path / "memory")
    return subprocess.run(
        [str(BIN), *args],
        input=input_text,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def git(tmp_path, *args):
    return subprocess.run(
        ["git", "-C", str(tmp_path / "memory"), *args],
        text=True,
        capture_output=True,
        check=False,
    )


class CodexMemoryCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_inbox_append_writes_user_prompt_jsonl_with_stable_metadata(self):
        project = self.tmp_path / "example-repo"
        project.mkdir()
        result = run_cli(
            self.tmp_path,
            "inbox",
            "append",
            "--source",
            "user_prompt",
            "--session-id",
            "sess-1",
            "--turn-id",
            "turn-1",
            "--cwd",
            str(project),
            input_text="记住我喜欢短回答\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["id"].startswith("up_"))
        self.assertIn("Memory root:", payload["protocol"])
        self.assertNotIn("index.md", payload["protocol"])
        self.assertIn("canonical/", payload["protocol"])
        self.assertFalse((self.tmp_path / "memory" / "index.md").exists())

        inbox_file = self.tmp_path / "memory" / "inbox" / "user-prompts.jsonl"
        entries = read_jsonl(inbox_file)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], payload["id"])
        self.assertEqual(entries[0]["source"], "user_prompt")
        self.assertEqual(entries[0]["session_id"], "sess-1")
        self.assertEqual(entries[0]["codex_session_id"], "sess-1")
        self.assertEqual(entries[0]["turn_id"], "turn-1")
        self.assertEqual(entries[0]["cwd"], str(project))
        self.assertEqual(entries[0]["workspace_key"], "example-repo")
        self.assertEqual(entries[0]["text"], "记住我喜欢短回答")
        agents_text = (project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("codex-agent-memory workspace-key: example-repo", agents_text)
        self.assertTrue((self.tmp_path / "memory" / ".git").exists())
        gitignore = (self.tmp_path / "memory" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*", gitignore)
        self.assertIn("!/canonical/**", gitignore)
        self.assertIn("!/system/extract-jobs.jsonl", gitignore)

    def test_workspace_key_reuses_project_agents_file(self):
        project = self.tmp_path / "Readable Project"
        project.mkdir()
        agents = project / "AGENTS.md"
        agents.write_text(
            "# Project Instructions\n\n"
            "<!-- codex-agent-memory workspace-key: readable-project -->\n",
            encoding="utf-8",
        )

        result = run_cli(
            self.tmp_path,
            "inbox",
            "append",
            "--source",
            "user_prompt",
            "--cwd",
            str(project),
            input_text="hello",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        entries = read_jsonl(self.tmp_path / "memory" / "inbox" / "user-prompts.jsonl")
        self.assertEqual(entries[0]["workspace_key"], "readable-project")
        self.assertEqual(agents.read_text(encoding="utf-8").count("codex-agent-memory workspace-key"), 1)

    def test_inbox_append_accepts_explicit_codex_session_id(self):
        result = run_cli(
            self.tmp_path,
            "inbox",
            "append",
            "--source",
            "user_prompt",
            "--session-id",
            "local-session",
            "--codex-session-id",
            "codex-session-123",
            input_text="hello",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        entries = read_jsonl(self.tmp_path / "memory" / "inbox" / "user-prompts.jsonl")
        self.assertEqual(entries[0]["session_id"], "local-session")
        self.assertEqual(entries[0]["codex_session_id"], "codex-session-123")

    def test_pending_entries_are_idempotent_against_processed_log(self):
        first = run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="one")
        second = run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="two")
        first_id = json.loads(first.stdout)["id"]
        second_id = json.loads(second.stdout)["id"]

        processed = self.tmp_path / "memory" / "system" / "processed.jsonl"
        processed.parent.mkdir(parents=True, exist_ok=True)
        processed.write_text(json.dumps({"id": first_id, "status": "processed"}) + "\n")

        result = run_cli(self.tmp_path, "inbox", "pending", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        pending = json.loads(result.stdout)
        self.assertEqual([entry["id"] for entry in pending], [second_id])

    def test_claim_batch_marks_entries_processing_and_prevents_reclaim(self):
        first = run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="one")
        second = run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="two")
        ids = [json.loads(first.stdout)["id"], json.loads(second.stdout)["id"]]

        claim = run_cli(self.tmp_path, "extract", "claim", "--limit", "10")
        self.assertEqual(claim.returncode, 0, claim.stderr)
        claimed = json.loads(claim.stdout)
        self.assertEqual([entry["id"] for entry in claimed["entries"]], ids)
        self.assertTrue(claimed["batch_id"].startswith("batch_"))

        second_claim = run_cli(self.tmp_path, "extract", "claim", "--limit", "10")
        self.assertEqual(second_claim.returncode, 0, second_claim.stderr)
        self.assertEqual(json.loads(second_claim.stdout)["entries"], [])

        processed = read_jsonl(self.tmp_path / "memory" / "system" / "processed.jsonl")
        self.assertEqual(
            {entry["id"]: entry["status"] for entry in processed},
            {ids[0]: "processing", ids[1]: "processing"},
        )

    def test_pending_ignores_processing_entries(self):
        appended = run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="one")
        entry_id = json.loads(appended.stdout)["id"]
        processed = self.tmp_path / "memory" / "system" / "processed.jsonl"
        processed.parent.mkdir(parents=True, exist_ok=True)
        processed.write_text(json.dumps({"id": entry_id, "status": "processing"}) + "\n")

        result = run_cli(self.tmp_path, "inbox", "pending", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_extract_dry_run_includes_memory_structure_contract(self):
        run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="remember a domain rule")

        result = run_cli(self.tmp_path, "extract", "run", "--dry-run", "--limit", "1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Codex Agent Memory Structure", result.stdout)
        self.assertIn("canonical/domains/<domain-key>/", result.stdout)
        self.assertIn("assets/extraction-output.schema.json", result.stdout)

    def test_apply_plan_accepts_domain_memory_kind(self):
        plan = {
            "candidates": [
                {
                    "kind": "domain_decision",
                    "target_file": "canonical/domains/agent-memory/decisions.md",
                    "content": "Canonical memory is split into user, engineering, workspaces, and domains.",
                    "source_ids": ["up_domain_1"],
                }
            ],
            "ignored": [],
        }

        result = run_cli(self.tmp_path, "plan", "apply", "--stdin", input_text=json.dumps(plan))

        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.tmp_path / "memory" / "canonical" / "domains" / "agent-memory" / "decisions.md"
        self.assertIn("Canonical memory is split", target.read_text())

    def test_stop_hook_is_disabled_by_default(self):
        env = {**os.environ, "CODEX_AGENT_MEMORY_ROOT": str(self.tmp_path / "memory")}
        env.pop("CODEX_AGENT_MEMORY_EXTRACT_ON_STOP", None)
        result = subprocess.run(
            [sys.executable, str(STOP_HOOK)],
            input=json.dumps({"hook_event_name": "Stop", "turn_id": "turn-1"}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_stop_hook_starts_async_extraction_when_enabled(self):
        fake_bin = self.tmp_path / "fake-bin"
        fake_bin.mkdir()
        log_path = self.tmp_path / "codex-memory.log"
        fake_cli = fake_bin / "codex-memory"
        fake_cli.write_text(
            "#!/usr/bin/env sh\n"
            f"echo \"$@\" >> {log_path}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_cli.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "CODEX_AGENT_MEMORY_EXTRACT_ON_STOP": "1",
            "CODEX_AGENT_MEMORY_EXTRACT_LIMIT": "7",
        }

        result = subprocess.run(
            [sys.executable, str(STOP_HOOK)],
            input=json.dumps({"hook_event_name": "Stop", "turn_id": "turn-1"}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("extract start --limit 7", log_path.read_text())

    def test_extract_jobs_defaults_to_active_jobs(self):
        jobs = self.tmp_path / "memory" / "system" / "extract-jobs.jsonl"
        jobs.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"job_id": "job-1", "status": "started", "pid": 101, "started_at": "2026-06-20T10:00:00+00:00"},
            {"job_id": "job-1", "status": "running", "running_at": "2026-06-20T10:00:01+00:00"},
            {"job_id": "job-1", "status": "succeeded", "finished_at": "2026-06-20T10:00:02+00:00", "returncode": 0},
            {"job_id": "job-2", "status": "started", "pid": 102, "started_at": "2026-06-20T10:01:00+00:00"},
            {"job_id": "job-3", "status": "started", "pid": 103, "started_at": "2026-06-20T10:02:00+00:00"},
            {"job_id": "job-3", "status": "failed", "finished_at": "2026-06-20T10:02:02+00:00", "returncode": 1},
        ]
        jobs.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        result = run_cli(self.tmp_path, "extract", "jobs", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["scope"], "active")
        self.assertEqual(payload["statuses"], ["running", "started"])
        self.assertEqual(payload["counts"], {"started": 1})
        self.assertEqual(payload["total_counts"], {"failed": 1, "started": 1, "succeeded": 1})
        self.assertEqual([job["job_id"] for job in payload["jobs"]], ["job-2"])

    def test_extract_jobs_can_show_all_or_selected_statuses(self):
        jobs = self.tmp_path / "memory" / "system" / "extract-jobs.jsonl"
        jobs.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"job_id": "job-1", "status": "succeeded", "finished_at": "2026-06-20T10:00:02+00:00"},
            {"job_id": "job-2", "status": "started", "started_at": "2026-06-20T10:01:00+00:00"},
            {"job_id": "job-3", "status": "failed", "finished_at": "2026-06-20T10:02:02+00:00"},
        ]
        jobs.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        all_result = run_cli(self.tmp_path, "extract", "jobs", "--json", "--all", "--limit", "2")
        failed_result = run_cli(self.tmp_path, "extract", "jobs", "--json", "--status", "failed")

        self.assertEqual(all_result.returncode, 0, all_result.stderr)
        all_payload = json.loads(all_result.stdout)
        self.assertEqual(all_payload["scope"], "all")
        self.assertIsNone(all_payload["statuses"])
        self.assertEqual([job["job_id"] for job in all_payload["jobs"]], ["job-3", "job-2"])

        self.assertEqual(failed_result.returncode, 0, failed_result.stderr)
        failed_payload = json.loads(failed_result.stdout)
        self.assertEqual(failed_payload["scope"], "status")
        self.assertEqual(failed_payload["statuses"], ["failed"])
        self.assertEqual([job["job_id"] for job in failed_payload["jobs"]], ["job-3"])

    def test_extract_jobs_summarizes_latest_job_statuses_with_all(self):
        jobs = self.tmp_path / "memory" / "system" / "extract-jobs.jsonl"
        jobs.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"job_id": "job-1", "status": "started", "pid": 101, "started_at": "2026-06-20T10:00:00+00:00"},
            {"job_id": "job-1", "status": "running", "running_at": "2026-06-20T10:00:01+00:00"},
            {"job_id": "job-1", "status": "succeeded", "finished_at": "2026-06-20T10:00:02+00:00", "returncode": 0},
            {"job_id": "job-2", "status": "started", "pid": 102, "started_at": "2026-06-20T10:01:00+00:00"},
            {"job_id": "job-3", "status": "started", "pid": 103, "started_at": "2026-06-20T10:02:00+00:00"},
            {"job_id": "job-3", "status": "failed", "finished_at": "2026-06-20T10:02:02+00:00", "returncode": 1},
        ]
        jobs.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        result = run_cli(self.tmp_path, "extract", "jobs", "--json", "--all")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["counts"], {"failed": 1, "started": 1, "succeeded": 1})
        by_id = {job["job_id"]: job for job in payload["jobs"]}
        self.assertEqual(by_id["job-1"]["status"], "succeeded")
        self.assertEqual(by_id["job-2"]["status"], "started")
        self.assertEqual(by_id["job-3"]["status"], "failed")

    def test_extract_run_uses_configured_model_effort_and_last_message_output(self):
        run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="记住我喜欢中文")
        fake_codex = self.tmp_path / "fake-codex.py"
        argv_path = self.tmp_path / "fake-codex-argv.json"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            f"pathlib.Path({str(argv_path)!r}).write_text(json.dumps({{'argv': sys.argv, 'internal': os.environ.get('CODEX_AGENT_MEMORY_INTERNAL_EXTRACT')}}, ensure_ascii=False), encoding='utf-8')\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            "out.write_text(json.dumps({'candidates': [], 'ignored': []}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        result = run_cli(
            self.tmp_path,
            "extract",
            "run",
            "--codex-command",
            str(fake_codex),
            "--model",
            "gpt-5.4",
            "--effort",
            "medium",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(argv_path.read_text(encoding="utf-8"))
        argv = captured["argv"]
        self.assertEqual(captured["internal"], "1")
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.4")
        self.assertIn('model_reasoning_effort="medium"', argv)
        self.assertIn("--output-schema", argv)
        self.assertTrue(argv[argv.index("--output-schema") + 1].endswith("assets/extraction-output.schema.json"))
        self.assertIn("--output-last-message", argv)

    def test_extract_run_commits_memory_changes_to_git(self):
        entry_id = json.loads(
            run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="记住我喜欢中文").stdout
        )["id"]
        fake_codex = self.tmp_path / "fake-codex.py"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            f"out.write_text(json.dumps({{'candidates': [], 'ignored': [{{'source_id': {entry_id!r}}}]}}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        result = run_cli(self.tmp_path, "extract", "run", "--job-id", "job-git", "--codex-command", str(fake_codex))

        self.assertEqual(result.returncode, 0, result.stderr)
        log = git(self.tmp_path, "log", "--oneline", "-1")
        self.assertEqual(log.returncode, 0, log.stderr)
        self.assertIn("Memory extraction job-git: completed", log.stdout)
        status = git(self.tmp_path, "status", "--porcelain")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "")

    def test_extract_run_untracks_ignored_job_logs_before_commit(self):
        entry_id = json.loads(
            run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="记住我喜欢中文").stdout
        )["id"]
        fake_codex = self.tmp_path / "fake-codex.py"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            f"out.write_text(json.dumps({{'candidates': [], 'ignored': [{{'source_id': {entry_id!r}}}]}}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        run_cli(self.tmp_path, "extract", "run", "--job-id", "job-first", "--codex-command", str(fake_codex))

        old_log = self.tmp_path / "memory" / "system" / "extract-jobs" / "old.stderr.log"
        old_log.parent.mkdir(parents=True, exist_ok=True)
        old_log.write_text("old raw stderr\n", encoding="utf-8")
        self.assertEqual(git(self.tmp_path, "add", "-f", "system/extract-jobs/old.stderr.log").returncode, 0)
        self.assertEqual(git(self.tmp_path, "commit", "-m", "track old raw job log").returncode, 0)
        self.assertIn("system/extract-jobs/old.stderr.log", git(self.tmp_path, "ls-files").stdout)

        second_id = json.loads(
            run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="记住我喜欢直接回答").stdout
        )["id"]
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            f"out.write_text(json.dumps({{'candidates': [], 'ignored': [{{'source_id': {second_id!r}}}]}}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        result = run_cli(self.tmp_path, "extract", "run", "--job-id", "job-second", "--codex-command", str(fake_codex))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("system/extract-jobs/old.stderr.log", git(self.tmp_path, "ls-files").stdout)

    def test_extract_run_skips_when_job_lock_is_held(self):
        run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="one")
        lock_path = self.tmp_path / "memory" / "system" / "locks" / "extract-job.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            result = run_cli(self.tmp_path, "extract", "run", "--job-id", "job-lock")
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("another_extraction_job_is_running", result.stdout)
        rows = read_jsonl(self.tmp_path / "memory" / "system" / "extract-jobs.jsonl")
        self.assertEqual(rows[-1]["status"], "skipped")
        self.assertEqual(rows[-1]["phase"], "already_running")
        pending = json.loads(run_cli(self.tmp_path, "inbox", "pending", "--json").stdout)
        self.assertEqual(len(pending), 1)

    def test_extract_run_batches_entries_by_character_budget_without_splitting_entries(self):
        first = json.loads(run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="a" * 80).stdout)["id"]
        second = json.loads(run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="b" * 80).stdout)["id"]
        fake_codex = self.tmp_path / "fake-codex.py"
        calls_path = self.tmp_path / "calls.jsonl"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, re, sys\n"
            "prompt = sys.stdin.read()\n"
            f"calls = pathlib.Path({str(calls_path)!r})\n"
            "calls.write_text(calls.read_text(encoding='utf-8') + json.dumps({'chars': len(prompt), 'ids': re.findall(r'up_[A-Za-z0-9T+_:-]+', prompt)}) + '\\n' if calls.exists() else json.dumps({'chars': len(prompt), 'ids': re.findall(r'up_[A-Za-z0-9T+_:-]+', prompt)}) + '\\n', encoding='utf-8')\n"
            "ids = sorted(set(re.findall(r'up_[A-Za-z0-9T+_:-]+', prompt)))\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            "out.write_text(json.dumps({'candidates': [], 'ignored': [{'source_id': i} for i in ids]}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        result = run_cli(
            self.tmp_path,
            "extract",
            "run",
            "--codex-command",
            str(fake_codex),
            "--max-batch-chars",
            "400",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = read_jsonl(calls_path)
        self.assertEqual(len(calls), 2)
        self.assertEqual([{first}, {second}], [set(call["ids"]) for call in calls])

    def test_extract_run_marks_single_oversized_entry_failed_without_splitting(self):
        entry_id = json.loads(
            run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="x" * 500).stdout
        )["id"]
        fake_codex = self.tmp_path / "fake-codex.py"
        fake_codex.write_text("#!/usr/bin/env python3\nraise SystemExit(99)\n", encoding="utf-8")
        fake_codex.chmod(0o755)

        result = run_cli(
            self.tmp_path,
            "extract",
            "run",
            "--codex-command",
            str(fake_codex),
            "--max-batch-chars",
            "200",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        processed = read_jsonl(self.tmp_path / "memory" / "system" / "processed.jsonl")
        self.assertEqual(processed[-1]["id"], entry_id)
        self.assertEqual(processed[-1]["status"], "failed")
        self.assertEqual(processed[-1]["reason"], "entry_exceeds_max_batch_chars")

    def test_extract_failure_job_log_stores_error_summary_not_full_stderr(self):
        run_cli(self.tmp_path, "inbox", "append", "--source", "user_prompt", input_text="记住测试失败摘要")
        fake_codex = self.tmp_path / "fake-codex.py"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stderr.write('A' * 5000)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        result = run_cli(
            self.tmp_path,
            "extract",
            "run",
            "--job-id",
            "job-test",
            "--codex-command",
            str(fake_codex),
        )

        self.assertNotEqual(result.returncode, 0)
        rows = read_jsonl(self.tmp_path / "memory" / "system" / "extract-jobs.jsonl")
        failed = next(row for row in rows if row.get("phase") == "codex_failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["stderr_chars"], 5000)
        self.assertLessEqual(len(failed["stderr_head"]), 1200)
        self.assertLessEqual(len(failed["stderr_tail"]), 1200)
        self.assertNotIn("error", failed)
        self.assertEqual(rows[-1]["phase"], "git_commit")
        self.assertEqual(rows[-1]["status"], "failed")

    def test_extract_start_records_default_model_and_effort(self):
        result = run_cli(self.tmp_path, "extract", "start", "--codex-command", "codex", "--limit", "1")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("--model", payload["command"])
        self.assertEqual(payload["command"][payload["command"].index("--model") + 1], "gpt-5.4")
        self.assertIn("--effort", payload["command"])
        self.assertEqual(payload["command"][payload["command"].index("--effort") + 1], "medium")
        self.assertIn("--max-batch-chars", payload["command"])
        self.assertEqual(payload["command"][payload["command"].index("--max-batch-chars") + 1], "100000")
        self.assertIn("--timeout-sec", payload["command"])

    def test_hooks_do_not_write_flow_log_when_debug_is_disabled(self):
        memory_root = self.tmp_path / "memory"
        env = {**os.environ, "CODEX_AGENT_MEMORY_ROOT": str(memory_root)}
        env.pop("CODEX_AGENT_MEMORY_DEBUG", None)
        env.pop("CODEX_AGENT_MEMORY_EXTRACT_ON_STOP", None)
        result = subprocess.run(
            [sys.executable, str(USER_PROMPT_HOOK)],
            input=json.dumps(
                {
                    "prompt": "debug disabled",
                    "session_id": "sess-1",
                    "turn_id": "turn-1",
                    "cwd": "/tmp/example-repo",
                }
            ),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((memory_root / "system" / "hook-flow.jsonl").exists())

    def test_hooks_write_flow_log_when_debug_is_enabled(self):
        memory_root = self.tmp_path / "memory"
        env = {
            **os.environ,
            "CODEX_AGENT_MEMORY_ROOT": str(memory_root),
            "CODEX_AGENT_MEMORY_DEBUG": "1",
        }
        env.pop("CODEX_AGENT_MEMORY_EXTRACT_ON_STOP", None)
        user_prompt = subprocess.run(
            [sys.executable, str(USER_PROMPT_HOOK)],
            input=json.dumps(
                {
                    "prompt": "debug enabled",
                    "session_id": "sess-1",
                    "turn_id": "turn-1",
                    "cwd": "/tmp/example-repo",
                }
            ),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        stop = subprocess.run(
            [sys.executable, str(STOP_HOOK)],
            input=json.dumps({"hook_event_name": "Stop", "turn_id": "turn-1"}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(user_prompt.returncode, 0, user_prompt.stderr)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        rows = read_jsonl(memory_root / "system" / "hook-flow.jsonl")
        self.assertEqual([row["hook"] for row in rows], ["UserPromptSubmit", "Stop"])
        self.assertTrue(rows[0]["ts"].endswith("+08:00"))
        self.assertTrue(rows[1]["ts"].endswith("+08:00"))
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[0]["session_id"], "sess-1")
        self.assertEqual(rows[0]["turn_id"], "turn-1")
        self.assertEqual(rows[0]["workspace_key"], "example-repo")
        self.assertEqual(rows[0]["action"], "inbox_append")
        self.assertEqual(rows[0]["action_returncode"], 0)
        self.assertTrue(rows[0]["entry_id"].startswith("up_"))
        self.assertEqual(rows[1]["status"], "skipped")
        self.assertEqual(rows[1]["action"], "extract_start")
        self.assertFalse(rows[1]["extract_on_stop"])

    def test_user_prompt_hook_filters_memory_hook_injected_prompt(self):
        memory_root = self.tmp_path / "memory"
        env = {
            **os.environ,
            "CODEX_AGENT_MEMORY_ROOT": str(memory_root),
            "CODEX_AGENT_MEMORY_DEBUG": "1",
        }
        prompt = (
            "<hook_prompt hook_run_id=\"stop:1:/tmp/hooks.json\">"
            "# Codex Agent Memory Structure\nExisting canonical memory:\nInbox entries:\n"
            "Return only JSON that conforms to assets/extraction-output.schema.json."
            "</hook_prompt>"
        )

        result = subprocess.run(
            [sys.executable, str(USER_PROMPT_HOOK)],
            input=json.dumps({"prompt": prompt, "session_id": "sess-1", "turn_id": "turn-1", "cwd": "/tmp/example-repo"}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((memory_root / "inbox" / "user-prompts.jsonl").exists())
        rows = read_jsonl(memory_root / "system" / "hook-flow.jsonl")
        self.assertEqual(rows[0]["status"], "filtered")
        self.assertEqual(rows[0]["filter_reason"], "hook_prompt_injection")

    def test_hooks_skip_during_internal_extract(self):
        memory_root = self.tmp_path / "memory"
        fake_bin = self.tmp_path / "fake-bin"
        fake_bin.mkdir()
        called = self.tmp_path / "called"
        fake_cli = fake_bin / "codex-memory"
        fake_cli.write_text(
            "#!/usr/bin/env sh\n"
            f"touch {called}\n",
            encoding="utf-8",
        )
        fake_cli.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "CODEX_AGENT_MEMORY_ROOT": str(memory_root),
            "CODEX_AGENT_MEMORY_DEBUG": "1",
            "CODEX_AGENT_MEMORY_EXTRACT_ON_STOP": "1",
            "CODEX_AGENT_MEMORY_INTERNAL_EXTRACT": "1",
        }

        user_prompt = subprocess.run(
            [sys.executable, str(USER_PROMPT_HOOK)],
            input=json.dumps({"prompt": "should skip", "session_id": "sess-1", "turn_id": "turn-1", "cwd": "/tmp/example-repo"}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        stop = subprocess.run(
            [sys.executable, str(STOP_HOOK)],
            input=json.dumps({"hook_event_name": "Stop", "turn_id": "turn-1"}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(user_prompt.returncode, 0, user_prompt.stderr)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertFalse((memory_root / "inbox" / "user-prompts.jsonl").exists())
        self.assertFalse(called.exists())
        rows = read_jsonl(memory_root / "system" / "hook-flow.jsonl")
        self.assertEqual([row["hook"] for row in rows], ["UserPromptSubmit", "Stop"])
        self.assertEqual([row["status"] for row in rows], ["skipped", "skipped"])
        self.assertEqual([row["skip_reason"] for row in rows], ["internal_extract", "internal_extract"])

    def test_bin_launcher_invokes_cli_without_installing(self):
        result = run_bin(
            self.tmp_path,
            "inbox",
            "append",
            "--source",
            "user_prompt",
            input_text="direct launcher",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["id"].startswith("up_"))

    def test_install_cli_creates_executable_launcher(self):
        target_dir = self.tmp_path / "bin"
        result = subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "--target-dir",
                str(target_dir),
                "--print-path",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        installed = Path(result.stdout.strip())
        self.assertTrue(installed.exists())
        self.assertTrue(os.access(installed, os.X_OK))

        launched = subprocess.run(
            [str(installed), "inbox", "append", "--source", "user_prompt"],
            input="installed launcher",
            text=True,
            capture_output=True,
            env={**os.environ, "CODEX_AGENT_MEMORY_ROOT": str(self.tmp_path / "installed-memory")},
            check=False,
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)

    def test_install_cli_copy_mode_embeds_plugin_path(self):
        target_dir = self.tmp_path / "copy-bin"
        result = subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "--target-dir",
                str(target_dir),
                "--copy",
                "--print-path",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        installed = Path(result.stdout.strip())
        self.assertTrue(installed.exists())

        launched = subprocess.run(
            [str(installed), "inbox", "append", "--source", "user_prompt"],
            input="copy launcher",
            text=True,
            capture_output=True,
            env={**os.environ, "CODEX_AGENT_MEMORY_ROOT": str(self.tmp_path / "copy-memory")},
            check=False,
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)

    def test_apply_plan_rejects_target_files_outside_canonical_tree(self):
        plan = {
            "candidates": [
                {
                    "kind": "user_preference",
                    "target_file": "../outside.md",
                    "content": "Bad write",
                    "source_ids": ["up_1"],
                }
            ],
            "ignored": [],
        }

        result = run_cli(self.tmp_path, "plan", "apply", "--stdin", input_text=json.dumps(plan))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside canonical", result.stderr)

    def test_apply_plan_appends_allowed_bullet_and_records_sources(self):
        plan = {
            "candidates": [
                {
                    "kind": "user_preference",
                    "target_file": "canonical/user/preferences.md",
                    "content": "用户偏好架构讨论先给整体流程。",
                    "source_ids": ["up_20260620_1"],
                }
            ],
            "ignored": [
                {
                    "source_id": "up_20260620_2",
                }
            ],
        }

        result = run_cli(self.tmp_path, "plan", "apply", "--stdin", input_text=json.dumps(plan))

        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.tmp_path / "memory" / "canonical" / "user" / "preferences.md"
        text = target.read_text()
        self.assertIn("# 偏好", text)
        self.assertIn("- 用户偏好架构讨论先给整体流程。", text)
        self.assertNotIn("来源:", text)
        self.assertNotIn("原因:", text)

        processed = read_jsonl(self.tmp_path / "memory" / "system" / "processed.jsonl")
        self.assertEqual(
            {entry["id"]: entry["status"] for entry in processed},
            {
                "up_20260620_1": "processed",
                "up_20260620_2": "ignored",
            },
        )

        checkpoint = json.loads((self.tmp_path / "memory" / "system" / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["last_processed_id"], "up_20260620_2")
        self.assertEqual(checkpoint["processed_count"], 2)

    def test_apply_plan_deduplicates_and_updates_similar_bullets(self):
        target = self.tmp_path / "memory" / "canonical" / "user" / "preferences.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# 偏好\n\n- 用户偏好短回答。\n", encoding="utf-8")
        duplicate = {
            "candidates": [
                {
                    "kind": "user_preference",
                    "target_file": "canonical/user/preferences.md",
                    "content": "用户偏好短回答",
                    "source_ids": ["up_dup"],
                }
            ],
            "ignored": [],
        }
        update = {
            "candidates": [
                {
                    "kind": "user_preference",
                    "target_file": "canonical/user/preferences.md",
                    "content": "用户偏好短回答，并希望回答直接给结论。",
                    "source_ids": ["up_update"],
                }
            ],
            "ignored": [],
        }

        first = run_cli(self.tmp_path, "plan", "apply", "--stdin", input_text=json.dumps(duplicate))
        second = run_cli(self.tmp_path, "plan", "apply", "--stdin", input_text=json.dumps(update))

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
        self.assertEqual(lines, ["- 用户偏好短回答，并希望回答直接给结论。"])

    def test_apply_plan_writes_extraction_log_per_source_id(self):
        plan = {
            "candidates": [
                {
                    "kind": "user_preference",
                    "target_file": "canonical/user/preferences.md",
                    "content": "用户偏好短回答。",
                    "source_ids": ["up_1"],
                },
                {
                    "kind": "engineering_principle",
                    "target_file": "canonical/engineering/principles.md",
                    "content": "CLI 负责可靠性。",
                    "source_ids": ["up_1", "up_2"],
                },
            ],
            "ignored": [
                {
                    "source_id": "up_3",
                }
            ],
        }

        result = run_cli(self.tmp_path, "plan", "apply", "--stdin", input_text=json.dumps(plan))

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = read_jsonl(self.tmp_path / "memory" / "system" / "extraction-log.jsonl")
        by_id = {row["source_id"]: row for row in rows}
        self.assertEqual(by_id["up_1"]["memory_count"], 2)
        self.assertEqual(by_id["up_2"]["memory_count"], 1)
        self.assertEqual(by_id["up_3"]["memory_count"], 0)
        self.assertEqual(by_id["up_3"]["status"], "ignored")
        self.assertEqual(by_id["up_1"]["target_files"], ["canonical/engineering/principles.md", "canonical/user/preferences.md"])

        log_result = run_cli(self.tmp_path, "extract", "log", "--json")
        self.assertEqual(log_result.returncode, 0, log_result.stderr)
        self.assertEqual(len(json.loads(log_result.stdout)), 3)


if __name__ == "__main__":
    unittest.main()
