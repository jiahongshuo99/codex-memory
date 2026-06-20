---
name: codex-agent-memory
description: Use before code search or external lookup when a task involves an existing workspace, repeated workflow, prior decision, user preference, user constraint, known issue, or durable engineering/domain memory stored by the Codex Agent Memory plugin.
---

# Codex Agent Memory

This skill uses a local, text-first memory store managed by the `codex-memory` CLI.

The full memory directory contract is in `assets/memory-structure.md` in this plugin. Use that contract when deciding whether a memory belongs under global `canonical/user`, global `canonical/engineering`, project `.codex/codex-agent-memory/canonical`, or global `canonical/domains/<domain-key>`.

## Memory Root

Default global root:

```text
~/.codex/codex-agent-memory
```

Override with:

```text
CODEX_AGENT_MEMORY_ROOT=/path/to/memory
```

Project memory root:

```text
<repo-root>/.codex/codex-agent-memory
```

For monorepos, read one project memory root at the repo root.

## Memory-First Rule

If there is even a modest chance that durable memory could answer or narrow the task, read relevant canonical memory before code search, broad repository exploration, or external lookup.

You must check memory first when:

- The task is inside a workspace with a `codex-agent-memory workspace-key` marker.
- The user asks about an existing project, CLI, command, workflow, test, release process, hook, configuration, or recurring debugging path.
- The user asks about prior preferences, constraints, decisions, or "what we decided before".
- The user asks Codex to remember, apply, or check a preference.
- The task resembles a known issue, repeated engineering standard, or domain rule.
- The current request is ambiguous and stored preferences or project facts could clarify the answer.

Do not start with `rg`, broad file listing, broad README exploration, web search, or source inspection when one of these conditions applies. For project-specific facts, a targeted read of the project README.md is allowed and has priority over project memory.

## Read Flow

1. Identify the workspace key from the prompt, cwd, or project `AGENTS.md` marker.
2. For project-specific facts, read the project README.md before project memory. Project README.md has higher priority than project memory.
3. Read only the likely relevant canonical files, usually 1 to 3 files:
   - `<repo-root>/.codex/codex-agent-memory/canonical/` for project commands, workflows, stack, standards, and known issues not already covered by README.md.
   - `canonical/user/` for user preferences, constraints, and stable profile.
   - `canonical/engineering/` for reusable engineering standards and workflows.
   - `canonical/domains/` for durable domain facts and rules.
4. If README.md or memory has a plausible answer, use it as the starting point and perform only the smallest necessary verification against the source of truth.
5. If README.md and memory do not answer the task, proceed with normal exploration.
6. Current user instructions and current source-of-truth data always override stored memory.
7. If project memory conflicts with README.md, follow README.md and mention the conflict when it affects the answer.

## Verification Policy

Memory is a cache, not an authority over mutable facts.

- User preferences and explicit constraints usually do not need verification unless the current prompt conflicts.
- Project commands, CLI flags, hooks, configuration, and workflows should be verified with a targeted read or narrow search, not broad rediscovery.
- Current code behavior should be verified against the smallest relevant code or config surface.
- External facts that may change, such as prices, laws, package versions, API availability, or schedules, require current source verification.

## Red Flags

These thoughts mean you are about to skip memory incorrectly:

- "I can just check the repo quickly."
- "The answer is probably somewhere in the repo."
- "This is a simple CLI/workflow question."
- "I need to inspect files before reading memory."
- "Memory is only background context."
- "I remember the project well enough."

For those cases, read project README.md first when the question is project-specific, then read the relevant canonical memory and verify narrowly.

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

If `CODEX_AGENT_MEMORY_EXTRACT_ON_STOP=1`, the Stop hook also runs synchronous extraction at turn end. This is off by default.

Extraction uses a short claim lock under `system/locks/extract-claim.lock`. The lock is held only while marking a batch as `processing`; Codex CLI extraction runs outside the lock. Entries marked `processing`, `processed`, or `ignored` are not returned by pending queries.

Project-specific extraction writes to `<repo-root>/.codex/codex-agent-memory/canonical/` and stages changed project memory files with `git add`. It does not commit project repository changes.

Do not write project memory that duplicates or conflicts with README.md. During extraction, README.md is treated as the higher-priority project source; project memory should only fill durable gaps that README.md does not already cover.

## What Belongs In Canonical Memory

Keep:

- Explicit user collaboration preferences.
- Explicit user boundaries and permission preferences.
- Stable user background that is clearly useful.
- Reusable cross-project engineering principles, workflows, standards, stack decisions, and gotchas.
- Project-specific facts and workflows under the project memory root.
- Durable domain concepts, rules, decisions, and gotchas under the matching domain key.

Do not keep:

- One-off cases.
- Narrow business facts.
- Temporary task details.
- Assistant messages.
- Weak inferences.
- Sensitive personal information unless the user explicitly asks to remember it.
