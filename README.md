# Codex Agent Memory

Local Codex plugin and CLI for a text-first memory system.

All source code lives inside this plugin. The CLI is plain Python plus a small shell launcher, so there is no compile step.

## Shape

The full memory directory contract lives in [assets/memory-structure.md](assets/memory-structure.md). Extraction prompts include that document so memory extraction agents see the same structure and routing rules.

```text
~/.codex/codex-agent-memory/

  inbox/events/YYYY-MM-DD.jsonl

  canonical/
    user/
    engineering/
    workspaces/<workspace-key>/
    domains/<domain-key>/

  system/checkpoint.json
  system/processed.jsonl
  system/extraction-log.jsonl
  system/extraction-rules.md
  system/locks/

  tmp/
```

The memory root is initialized as a Git repository automatically. Transient files under `tmp/` and
`system/locks/` are ignored.

## Flow

1. `UserPromptSubmit` hook calls the CLI.
2. CLI appends the user prompt to the daily JSONL inbox event stream with a stable ID and metadata.
3. Hook injects a short memory protocol on every user prompt.
4. Codex reads relevant canonical memory first when the task involves an existing workspace, repeated workflow, prior decision, user preference, constraint, known issue, or durable engineering/domain fact.
5. A manual, scheduled, or Stop-hook-started background extraction command processes pending inbox entries.
6. The CLI calls Codex CLI for semantic extraction, validates the returned plan, applies safe markdown bullets, and updates `processed.jsonl` plus `checkpoint.json`.

The Codex `Stop` hook records assistant `final_answer` events into the same daily inbox event stream. Optional extraction can also be started from the `Stop` hook. It is disabled by default and must be enabled with an environment variable. When enabled, the hook starts a background extraction job and returns immediately.

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
codex-memory inbox append --type user_prompt --text-stdin
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

Run extraction through Codex CLI synchronously:

```bash
codex-memory extract run --limit 20
```

`extract run` processes claimed inbox entries in batches. Each Codex extraction call defaults to at most
100,000 characters of inbox entry JSON; if pending content exceeds that budget, the run continues in
additional batches. A single entry is never split or truncated. If one entry alone exceeds the batch
budget, it is marked `failed` with reason `entry_exceeds_max_batch_chars`.

Extraction is serialized with `system/locks/extract-job.lock`. If another extraction job is already
running, a new run exits with `skipped` and does not claim inbox entries. At the end of each real
extraction run, the memory root is committed with Git so memory changes can be reviewed over time.

Start extraction asynchronously and return immediately:

```bash
codex-memory extract start --limit 20
```

View the extraction job board:

```bash
codex-memory extract jobs
```

By default, `extract jobs` shows only active jobs (`started` or `running`). Use `--all` to show every
latest job, `--status failed` to filter by status, and `--limit N` to cap the displayed result count.
All display modes also support `--json`.

Job state is recorded in `system/extract-jobs.jsonl`. Background job stdout/stderr logs are stored under `system/extract-jobs/`.
Job events include phase-level records such as `batch_claimed`, `codex_start`, `codex_finished`,
`plan_apply_start`, and `batch_done` to make slow or timed-out extraction runs diagnosable. Failed
job records store summarized fields such as `error_code`, `stderr_head`, `stderr_tail`, and character
counts instead of embedding full prompts or full stderr in `extract-jobs.jsonl`.

The extraction command shells out to:

```bash
codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --model gpt-5.4 \
  -c 'model_reasoning_effort="medium"' \
  --output-schema <plugin>/assets/extraction-output.schema.json \
  --output-last-message <tmp-output.json> \
  -
```

The extraction output contract is defined once in `assets/extraction-output.schema.json`; prompts and
rules reference that schema instead of duplicating the response shape.

The default extraction model is `gpt-5.4` with `medium` reasoning effort. Override with
`--model`, `--effort`, `CODEX_AGENT_MEMORY_EXTRACT_MODEL`, or `CODEX_AGENT_MEMORY_EXTRACT_EFFORT`.
The default timeout for one Codex extraction call is 900 seconds; override with `--timeout-sec` or
`CODEX_AGENT_MEMORY_EXTRACT_TIMEOUT_SEC`.
Use `--codex-command /path/to/codex` if `codex` is not on PATH.

Extraction sets `CODEX_AGENT_MEMORY_INTERNAL_EXTRACT=1` for its child `codex exec` process. The
plugin hooks skip themselves when this variable is present, which prevents extraction sessions from
recording their own prompts or starting another extraction loop.

Claim a batch without extracting:

```bash
codex-memory extract claim --limit 20
```

`extract run` uses the same claim path internally before it calls Codex CLI.
Claimed entries first receive `processing`. Normal terminal states are `processed` or `ignored`;
extraction failures and oversize entries are marked `failed` so they do not remain stuck in
`processing`.

On macOS, prefer `launchd` for periodic extraction. A user LaunchAgent can run:

```bash
codex-memory extract start --limit 50
```

Use `StartInterval` for simple intervals, or `StartCalendarInterval` for calendar-style schedules.

Read the extraction audit log:

```bash
codex-memory extract log --json
```

The log lives at `system/extraction-log.jsonl`. It records one row per raw source id for each applied extraction plan, including how many canonical memories were extracted from that raw content.

## Memory Root

Default:

```text
~/.codex/codex-agent-memory
```

Override:

```bash
CODEX_AGENT_MEMORY_ROOT=/path/to/memory codex-memory inbox pending --json
```

## Hooks

The plugin includes:

- `UserPromptSubmit`: always records the user prompt into inbox and injects the short memory protocol.
- `Stop`: optionally starts asynchronous extraction at the end of each turn.

`UserPromptSubmit` calls:

```bash
python3 scripts/user_prompt_submit_hook.py
```

`Stop` calls:

```bash
python3 scripts/stop_hook.py
```

The hook scripts can call the plugin-local CLI source directly, so hooks do not require `codex-memory` to be installed on PATH. Installing the launcher is for humans, scheduled jobs, and external automation.

### Enable Stop Extraction

Default: disabled.

Enable asynchronous extraction at turn end:

```bash
export CODEX_AGENT_MEMORY_EXTRACT_ON_STOP=1
```

Optional settings:

```bash
export CODEX_AGENT_MEMORY_EXTRACT_LIMIT=50
export CODEX_AGENT_MEMORY_CLI=/path/to/codex-memory
export CODEX_AGENT_MEMORY_CODEX_COMMAND=/path/to/codex
```

The Stop hook starts extraction in the background and returns immediately. The background job keeps running until extraction finishes. The CLI only holds the extraction claim lock while choosing and marking a batch; the slower Codex CLI extraction runs outside that lock.

## Concurrency

Extraction uses a short critical section:

```text
lock system/locks/extract-claim.lock
  read pending inbox entries
  append processing records to system/processed.jsonl
unlock
run Codex CLI extraction outside the lock
apply the validated plan
append processed/ignored records
update checkpoint
```

`pending` excludes any inbox item that already has a record in `processed.jsonl`, including `processing`, `processed`, or `ignored`. This means concurrent Stop hooks or scheduled jobs cannot claim the same raw inbox item.

The lock is intentionally narrow: it covers only batch claiming, not semantic extraction or markdown writing.

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
- Canonical memory is split into `user`, `engineering`, `workspaces`, and `domains`; see [assets/memory-structure.md](assets/memory-structure.md).
- Canonical memory should contain durable user preferences, user boundaries, reusable engineering standards, workspace-specific facts, or domain knowledge, not one-off cases.
