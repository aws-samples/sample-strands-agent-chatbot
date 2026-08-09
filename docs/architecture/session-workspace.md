# Session Workspace

## Status

Accepted for incremental implementation.

## Context

Agent tools currently use an S3 bucket as a workspace, but each integration
implements its own object synchronization. Code Interpreter downloads objects,
injects them into its filesystem, and uploads results again. Code Agent restores
and synchronizes a separate prefix. The frontend discovers only selected
document types after a tool finishes.

This makes S3 object keys part of several client and tool contracts and prevents
the session from feeling like one persistent workspace.

## Decision

The application exposes one logical workspace per authenticated user and chat
session. Consumers use logical paths and never depend on S3 keys or a particular
filesystem implementation.

The initial repository maps existing S3 prefixes into these namespaces:

| Logical path | Current storage prefix |
| --- | --- |
| `documents/` | `documents/{userId}/{sessionId}/` |
| `code-interpreter/` | `code-interpreter-workspace/{userId}/{sessionId}/` |
| `code-agent/` | `code-agent-workspace/{userId}/{sessionId}/` |

The repository contract supports directory listing, file metadata, preview, and
download. Directory listing is paginated and scoped by both authenticated user
and explicit session ID. Paths containing traversal segments, backslashes, or
NUL bytes are rejected.

The right sidebar has two views:

- **Artifacts** are conversational projections such as research reports and
  browser sessions.
- **Workspace** is the durable file tree for the chat session.

Selecting a workspace file opens its preview in the same sidebar. Download is a
secondary action.

## API Contract

```text
GET /api/workspace/entries?path=&cursor=
GET /api/workspace/preview?path=
POST /api/workspace/download
```

All requests require `X-Session-ID`; authenticated identity determines the user
scope. The frontend consumes `WorkspaceEntry` and `WorkspacePreview` objects
defined in `src/lib/workspace/types.ts`.

## Storage Evolution

The S3 repository is a compatibility implementation. A later phase will attach
Amazon S3 Files to AgentCore Runtime and Code Interpreter and implement the same
repository contract over the mounted filesystem.

Arbitrary-code environments must receive a session-rooted access point. A
shared root access point must not be exposed to Code Interpreter or Code Agent.
The intended mounted layout is:

```text
/mnt/workspace
├── uploads/
├── projects/
├── outputs/
├── tools/
└── .system/
```

The `.system` namespace is never shown in the user-facing browser.

## Retention

The active workspace follows the chat session lifecycle. Long-lived or shared
outputs are explicitly published or archived. Session deletion must eventually
remove the corresponding workspace root and access point.

## Consequences

- Existing tools continue to work while the UI gains a unified file browser.
- Storage migration does not require another frontend protocol change.
- Artifacts and files remain separate concepts.
- Real-time file change events, upload, rename, delete, and S3 Files mounts are
  follow-up phases rather than implicit behavior in the initial slice.
