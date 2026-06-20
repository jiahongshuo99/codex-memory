# Codex Agent Memory Extraction Rules

Extract only durable memory with high future reuse value.

所有写入 `canonical/` 的记忆内容、`reason` 和说明性字段必须使用中文；如果源内容是英文，也要提炼成自然中文。

Before choosing a `target_file`, follow `assets/memory-structure.md`. Canonical memory is split into four modules:

- `canonical/user/`
- `canonical/engineering/`
- `canonical/workspaces/<workspace-key>/`
- `canonical/domains/<domain-key>/`

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
- Project or workspace workflows that are expected to apply repeatedly within that workspace.
- Testing, review, release, and style standards.
- Gotchas that are general enough to prevent future repeated mistakes.

Do not keep:

- One-off bug fixes.
- Narrow business facts.
- Specific implementation details from a temporary case.
- Current code state unless it is a stable project convention.

## Domain Memory

Keep:

- Durable concepts, rules, decisions, and gotchas for a subject area that is not tied to one workspace.
- Product or architecture decisions that may apply across multiple future tasks in the same domain.

Do not keep:

- Topic notes that only explain the current conversation.
- Domain claims that are weak inferences rather than explicit or well-supported decisions.

## Output Schema

Return only JSON:

```json
{
  "candidates": [
    {
      "kind": "user_preference",
      "target_file": "canonical/user/preferences.md",
      "operation": "append_bullet",
      "content": "用一句中文写成的稳定用户偏好。",
      "source_ids": ["up_..."],
      "confidence": "high",
      "reason": "说明这条记忆为什么具有长期复用价值。"
    }
  ],
  "ignored": [
    {
      "source_id": "up_...",
      "reason": "过于具体、临时，或不适合作为长期记忆。"
    }
  ]
}
```

Allowed `kind` values:

- `user_preference`
- `user_constraint`
- `user_profile`
- `engineering_principle`
- `engineering_workflow`
- `engineering_standard`
- `engineering_gotcha`
- `engineering_stack_decision`
- `workspace_overview`
- `workspace_principle`
- `workspace_workflow`
- `workspace_standard`
- `workspace_stack`
- `workspace_gotcha`
- `domain_concept`
- `domain_rule`
- `domain_decision`
- `domain_gotcha`
