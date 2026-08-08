# Durable Session Mailbox Architecture

Status: Implemented for asynchronous research delivery; extensible to other
external completions

## Implementation Status

Implemented:

- Dedicated orchestration table, mailbox, fencing lease, retry, and dead letter.
- Research completion adapter with deterministic event IDs.
- AgentCore Memory idempotency metadata for mailbox-originated messages.
- Generic `artifact.upserted` and `assistant.turn.completed` projections.
- Foreground safe-boundary recovery and M2M DynamoDB Stream dispatcher.
- Research job migration from the users table to orchestration records.
- Stable logical message identity with legacy event-ID fallback.
- Session delete tombstones that reject late background delivery.
- Truncate invalidation for mailbox assistant projections when origin identity
  is available.

Compatibility paths remain for legacy research jobs, message metadata maps,
and non-research artifacts. General tool artifacts still require the catalog
migration described below. Full asynchronous cleanup of deleted-session
Memory, S3 objects, job rows, and message metadata remains Phase 6 work.

Foreground user turns and their synchronous tool calls intentionally remain in
the existing AG-UI/Strands execution loop. They do not pay for a DynamoDB
mailbox round trip. The mailbox is the durable ingress for work that outlives
or originates outside that loop. Tool progress and token events remain
transient; only terminal asynchronous transitions use durable projections.

## Decision

Chat execution will use a durable, per-session mailbox with one coordinator
writer per session for background inputs. Background work may run in parallel,
but only the session coordinator may apply those results to the canonical
conversation or Strands agent state. Foreground turns continue through the
existing Runtime session and execution registry.

This is not full event sourcing. Durable business transitions are stored;
token deltas and high-frequency progress remain transient.

## Goals

- Deliver asynchronous results whether the session is idle or busy.
- Recover pending delivery after a process or Runtime restart.
- Preserve one ordered conversation and one consistent Strands state.
- Use one event contract for research, long-running tools, OAuth completion,
  artifact readiness, and future external callbacks.
- Keep live rendering loosely coupled from agent execution.
- Make retries explicit and idempotent.

## Non-goals

- Persist every token or progress update.
- Replace AgentCore Memory with DynamoDB.
- Reimplement Strands interrupt or conversation-manager persistence.
- Interrupt an in-flight model token stream to inject an asynchronous result.
- Retry arbitrary external side effects without a tool-level idempotency
  contract.

## Persistence Ownership

| Data | Canonical owner | Notes |
| --- | --- | --- |
| User and assistant messages | AgentCore Memory | Includes toolUse/toolResult transcript |
| Strands state | AgentCore Memory | Interrupt, model, conversation-manager, compaction |
| Session mailbox and leases | DynamoDB orchestration table | Durable control plane |
| Run/job state | DynamoDB orchestration table | Small records only |
| Message UI metadata | DynamoDB orchestration table | One item per logical message |
| Artifact catalog | DynamoDB orchestration table | Metadata, version, checksum, body reference |
| Artifact bodies | S3 | Deterministic immutable or versioned keys |
| Token deltas and live progress | SSE/WebSocket | Terminal milestones may be durable |
| Session list projection | DynamoDB sessions table | Derived, repairable metadata |

AgentCore Memory remains the source of truth for data required to recreate a
Strands agent. DynamoDB must not contain a second canonical transcript.

## Three Delivery Lanes

### Ordered session mailbox

Semantic inputs that may change the conversation:

- `async_result.ready`
- `interrupt.response.received`
- `artifact.context.requested`

These are serialized by the session coordinator.

`user.message.received` may move into this lane later if the product needs
server-side queued user turns across disconnected clients. It is not required
for durable external completion delivery and is deliberately excluded from the
current rollout.

### Immediate control lane

Signals that must bypass a busy mailbox:

- stop/cancel
- OAuth completion signal observed by a blocked tool
- lease fencing and health state

These use the same correlation identifiers but do not wait behind a long agent
turn.

### Transient stream lane

Non-durable rendering data:

- token deltas
- reasoning deltas
- tool progress
- research step progress

Loss is repaired by loading the durable transcript, artifact catalog, and
terminal projections.

## Mailbox Envelope

```json
{
  "schemaVersion": 1,
  "eventId": "stable producer-generated id",
  "eventType": "async_result.ready",
  "sessionId": "chat session id",
  "userId": "authenticated owner id",
  "createdAt": "ISO-8601",
  "availableAt": "ISO-8601",
  "source": {
    "type": "research_job",
    "id": "job id"
  },
  "correlation": {
    "runId": "originating run id",
    "toolUseId": "originating tool use id",
    "artifactId": "artifact id"
  },
  "payload": {},
  "payloadRef": null,
  "visibility": "internal",
  "status": "pending",
  "attempts": 0
}
```

`eventId` is an application ID, not an AgentCore Memory `eventId`. Producers
must reuse it when retrying the same logical delivery.

Large payloads are written to S3 first and represented by `payloadRef`.

## DynamoDB Layout

Use a dedicated orchestration table:

```text
PK sessionKey = USER#{userId}#SESSION#{sessionId}
SK recordKey
```

Record families:

```text
STATE
INBOX#{eventId}
OUTBOX#{eventId}
RUN#{runId}
JOB#{jobId}
ARTIFACT#{artifactId}
MESSAGE_META#{logicalMessageId}
```

The table uses on-demand capacity, point-in-time recovery, encryption, TTL, and
DynamoDB Streams. The existing users table remains profile-oriented. The
existing sessions table remains a session-list projection.

Mailbox insertion uses a conditional put on `INBOX#{eventId}`. Duplicate
producer delivery is therefore success without creating a second command.

## Coordinator Lease

The `STATE` item contains:

```text
leaseOwner
leaseEpoch
leaseUntil
version
lastProcessedAt
```

Lease acquisition and renewal are conditional on `version` and `leaseEpoch`.
Every mailbox acknowledgement is fenced by the active lease epoch. A stale
coordinator may finish local work but cannot acknowledge or publish it.

The initial implementation queries all `INBOX#` records for one session and
sorts eligible events by `createdAt`, then `eventId`. Mailbox volume is expected
to be small. A status GSI is added only if measurements show it is necessary.

## Processing Protocol

1. Producer writes a large body to a deterministic S3 key when required.
2. Producer conditionally creates the mailbox event.
3. A local notifier or external dispatcher requests a mailbox drain.
4. Coordinator conditionally acquires the session lease.
5. Coordinator claims the oldest eligible event.
6. Coordinator loads AgentCore Memory state and materializes an internal input.
7. Agent execution writes transcript/state using deterministic idempotency
   tokens and stable logical message metadata.
8. Coordinator transactionally marks the inbox event processed, updates small
   projections, and creates durable outbox milestones.
9. Live subscribers receive the outbox milestone and any available transient
   stream.

The acknowledgement occurs only after canonical AgentCore persistence
succeeds.

## Cross-store Recovery

There is no transaction spanning S3, AgentCore Memory, and DynamoDB. Recovery
therefore uses deterministic identifiers and ordered commits.

### S3 before DynamoDB

Artifact body upload occurs first. A crash may leave an orphaned S3 object.
Lifecycle cleanup or reconciliation may remove unreferenced objects.

When a producer owns both records, the artifact catalog and mailbox event
should be written in one DynamoDB transaction. The current research adapter
instead persists `JOB#{jobId}` before publishing `INBOX#{eventId}` because the
job is also its migration-compatible status record. A failed or ambiguous
INBOX write is reconciled with a strongly consistent read and recreated from
the deterministic event ID on the next foreground invocation.

### AgentCore Memory before mailbox acknowledgement

Transcript writes use a deterministic AgentCore `clientToken` derived from:

```text
sessionId + mailbox eventId + transcript phase
```

AgentCore Memory event metadata also records:

```text
logicalMessageId
originEventId
visibility
```

If the process crashes after the Memory write, retrying the mailbox event does
not append a duplicate message. Internal inputs use `extractionMode=SKIP` so
background protocol messages do not pollute long-term memory.

The pinned AgentCore SDK does not expose `clientToken` through the Strands
session-manager method. The implemented adapter injects `clientToken` and
`extractionMode=SKIP` through the botocore parameter-build event and is covered
by session-manager contract tests. SDK upgrades must retain that contract or
replace the adapter with a first-class parameter once one is available.

### External tool side effects

At-least-once mailbox handling does not make external tools idempotent.

Tools are classified as:

- `pure`: safe to retry.
- `idempotent`: retry with a provider or application idempotency key.
- `side_effecting`: do not replay automatically after an ambiguous result.

Email send, repository writes, payments, and similar operations require a
tool-side effect ledger or provider idempotency support before whole-turn
automatic retry is enabled.

The research completion continuation disables all tools, so the remaining
ambiguous retry window can repeat a model inference but cannot repeat an
external tool side effect. Deterministic Memory client tokens prevent duplicate
transcript events. Exactly-once model execution is not claimed.

## Busy and Idle Semantics

### Idle session

A pending mailbox event triggers a dispatcher invocation for the same Runtime
session. The coordinator creates a separate assistant turn and publishes its
terminal projection.

DynamoDB Streams alone do not wake AgentCore Runtime. The dispatcher must
obtain a service Cognito token, invoke `drain_mailbox`, and prove that the
mailbox session belongs to the requested user. Runtime code must not trust an
arbitrary `state.user_id` supplied by a general client.

### Busy session

The artifact/result projection may render immediately. Conversation mutation
waits until a safe agent boundary:

- after the current model response,
- between tool/model cycles when a supported hook is available, or
- after interrupt state has been durably synchronized.

The coordinator never injects content into the middle of a token stream.

## Frontend Projection

The frontend consumes generic session events rather than research-specific
delivery executions:

- `run.started`
- `tool.started`
- `tool.progress`
- `artifact.upserted`
- `assistant.turn.completed`
- `run.failed`

Events carry `originEventId`, `runId`, `toolUseId`, and `artifactId` so the UI
can map a background completion to its original tool card while rendering the
assistant completion as a separate turn.

Process-local SSE buffering remains an optimization. If it is unavailable
after a restart, the frontend reloads AgentCore history and DynamoDB
projections instead of treating replay failure as data loss.

## Artifact Migration

Current tools mutate the full `agent.state.artifacts` map. Migration uses a
compatibility repository:

1. Read existing full artifacts from agent state.
2. Write new bodies to S3 and catalog records to DynamoDB.
3. Dual-read catalog records and legacy agent state.
4. Store only compact references in `agent.state.artifacts`.
5. Update tools to use optimistic artifact versions instead of map replacement.
6. Remove full-body fallback after old sessions have aged out or migrated.

Parallel tools may update different artifacts. Updating the same artifact
requires an expected version and returns a conflict rather than silently
overwriting another result.

## Message Identity Migration

Introduce `logicalMessageId` while retaining AgentCore `eventId` as a storage
locator.

During migration:

- history prefers `logicalMessageId` from event metadata;
- old messages fall back to AgentCore `eventId`;
- feedback/document metadata reads both keys;
- new metadata is stored as `MESSAGE_META#{logicalMessageId}`;
- redaction or event replacement preserves the logical ID.

## Delete, Truncate, and Compaction

The target workflows are:

- Session delete writes a tombstone, blocks new mailbox work, cancels active
  jobs, and asynchronously removes Memory events, catalog records, and S3 data.
- Truncate tombstones affected logical messages and invalidates associated UI
  metadata without deleting unrelated artifacts automatically.
- Compaction changes LLM context but does not delete artifact bodies or
  mailbox audit state.
- Pending asynchronous results targeting a deleted session become cancelled,
  not delivered to a recreated session with the same display title.

The current rollout implements the delete tombstone and mailbox projection
invalidation. It does not yet perform the full cross-store garbage collection
described above.

## Migration Plan

### Phase 0: contracts and observability

- Add this architecture decision.
- Define typed envelopes and repository interfaces.
- Pin AgentCore and Strands SDK versions.
- Add correlation IDs to logs and metrics.

Exit criteria:

- Existing behavior is unchanged.
- Event IDs and cross-store write failures are observable.

### Phase 1: durable mailbox foundation

- Provision the orchestration table and IAM.
- Implement cloud and local mailbox repositories.
- Add conditional enqueue, lease, claim, ack, retry, and dead-letter behavior.
- Add unit tests for duplicates, stale leases, and ordering.

Exit criteria:

- Mailbox operations work without changing chat delivery.

### Phase 2: coordinator and research adapter

- Implement the single-writer coordinator.
- Make research completion enqueue `async_result.ready`.
- Keep the current delivery path behind a fallback flag.
- Recover pending events on foreground invocation.

Exit criteria:

- Duplicate research completion creates one assistant turn.
- A restart between completion and delivery is recoverable.
- Parallel research jobs remain independent.

### Phase 3: generic frontend projections

- Add a generic session-events endpoint/subscription.
- Render artifact updates and assistant completion turns from generic events.
- Retain research job polling only for progress during migration.

Exit criteria:

- Delivery no longer depends on a process-local research execution buffer.
- Refresh and live delivery produce the same UI state.

### Phase 4: wake dispatcher

- Add DynamoDB Stream to dispatcher integration.
- Acquire a service Cognito token and invoke `drain_mailbox`.
- Add ownership validation and wake coalescing.

Exit criteria:

- An idle session wakes after Runtime process loss.
- Concurrent wake requests result in one active coordinator.

### Phase 5: artifacts and message identity

- Introduce the artifact repository and dual-read compatibility layer.
- Move artifact bodies to S3 and metadata to the catalog.
- Add stable logical message IDs and AgentCore idempotency tokens.
- Move message UI metadata to individual records.

Exit criteria:

- Agent state contains artifact references, not large bodies.
- Feedback survives message redaction/replacement.
- Session items no longer grow with message count.

### Phase 6: cleanup and control workflows

- Convert delete/truncate/compaction to cross-store workflows.
- Remove research-only delivery and polling code.
- Retire legacy session metadata maps after migration.

## Rollout and Rollback

Feature flags:

```text
SESSION_MAILBOX_WRITE_ENABLED
SESSION_MAILBOX_DELIVERY_ENABLED
SESSION_EVENT_PROJECTION_ENABLED
ARTIFACT_CATALOG_WRITE_ENABLED
ARTIFACT_CATALOG_READ_ENABLED
```

The first two flags are implemented. The remaining names reserve later
artifact-catalog rollout boundaries and are not active configuration yet.

Rollout order is write-only, shadow verification, read enablement, delivery
enablement, then legacy removal.

Rollback disables new reads/delivery while retaining dual-written records.
Rollback must not delete mailbox events or catalog entries.

## Required Tests

- Duplicate enqueue with the same event ID.
- Two coordinators racing for one session lease.
- Lease expiry and stale-owner acknowledgement.
- Crash after S3 upload but before catalog commit.
- Crash after AgentCore message write but before mailbox acknowledgement.
- Busy user turn plus one or more asynchronous completions.
- Interrupt waiting while an asynchronous result arrives.
- Stop signal while mailbox work is queued.
- Parallel research jobs completing in reverse order.
- Runtime restart before frontend replay.
- Artifact update version conflict.
- Session delete while a worker is running.
- Old event-ID feedback migration.
- Internal message exclusion from UI and long-term-memory extraction.
