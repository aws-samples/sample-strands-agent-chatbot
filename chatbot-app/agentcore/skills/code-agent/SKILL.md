---
name: code-agent
description: Autonomous coding agent. Delegate any task that involves understanding, writing, or running code — from a GitHub issue, a bug report, or a user request. It explores, implements, and verifies on its own.
---

# Code Agent

An autonomous coding agent. It doesn't just write code on demand — it thinks through problems, forms its own plan, reads the existing codebase to understand context, implements solutions iteratively, and verifies they work before finishing.

Given a goal, it will:
- Explore the workspace to understand what's already there
- Break the task into steps and track them with a todo list
- Implement, run, and iterate until the outcome is correct
- Ask only when it hits a real decision point, not for every micro-step

Brief it like you'd brief a capable engineer: describe what you want to achieve, not how to do it.

## Code Agent vs Code Interpreter

| | Code Agent | Code Interpreter |
|---|---|---|
| **Nature** | Autonomous agent (Claude Code) | Sandboxed execution environment |
| **Best for** | Multi-file projects, refactoring, test suites | Quick scripts, data analysis, prototyping |
| **File persistence** | All files auto-synced to S3, accessible via workspace tools | Files in `/mnt/workspace` persist across interpreter restarts |
| **Session state** | Files + conversation persist across sessions | Variables persist within one interpreter session; workspace files persist for the chat session |
| **Autonomy** | Plans, writes, runs, and iterates independently | You write the code it executes |
| **Use when** | You need an engineer to solve a problem end-to-end | You need to run a specific piece of code |

## Execution Environment

The code agent runs in an **isolated container** dedicated solely to this session. Its filesystem, running processes, and local ports are completely separate from your own environment — do not attempt to access its paths or local servers via browser or other tools.

Trust the code agent's reasoning and autonomy — delegate not just implementation but also testing, verification, and iteration. Only step in when there's a genuine constraint the agent cannot resolve on its own; in that case, surface it to the user and decide together.

## Your Role as Orchestrator

You give direction and verify results. The agent explores, implements, and checks in when it hits a genuine decision point.

**Trust the agent to deliver. Don't over-specify the how — focus on the what. For complex tasks, break work into phases and steer between turns. Surface critical design decisions to the user early, then execute autonomously.**

### What you uniquely contribute

The code agent can read the entire workspace. What it can't do is reach outside it. That's where you add value.

Your job is to bring in what the agent can't get on its own:
- **User intent** — clarify ambiguous requirements, relay tradeoff decisions, confirm priorities
- **External context** — API docs, library changelogs, web search results, findings from other skills
- **Cross-session continuity** — context from earlier conversations that isn't in the workspace

What you should NOT be doing:
- Fully tracing a bug through the codebase to hand the agent a ready-made solution
- Pre-mapping which files need to change before delegating
- Doing the investigation that the agent should do

Reading a file to spot-check the agent's output is fine. Spending time reading 10 files to diagnose a problem yourself — then handing the agent a pre-solved task — is not. That's the agent's job.

### Division of responsibility

| You (orchestrator) provide | Code agent discovers on its own |
|---|---|
| **What** the user wants — goals, constraints, preferences | **How** to implement — codebase structure, existing patterns, design decisions |
| External context the agent can't reach — API docs, user requirements, npm/registry info | Internal context from the workspace — file layout, dependencies, coding conventions |
| Resolved decisions — framework choice, scope boundaries | Implementation decisions — variable naming, module structure, error strategies |

When the code agent encounters a **requirements-level question** it can't resolve from the codebase alone (e.g., "should this be public or internal?", "which auth provider?"), it will surface it. That's the right behavior — resolve it and pass the answer back. Don't try to pre-answer every possible question; let the agent ask when it genuinely needs direction.

---

## Smart Delegation — Scale Your Approach to Complexity

The goal is to deliver the best possible result with minimal friction. The key is how you (orchestrator) and the code agent collaborate — not just fire-and-forget.

> **One at a time.** The code agent runs as a single process against one workspace. Always wait for the current call to complete before making the next one. Never issue parallel `code_agent` calls — they will conflict and produce broken results.

> **Timeout awareness.** Each code agent call has a ~30-minute practical limit. For large tasks, break them into focused phases (explore → implement → test) rather than sending a single massive request. If a task might exceed this, split it proactively — don't wait for a timeout error.

### Simple tasks — delegate directly in one call:

    code_agent(task="Fix the typo in src/config.ts line 42: 'recieve' → 'receive'")

### Medium tasks — delegate with clear scope, let the agent plan internally:

    code_agent(task="Add input validation to the /api/users endpoint.
      Validate email format and required fields. Add tests.")

### Complex tasks — break into phases, steer between turns:

    # Turn 1: Explore & plan
    code_agent(task="Explore how auth works and propose a plan for adding JWT.
      Do NOT modify files yet.")

    # Review the plan the agent returns — does the approach make sense?

    # Turn 2: Implement
    code_agent(task="Implement JWT middleware with httpOnly cookies.")

    # Turn 3: Integrate
    code_agent(task="Apply middleware to routes. Exclude /api/public.")

    # Turn 4: Verify
    code_agent(task="Run full test suite and fix any failures.")

    # → Report to user

Use your judgment. The complexity of the delegation should match the complexity of the task. Don't over-orchestrate simple work, but don't fire-and-forget complex multi-file changes either.

### Multi-turn Agent Interaction

For complex tasks, the orchestrator and code agent naturally go back and forth. This happens autonomously — the user doesn't need to be involved in each turn:

    Turn 1: Explore → Agent returns findings + proposed plan
    Turn 2: Implement core → Agent returns results
    Turn 3: Fix issue found in Turn 2 → Agent iterates
    Turn 4: Run tests → All pass
    → Report to user: "JWT auth added. 4 files changed, 12 tests pass."

The user sees real-time terminal progress throughout. They only get pulled in if a genuine design decision emerges that the code agent can't resolve from the codebase alone.

### Surface Critical Decision Points (Only When Necessary)

Before diving into implementation, scan for genuine ambiguities that only the user can resolve:

- **Architecture choices**: "REST vs GraphQL?", "Redis vs DynamoDB?"
- **Scope tradeoffs**: "Should this affect existing data or only new records?"
- **Behavior decisions**: "Fail fast or degrade gracefully?"

If you spot these, ask the user **before** delegating implementation. But most tasks don't need this — if the codebase and user request are clear enough, just proceed.

**Important**: Ask only what the user must decide. Don't ask about implementation details the agent can figure out. Don't ask "should I proceed?" — just proceed after resolving any genuine decision point.

---

## Reporting Results to the User

When the code agent finishes, summarize concisely. Do NOT pass through raw code, full file contents, or verbose agent output. The user sees the code agent's terminal activity in real-time — they don't need it repeated.

**Include:**
- What changed and where (file level, not line-by-line)
- What was verified and how (test output summary, not raw logs)
- Design decisions made (anything that affects future work)
- Known limitations or deferred items

**Do NOT include:**
- Raw source code or full file contents
- Line-by-line diffs or the agent's exploration logs
- Lengthy code blocks unless the user explicitly asked to see code

**Example format:**

    Files changed:
      - src/middleware/rateLimiter.ts — added rate limiting logic (new file)
      - tests/rateLimiter.test.ts — added 4 tests; all pass

    Verified: ran full test suite (42 tests, 0 failures)

    Note: rate limit is currently per-IP. If per-user-ID is needed later,
    the key function can be swapped without touching routes.

---

## Orchestration Process

→ [DESIGN.md](DESIGN.md) — requirements capture, scope decisions, trade-off escalation
→ [IMPLEMENT.md](IMPLEMENT.md) — stepwise delegation, steering, correctness verification
→ [REVIEW.md](REVIEW.md) — iterative review, complexity-based depth, known issue checklist

---

## Session Management

- **`compact_session=True`** — before a new task in a long session. Summarizes history, saves tokens, preserves context.
- **`reset_session=True`** — only when switching to a completely unrelated project. Clears history, keeps workspace files.
- Omit both for continuation of the same task.

### Context isolation between tasks

A long conversation that handles multiple unrelated tasks is a liability — earlier context bleeds into later tasks and causes subtle wrong assumptions. When switching to a significantly different task (e.g., bug fix → new feature, frontend → backend), use `compact_session=True` to summarize and reset context. This is especially important when the nature of the work changes, not just the file being edited.

---

## When to Delegate vs Handle Directly

| Delegate to code_agent | Handle directly |
|---|---|
| Implement from a GitHub issue or feature request | Explain how an algorithm works |
| Investigate code to figure out an implementation approach | Write a short standalone snippet |
| Fix a failing test or bug | Answer a syntax or API question |
| Refactor a module | Simple code review without changes |
| Analyze uploaded source files | Generate a one-off script with no files |
| Run tests and fix failures | Summarize what code does |
| Scaffold following project conventions | |

---

## Uploaded Files

Files uploaded by the user are automatically available in the workspace:

```
task = "Unzip the uploaded my-project.zip and summarize the architecture."
```

---

## Advanced: Structured Task Template

Only use this when requirements are already fully resolved and you need explicit acceptance criteria. For most tasks, a plain description works better.

```xml
<task>
  <objective>Verifiable "done" state.</objective>
  <scope>What area of the system to work within. What to leave alone.</scope>
  <context>API signatures, versions, prior research findings.</context>
  <constraints>Language version, banned dependencies, style rules.</constraints>
  <acceptance_criteria>Commands that must pass: pytest, mypy, etc.</acceptance_criteria>
</task>
```

## UI Guidance (from tools-config)

**Code Agent:**
- Delegate tasks that require reading, writing, or running code in an isolated workspace
- Uploaded files are automatically available — do not encode them in the task
- Session state (files + context) persists across turns
- Use compact_session=True for long sessions; reset_session=True only when switching to an unrelated project
- After completion: summarize which files changed and key outcomes (2-3 sentences)
