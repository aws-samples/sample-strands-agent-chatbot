# Asynchronous Delegation

The supervisor delegates one bounded task to an isolated specialist and
continues the foreground conversation without waiting for the result.

## Ownership

- DynamoDB `DELEGATION_JOB` records own durable execution and delivery state.
- DynamoDB Stream and the session FIFO queue dispatch queued work.
- The orchestrator conditionally claims a job and invokes the specialist A2A
  Runtime in a background task.
- The specialist context is ephemeral. Results and artifacts are durable.
- The session mailbox publishes one deterministic completion event back to the
  supervisor conversation.

Execution and conversation delivery use separate state machines:

```text
queued -> running -> succeeded | failed | cancelled | timed_out
none   -> pending -> published -> delivered
```

The dispatcher reconciles queued jobs and running jobs with stale heartbeats
every two minutes. Conditional execution tokens fence retries and prevent an
old worker from overwriting cancellation or a newer attempt.

## Profiles

`analyst` may read the selected session workspace files and use Code
Interpreter. `reviewer` is read-only and cannot execute code. Neither profile
can delegate, access connectors, or perform external side effects.

The supervisor supplies one goal, one deliverable, acceptance criteria,
explicit constraints, a bounded context summary, and selected workspace paths.
The server limits a session to two active delegations and three execution
attempts per job.

## Interrupt And Cancellation

Foreground response interruption does not cancel accepted background work.
Delegations are cancelled explicitly through the job API. Conversation
truncate cancels jobs from the previous conversation epoch, while session
deletion removes their durable records. The runner polls cancellation while
streaming and closes the A2A stream so the remote task receives cancellation.

## Result Contract

The specialist returns a bounded JSON envelope:

```json
{
  "summary": "Short result",
  "findings": [],
  "artifacts": [],
  "openQuestions": [],
  "scopeExceptions": []
}
```

Large output belongs under `outputs/delegations/{jobId}` in the session
workspace. The full specialist transcript is never merged into the supervisor
context.
