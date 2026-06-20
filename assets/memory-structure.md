# Codex Agent Memory Structure

This document is the memory directory contract for the Codex Agent Memory plugin. Extraction agents should use it when deciding where a candidate memory belongs.

## Root

Default root:

```text
~/.codex/codex-agent-memory
```

Override with:

```text
CODEX_AGENT_MEMORY_ROOT=/path/to/memory
```

## Directory Layout

```text
<memory-root>/
  inbox/
    user-prompts.jsonl

  canonical/
    user/
      preferences.md
      constraints.md
      profile.md

    engineering/
      principles.md
      workflows.md
      standards.md
      stack-decisions.md
      gotchas.md

    workspaces/
      <workspace-key>/
        overview.md
        principles.md
        workflows.md
        standards.md
        stack.md
        gotchas.md

    domains/
      <domain-key>/
        concepts.md
        rules.md
        decisions.md
        gotchas.md

  system/
    checkpoint.json
    processed.jsonl
    extraction-log.jsonl
    extraction-rules.md
    locks/

  tmp/
```

## Core Areas

### `inbox/`

Raw event stream. For now, the plugin records user prompts only:

```text
inbox/user-prompts.jsonl
```

Each line is a JSON object with an `id`, timestamp, session metadata, workspace hint, and raw prompt text. Treat inbox as append-only.

Important fields:

- `id`: stable raw content ID used by processing logs.
- `session_id`: local session identifier passed by the hook or caller.
- `codex_session_id`: Codex conversation/session identifier. Use this to connect raw memory entries back to richer Codex session context later.
- `turn_id`: Codex turn identifier when available.
- `workspace_key`: workspace slug hint derived from `cwd` or passed explicitly.
- `text`: raw user prompt text.

### `canonical/`

Durable memory that agents may read during normal work. Keep entries concise, stable, and reusable.

Do not store raw conversation transcripts, one-off cases, or assistant messages in canonical memory.

### `system/`

Machine state for the memory system: checkpoint, processed log, extraction rules, and locks. This is not semantic memory.

Agents should not use `system/` as ordinary task context unless debugging the memory system itself.

- `processed.jsonl`: idempotency and claim state for inbox entries.
- `extraction-log.jsonl`: append-only extraction audit log. One record per raw content ID per extraction application, including `source_id`, status, and how many canonical memories were extracted.
- `checkpoint.json`: summary progress marker.
- `extraction-rules.md`: local override for extraction rules.
- `locks/`: filesystem locks.

### `tmp/`

Scratch space for temporary files.

## Canonical Modules

### `canonical/user/`

Memory about the user as a collaborator.

- `preferences.md`: collaboration style, language, output shape, reasoning depth, and interaction preferences.
- `constraints.md`: permission boundaries, actions the user does not want automated, privacy limits, and explicit prohibitions.
- `profile.md`: stable user background that is explicitly useful and appropriate to remember.

Use this module only for memory about the user, not for project facts or general engineering lessons.

### `canonical/engineering/`

Cross-project engineering memory.

- `principles.md`: general engineering judgment and tradeoff principles.
- `workflows.md`: reusable development, debugging, review, release, and operational workflows.
- `standards.md`: cross-project testing, documentation, quality, and PR standards.
- `stack-decisions.md`: general technology selection preferences and stack decision criteria.
- `gotchas.md`: reusable engineering pitfalls that are not specific to one workspace.

Use this module when the memory can apply across multiple projects or future engineering tasks.

### `canonical/workspaces/<workspace-key>/`

Memory that is specific to one concrete project, repository, or workspace.

- `overview.md`: what the workspace is and how it is organized.
- `principles.md`: workspace-specific architecture or product principles.
- `workflows.md`: workspace-specific commands, tests, release procedures, and recurring tasks.
- `standards.md`: workspace-specific style, review, acceptance, and verification standards.
- `stack.md`: actual technology stack and important local dependencies.
- `gotchas.md`: workspace-specific caveats and failure patterns.

Use this module only when the memory would be misleading outside that workspace.

### `canonical/domains/<domain-key>/`

Long-term subject-matter memory that is neither about the user nor tied to one workspace.

- `concepts.md`: stable concepts, terms, and mental models.
- `rules.md`: domain rules, policies, and constraints.
- `decisions.md`: durable design decisions and rationale.
- `gotchas.md`: domain-specific caveats and recurring mistakes.

Use this module for topics such as `agent-memory`, `codex-plugins`, or another domain that may span multiple workspaces.

## Key Format

`workspace-key` and `domain-key` must be slugs:

```text
lowercase letters, digits, and hyphens only
```

Rules:

- Convert spaces, underscores, punctuation, and path separators to `-`.
- Collapse repeated hyphens.
- Trim leading and trailing hyphens.
- Prefer stable repo or domain names.
- Do not use display names, absolute paths, mixed case, or non-ASCII characters.

Examples:

```text
codex-memory
agent-memory
frontend-platform
payments-service
```

## Routing Rules

Route candidate memory by scope:

```text
How the user wants to be served
  -> canonical/user/

Cross-project engineering principle, workflow, standard, stack choice, or gotcha
  -> canonical/engineering/

Only true for one concrete repo or workspace
  -> canonical/workspaces/<workspace-key>/

Long-term domain knowledge that is not user-specific or workspace-specific
  -> canonical/domains/<domain-key>/
```

If unsure whether a memory is workspace-specific or generally reusable, prefer the narrower workspace path unless the source explicitly frames it as a general rule.

## Canonical Entry Format

Use short Markdown bullets:

```md
- CLI should handle deterministic reliability concerns; agents should handle semantic extraction decisions.
  Source: up_20260620...
  Confidence: high
```

Guidelines:

- One durable idea per bullet.
- Keep content concise.
- Include source IDs.
- Include confidence when available.
- Avoid long summaries, raw transcripts, and narrow one-off case details.
