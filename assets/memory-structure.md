# Codex Agent Memory Structure

This document is the memory directory contract for the Codex Agent Memory plugin. Extraction agents should use it when deciding where a candidate memory belongs.

## Roots

Global memory root:

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

For monorepos, the repo root owns one project memory root for the whole repository. Do not create package-level memory roots inside subdirectories.

## Global Directory Layout

```text
<memory-root>/
  inbox/
    events/
      YYYY-MM-DD.jsonl

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

## Project Directory Layout

```text
<repo-root>/
  AGENTS.md
  .codex/
    codex-agent-memory/
      canonical/
        overview.md
        principles.md
        workflows.md
        standards.md
        stack.md
        gotchas.md
```

Projects that use this plugin must document the project memory rule in `AGENTS.md`: project-specific memory lives in `.codex/codex-agent-memory/`, agents should read it before rediscovering project facts, and machines without the plugin can use `git@github.com:jiahongshuo99/codex-memory.git`.

Project canonical memory should be tracked by the project repository. The extractor stages changed project memory files with `git add`, but it must not commit project repository changes.

## Core Areas

### Global `inbox/`

Raw event stream. The plugin records user prompts and assistant final answers in daily JSONL files:

```text
inbox/events/YYYY-MM-DD.jsonl
```

Each line is a JSON object with an `id`, timestamp, type, optional phase, session metadata, workspace hint, cwd, and raw text. Treat inbox as append-only.

Important fields:

- `id`: stable raw content ID used by processing logs.
- `type`: event type, such as `user_prompt` or `assistant_message`.
- `session_id`: local session identifier passed by the hook or caller.
- `codex_session_id`: Codex conversation/session identifier.
- `turn_id`: Codex turn identifier when available.
- `cwd`: original working directory for routing project-specific memory.
- `workspace_key`: readable repo or project slug derived from the repo root or `AGENTS.md`.
- `text`: raw user prompt text.
- `phase`: assistant message phase when `type` is `assistant_message`; only `final_answer` is collected.

Assistant final answers are lower authority than user prompts. They may help extract confirmed outcomes, but they must not be treated as user preferences or accepted decisions unless the surrounding user prompt or final answer makes that explicit.

### `canonical/`

Durable memory that agents may read during normal work. Keep entries concise, stable, and reusable.

Do not store raw conversation transcripts, one-off cases, or raw assistant messages in canonical memory.

### Global `system/`

Machine state for the memory system: checkpoint, processed log, extraction rules, and locks. This is not semantic memory.

Agents should not use `system/` as ordinary task context unless debugging the memory system itself.

- `processed.jsonl`: idempotency and claim state for inbox entries.
- `extraction-log.jsonl`: append-only extraction audit log.
- `checkpoint.json`: summary progress marker.
- `extraction-rules.md`: local override for extraction rules.
- `locks/`: filesystem locks.

## Canonical Modules

### Global `canonical/user/`

Memory about the user as a collaborator.

- `preferences.md`: collaboration style, language, output shape, reasoning depth, and interaction preferences.
- `constraints.md`: permission boundaries, actions the user does not want automated, privacy limits, and explicit prohibitions.
- `profile.md`: stable user background that is explicitly useful and appropriate to remember.

Use this module only for memory about the user, not for project facts or general engineering lessons.

### Global `canonical/engineering/`

Cross-project engineering memory.

- `principles.md`: general engineering judgment and tradeoff principles.
- `workflows.md`: reusable development, debugging, review, release, and operational workflows.
- `standards.md`: cross-project testing, documentation, quality, and PR standards.
- `stack-decisions.md`: general technology selection preferences and stack decision criteria.
- `gotchas.md`: reusable engineering pitfalls that are not specific to one project.

Use this module when the memory can apply across multiple projects or future engineering tasks.

### Project `.codex/codex-agent-memory/canonical/`

Memory that is specific to one concrete repository or project.

- `overview.md`: what the project is and how it is organized.
- `principles.md`: project-specific architecture or product principles.
- `workflows.md`: project-specific commands, tests, release procedures, and recurring tasks.
- `standards.md`: project-specific style, review, acceptance, and verification standards.
- `stack.md`: actual technology stack and important local dependencies.
- `gotchas.md`: project-specific caveats and failure patterns.

Use this module only when the memory would be misleading outside that project.

For extraction output, workspace candidate `target_file` values are relative to the project memory root and must still start with `canonical/`, for example:

```text
canonical/workflows.md
canonical/standards.md
```

Do not include the project key in the target path.

### Global `canonical/domains/<domain-key>/`

Long-term subject-matter memory that is neither about the user nor tied to one project.

- `concepts.md`: stable concepts, terms, and mental models.
- `rules.md`: domain rules, policies, and constraints.
- `decisions.md`: durable design decisions and rationale.
- `gotchas.md`: domain-specific caveats and recurring mistakes.

Use this module for topics such as `agent-memory`, `codex-plugins`, or another domain that may span multiple projects.

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
  -> global canonical/user/

Cross-project engineering principle, workflow, standard, stack choice, or gotcha
  -> global canonical/engineering/

Depends on one concrete repo or project, such as local paths, commands, release flow, or stack details
  -> project .codex/codex-agent-memory/canonical/

Long-term domain knowledge that is not user-specific or project-specific
  -> global canonical/domains/<domain-key>/
```

Before choosing project memory, test whether the idea remains true outside the current project. If it does, route it to the broadest accurate scope: global `canonical/engineering/` for reusable engineering practice, or global `canonical/domains/<domain-key>/` for subject-matter knowledge. Use project memory only when removing the project context would make the memory misleading.

## Canonical Entry Format

Use short Markdown bullets:

```md
- CLI 负责确定性的可靠性逻辑，agent 负责语义提取判断。
```

Guidelines:

- One durable idea per bullet.
- Keep content concise.
- 不要在 canonical 记忆中写来源、原因、置信度等元数据；这些属于 system 日志。
- Avoid long summaries, raw transcripts, and narrow one-off case details.
