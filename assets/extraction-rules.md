# Codex Agent Memory Extraction Rules

Extract only durable memory with high future reuse value.

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
      "content": "User preference written as one durable sentence.",
      "source_ids": ["up_..."],
      "confidence": "high",
      "reason": "Why this has future reuse value."
    }
  ],
  "ignored": [
    {
      "source_id": "up_...",
      "reason": "Too specific, temporary, or not suitable for durable memory."
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
