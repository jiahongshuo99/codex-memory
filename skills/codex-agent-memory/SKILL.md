---
name: codex-agent-memory
description: Use when a task may benefit from durable user preferences, user constraints, or reusable engineering standards stored by the Codex Agent Memory plugin.
---

# Codex Agent Memory

This skill uses a local, text-first memory store managed by the `codex-memory` CLI.

## Memory Root

Default root:

```text
~/.codex/codex-agent-memory
```

Override with:

```text
CODEX_AGENT_MEMORY_ROOT=/path/to/memory
```

## When To Read Memory

Read memory only when it may help the current task:

- The user asks about prior preferences, constraints, or decisions.
- The task involves an existing project or repeated engineering workflow.
- The user asks Codex to remember, apply, or check a preference.
- The current request is ambiguous and stored preferences could clarify style or process.

## Read Flow

1. Inspect `index.md` under the memory root.
2. Read only relevant files under `canonical/`.
3. Treat memory as background context.
4. Current user instructions always override stored memory.
5. If memory conflicts with the current prompt, follow the current prompt and mention the conflict if it matters.

## Write Flow

Do not edit canonical memory files directly unless the user explicitly asks for a manual memory edit.

Normal writes happen through:

```bash
codex-memory inbox append
codex-memory extract run
codex-memory plan apply
```

If `codex-memory` is not installed, use the plugin-local launcher:

```bash
./bin/codex-memory inbox pending --json
```

The UserPromptSubmit hook records user prompts into the JSONL inbox. A separate automation or manual command runs extraction later.

## What Belongs In Canonical Memory

Keep:

- Explicit user collaboration preferences.
- Explicit user boundaries and permission preferences.
- Stable user background that is clearly useful.
- Reusable engineering principles, workflows, standards, and gotchas.

Do not keep:

- One-off cases.
- Narrow business facts.
- Temporary task details.
- Assistant messages.
- Weak inferences.
- Sensitive personal information unless the user explicitly asks to remember it.
