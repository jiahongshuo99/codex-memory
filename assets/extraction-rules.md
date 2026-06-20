# Codex Agent Memory Extraction Rules

Extract only durable memory with high future reuse value.

所有写入 `canonical/` 的记忆内容必须使用中文；如果源内容是英文，也要提炼成自然中文。

Before choosing a `target_file`, follow `assets/memory-structure.md`. Canonical memory is split by root and scope:

- global `canonical/user/`
- global `canonical/engineering/`
- project `.codex/codex-agent-memory/canonical/`
- global `canonical/domains/<domain-key>/`

## Generalization Preference

Before writing a candidate, decide whether the underlying idea is transferable beyond the current project.

- If a design or implementation principle remains true outside the current workspace, generalize it and route it to `canonical/engineering/` or `canonical/domains/`.
- Treat the current project as an example, not as part of the memory, unless the project context is required for correctness.
- Avoid narrow wording such as "在 <workspace> 中", "针对这个项目", or "这个情况" unless the memory would be wrong without that scope.
- For schema definitions, protocol contracts, configuration contracts, output formats, API contracts, and similar consistency concerns, prefer `canonical/engineering/standards.md`.
- Do not write the same idea to both project and engineering memory by default. Choose the broadest accurate scope.
- For `workspace_*` candidate kinds, write `target_file` under the project memory root using paths such as `canonical/workflows.md`, `canonical/standards.md`, `canonical/stack.md`, or `canonical/gotchas.md`.
- Never output a project path that includes a workspace key segment.

## Assistant Message Memory

When `type` is `assistant_message` and `phase` is `final_answer`, treat the entry as lower authority than a user prompt.

Keep only:

- Confirmed conclusions.
- Completed actions and verified results.
- Accepted decisions that are explicitly stated as accepted or already applied.
- Stable facts about the current system that the assistant actually verified.

Do not keep:

- Proposed plans, possible designs, implementation suggestions, or alternatives.
- Speculation, caveats, or recommendations that the user has not accepted.
- Assistant-inferred user preferences.
- Mere summaries of what the assistant intends to do next.

If an assistant final answer mixes completed facts with suggestions, extract only the completed facts. If no durable confirmed conclusion remains, ignore the entry.

## User Memory

Keep:

- Explicit collaboration preferences, such as language, length, format, or desired reasoning style.
- Explicit boundaries, such as permission preferences or actions the user does not want automated.
- Stable personal or work background only when the user clearly wants it remembered or it is repeatedly useful.

Do not keep:

- Temporary moods.
- Weak identity or preference inferences.
- Sensitive personal information unless the user explicitly asks to remember it.

## Engineering Memory

Keep:

- Reusable engineering judgment standards.
- Cross-project engineering workflows and standards.
- Testing, review, release, and style standards.
- Gotchas that are general enough to prevent future repeated mistakes.

Do not keep:

- One-off bug fixes.
- Narrow business facts.
- Specific implementation details from a temporary case.
- Current code state unless it is a stable project convention.

## Project Memory

Keep in project memory only when the fact depends on the concrete repo:

- Local commands, scripts, test workflows, release procedures, or repo-specific automation.
- Project architecture, stack, directory layout, or conventions that would be misleading elsewhere.
- Project-specific caveats, known failure modes, and acceptance criteria.

Project memory is routed through `workspace_*` candidate kinds. The extractor uses the source entry `cwd` to resolve the repo root, then writes under `<repo-root>/.codex/codex-agent-memory/`.

Project `README.md` has higher priority than project memory:

- Before creating a `workspace_*` candidate, check the project README content provided in the prompt.
- Do not create project memory that duplicates information already documented in README.md.
- Do not create project memory that conflicts with README.md; README.md is the higher-priority project fact source.
- Use project memory only for durable project facts, workflows, gotchas, or conventions that README.md does not already cover.

## Domain Memory

Keep:

- Durable concepts, rules, decisions, and gotchas for a subject area that is not tied to one workspace.
- Product or architecture decisions that may apply across multiple future tasks in the same domain.

Do not keep:

- Topic notes that only explain the current conversation.
- Domain claims that are weak inferences rather than explicit or well-supported decisions.

## Output Contract

Return only JSON that conforms to `assets/extraction-output.schema.json`.
