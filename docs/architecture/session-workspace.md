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
| `code-interpreter/` | `code-interpreter-workspace/{workspaceId}/` |
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

## S3 Files Integration

The artifact bucket remains the durable source of truth. Amazon S3 Files exposes
the Code Interpreter namespace as a filesystem without changing the logical
workspace API.

Code Interpreter receives a dynamically created access point rooted at:

```text
/code-interpreter-workspace/{workspaceId}
```

That access point is mounted at `/mnt/workspace`. The access point root is the
security boundary; path validation or a working directory is not treated as an
isolation mechanism. A Code Interpreter session cannot traverse to another
user or chat session.

`workspaceId` is the first 48 hexadecimal characters of
`SHA-256(userId + NUL + sessionId)`. The bounded opaque ID keeps the access
point root below the S3 Files path-length limit.

The trusted frontend task mounts an access point rooted at
`/code-interpreter-workspace` read-only. Workspace API authorization still
scopes every request by authenticated user and explicit chat session. Mounted
paths are resolved with `realpath`, and symlinks that escape the session root
are rejected.

This read-only mount is intentional. S3 Files can take up to its export
inactivity window to synchronize a newly closed file back to the backing S3
bucket. Reading the mount allows the Workspace sidebar and Canvas preview to
see Code Interpreter outputs immediately instead of waiting for object export.

Existing logical namespaces remain unchanged:

```text
uploads/            S3 API, mapped to Code Interpreter inputs/
documents/          S3 API
code-interpreter/   S3 Files mount
code-agent/         S3 API
```

Workspace-panel uploads are written directly to the Code Interpreter workspace
`inputs/` prefix. PowerPoint tools use that canonical object directly; there is
no separate import step or `documents/.../powerpoint` copy. Published PowerPoint
files live under `artifacts/powerpoint/`. One hidden draft per source lives
under `.drafts/powerpoint/`, is updated with S3 ETag preconditions, and is
removed on finalize. Drafts expire after seven days and are excluded from
Workspace and PowerPoint listings.

Other supported chat attachments are stored by their document manager when
applicable and copied once to the same `inputs/` prefix.
Code Agent synchronizes that canonical prefix into its local `inputs/`
directory before every delegated task; the orchestrator does not relay a
second copy. It does relay the logical paths required by the delegated task,
and Code Agent fails the task before execution when any required input is
absent from the synchronized mirror.
Workspace uploads use session-scoped presigned PUT URLs, so file bytes do not
pass through the frontend task. Dev currently accepts workspace uploads up to
100 MB. JSON-family chat attachments are limited to 4 MB and represented in
model context by at most 40,000 characters; the complete object remains in
`inputs/` for Code Interpreter.
S3 Files imports that prefix on first directory access. Code Interpreter output
files are created directly in `/mnt/workspace`. Code Interpreter does not start
when its session-scoped S3 Files mount cannot be configured, attached, or
written. Session access points use a root POSIX identity because S3 Files
imports S3-created directories as `root:root`; the access point root remains
the user/session isolation boundary.

Access point metadata is stored under the hidden
`.workspace-access-points/{userId}/{sessionId}.json` prefix. Session deletion
removes the access point best-effort before removing this registry object. File
data follows the existing workspace retention policy.

## Network Boundary

S3 Files requires Code Interpreter VPC mode and mount targets in compatible
subnets. `code_interpreter_supported_az_ids` identifies the stable availability
zone IDs supported by the service. S3 Files mount targets remain available in
every ECS subnet.

Code Interpreter uses dedicated private subnets and a single public NAT Gateway
for outbound HTTP, package installation, and external APIs. The NAT route is
attached only to Code Interpreter subnets; the frontend and other runtimes keep
their existing network paths.

The single NAT is intentional for dev cost control. It creates a cross-AZ path
for Code Interpreter sessions outside the NAT availability zone and is not an
AZ-resilient production topology. A production environment should use one NAT
per Code Interpreter availability zone or an approved centralized egress
design.

## Retention

The active workspace follows the chat session lifecycle. Long-lived or shared
outputs are explicitly published or archived. Session deletion must eventually
remove the corresponding workspace root and access point.

## Consequences

- PowerPoint uses the canonical Workspace; Word and Excel retain their current
  document namespaces until separately migrated.
- The frontend upload protocol does not require a PowerPoint-specific import.
- Artifacts and files remain separate concepts.
- Workspace-panel and chat attachment uploads are imported into the session
  workspace. Real-time file change events, rename, and delete remain follow-up
  phases.
