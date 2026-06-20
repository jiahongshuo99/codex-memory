# Codex Agent Memory

Local Codex plugin and CLI for a text-first memory system.

All source code lives inside this plugin. The CLI is plain Python plus a small shell launcher, so there is no compile step.

## Shape

```text
~/.codex/codex-agent-memory/
  index.md
  inbox/user-prompts.jsonl
  canonical/user/
  canonical/workspaces/
  system/checkpoint.json
  system/processed.jsonl
```

## Flow

1. `UserPromptSubmit` hook calls the CLI.
2. CLI appends the user prompt to JSONL inbox with a stable ID and metadata.
3. Hook injects a short memory protocol on every user prompt.
4. Codex decides whether to inspect `index.md` and relevant canonical memory.
5. A manual or scheduled extraction command processes pending inbox entries.
6. The CLI calls Codex CLI for semantic extraction, validates the returned plan, applies safe markdown bullets, and updates `processed.jsonl` plus `checkpoint.json`.

## CLI

There are two ways to run the CLI.

Run it directly from the plugin:

```bash
./bin/codex-memory inbox pending --json
```

Install a launcher onto your PATH:

```bash
python3 scripts/install_cli.py
```

By default this creates a symlink:

```text
~/.local/bin/codex-memory -> <plugin>/bin/codex-memory
```

If `~/.local/bin` is not on PATH, add this to your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

You can install somewhere else:

```bash
python3 scripts/install_cli.py --target-dir /usr/local/bin
```

Use `--copy` if you want a copied launcher instead of a symlink. The copied launcher still expects the plugin source tree to remain available if it was copied without its sibling `scripts/` directory, so symlink install is the recommended mode.

Append a prompt:

```bash
codex-memory inbox append --source user_prompt --text-stdin
```

List unprocessed inbox entries:

```bash
codex-memory inbox pending --json
```

Preview the extraction prompt:

```bash
codex-memory extract run --dry-run --limit 20
```

Apply an extraction plan:

```bash
codex-memory plan apply --stdin < plan.json
```

Run extraction through Codex CLI:

```bash
codex-memory extract run --limit 20
```

The extraction command shells out to:

```bash
codex exec --full-auto --skip-git-repo-check -
```

Use `--codex-command /path/to/codex` if `codex` is not on PATH.

## Memory Root

Default:

```text
~/.codex/codex-agent-memory
```

Override:

```bash
CODEX_AGENT_MEMORY_ROOT=/path/to/memory codex-memory inbox pending --json
```

## Hook

The plugin includes `hooks/hooks.json` with one `UserPromptSubmit` hook. It does not use `SessionStart`, `PreCompact`, or `Stop`.

The hook calls:

```bash
python3 scripts/user_prompt_submit_hook.py
```

The hook script calls the plugin-local CLI source directly, so the hook does not require `codex-memory` to be installed on PATH. Installing the launcher is for humans, scheduled jobs, and external automation.

## Suggested Automation

Run extraction periodically with a scheduler:

```bash
codex-memory extract run --limit 50
```

For a dry run that only shows the prompt sent to Codex CLI:

```bash
codex-memory extract run --dry-run --limit 50
```

## Notes

- Inbox is JSONL, not markdown.
- The hook does not retrieve memory.
- Assistant messages are not recorded.
- Canonical memory should contain durable user preferences, user boundaries, and reusable engineering standards, not one-off cases.
