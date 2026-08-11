---
name: delegate-work
description: Delegate one independent, context-heavy task to an isolated asynchronous analyst or reviewer. Use when the parent can continue without the result and the task has one concrete deliverable. Do not use for simple questions, tightly coupled next steps, external side effects, or broad open-ended work.
---

# Delegate Work

Delegate only work that is independent enough to finish in an isolated context.

## Scope the task

- Give one concrete goal and one deliverable.
- Provide observable acceptance criteria.
- Pass only the workspace paths and context required for the task.
- State explicit non-goals in `constraints`.
- Never copy the full conversation into `context_summary`.
- Split unrelated deliverables into separate delegations.

Do not delegate requests such as "investigate everything", "fix all issues", or
"continue helping with this project". Narrow them first.

## Select a profile

- Use `analyst` for data inspection, computation, experiments, and generated
  artifacts. It can use the session Code Interpreter.
- Use `reviewer` for an independent read-only review. It cannot execute code or
  modify source files.

## Select task complexity

- Use `low` for narrow extraction, classification, or simple checks.
- Use `medium` for normal analysis and review work.
- Use `high` only for genuinely difficult reasoning or broad synthesis.
- Omit `task_complexity` when the delegated agent should inherit the parent
  model unchanged.

## Execute

Call `delegate_task` once. It returns an acceptance receipt immediately.
Continue the foreground conversation without waiting for completion.
Never expose or invent internal job identifiers; they are orchestration metadata
owned by the activity UI and backend.

Use `get_delegation` only when the user asks for status or the result is needed
before another decision. Filter by profile or distinctive goal text. Use
`cancel_delegation` only for an explicit request to cancel a background job;
if multiple jobs match, narrow the goal instead of guessing.

## Boundaries

- Do not delegate recursively.
- Do not use delegation for Gmail, Calendar, Notion, GitHub writes, OAuth, or
  other external side effects.
- Do not ask the subagent to modify files outside its output directory.
- Do not assume an interrupt of the foreground response cancels an accepted
  delegation.
- Treat the mailbox completion as the authoritative terminal result.
