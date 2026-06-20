import json
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


class CodexMemoryCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_inbox_append_writes_user_prompt_jsonl_with_stable_metadata(self):
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
            "/tmp/example-repo",
            input_text="记住我喜欢短回答\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["id"].startswith("up_"))
        self.assertIn("Memory root:", payload["protocol"])

        inbox_file = self.tmp_path / "memory" / "inbox" / "user-prompts.jsonl"
        entries = read_jsonl(inbox_file)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], payload["id"])
        self.assertEqual(entries[0]["source"], "user_prompt")
        self.assertEqual(entries[0]["session_id"], "sess-1")
        self.assertEqual(entries[0]["codex_session_id"], "sess-1")
        self.assertEqual(entries[0]["turn_id"], "turn-1")
        self.assertEqual(entries[0]["cwd"], "/tmp/example-repo")
        self.assertEqual(entries[0]["workspace_key"], "example-repo")
        self.assertEqual(entries[0]["text"], "记住我喜欢短回答")

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

    def test_apply_plan_accepts_domain_memory_kind(self):
        plan = {
            "candidates": [
                {
                    "kind": "domain_decision",
                    "target_file": "canonical/domains/agent-memory/decisions.md",
                    "operation": "append_bullet",
                    "content": "Canonical memory is split into user, engineering, workspaces, and domains.",
                    "source_ids": ["up_domain_1"],
                    "confidence": "high",
                    "reason": "explicit structure decision",
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

    def test_stop_hook_runs_sync_extraction_when_enabled(self):
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
        self.assertIn("extract run --limit 7", log_path.read_text())

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
                    "operation": "append_bullet",
                    "content": "Bad write",
                    "source_ids": ["up_1"],
                    "confidence": "high",
                    "reason": "test",
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
                    "operation": "append_bullet",
                    "content": "用户偏好架构讨论先给整体流程。",
                    "source_ids": ["up_20260620_1"],
                    "confidence": "high",
                    "reason": "explicit preference",
                }
            ],
            "ignored": [
                {
                    "source_id": "up_20260620_2",
                    "reason": "too specific",
                }
            ],
        }

        result = run_cli(self.tmp_path, "plan", "apply", "--stdin", input_text=json.dumps(plan))

        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.tmp_path / "memory" / "canonical" / "user" / "preferences.md"
        text = target.read_text()
        self.assertIn("- 用户偏好架构讨论先给整体流程。", text)
        self.assertIn("Source: up_20260620_1", text)

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

    def test_apply_plan_writes_extraction_log_per_source_id(self):
        plan = {
            "candidates": [
                {
                    "kind": "user_preference",
                    "target_file": "canonical/user/preferences.md",
                    "operation": "append_bullet",
                    "content": "用户偏好短回答。",
                    "source_ids": ["up_1"],
                    "confidence": "high",
                    "reason": "explicit",
                },
                {
                    "kind": "engineering_principle",
                    "target_file": "canonical/engineering/principles.md",
                    "operation": "append_bullet",
                    "content": "CLI 负责可靠性。",
                    "source_ids": ["up_1", "up_2"],
                    "confidence": "high",
                    "reason": "explicit",
                },
            ],
            "ignored": [
                {
                    "source_id": "up_3",
                    "reason": "too specific",
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
