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

## S3 Files Integration

The artifact bucket remains the durable source of truth. Amazon S3 Files exposes
the Code Interpreter namespace as a filesystem without changing the logical
workspace API.

Code Interpreter receives a dynamically created access point rooted at:

```text
/code-interpreter-workspace/{userId}/{sessionId}
```

That access point is mounted at `/mnt/workspace`. The access point root is the
security boundary; path validation or a working directory is not treated as an
isolation mechanism. A Code Interpreter session cannot traverse to another
user or chat session.

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
documents/          S3 API
code-interpreter/   S3 Files mount, with S3 API fallback
code-agent/         S3 API
```

Uploads are written to the existing documents prefix and copied once to the
Code Interpreter workspace `inputs/` directory through the backing bucket.
S3 Files imports that prefix on first directory access. Code Interpreter output
files are created directly in `/mnt/workspace`; the previous base64 preload and
push path remains only as a rollout fallback.

Access point metadata is stored under the hidden
`.workspace-access-points/{userId}/{sessionId}.json` prefix. Session deletion
removes the access point best-effort before removing this registry object. File
data follows the existing workspace retention policy.

The dev rollout is controlled by `enable_s3_files_workspace`. Disabling it
removes S3 Files IAM permissions, mounts, and environment configuration and
returns Code Interpreter to the legacy synchronization path.

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

- Existing tools continue to work while the UI gains a unified file browser.
- Storage migration does not require another frontend protocol change.
- Artifacts and files remain separate concepts.
- Real-time file change events and Workspace-panel upload, rename, and delete
  remain follow-up phases. Chat attachment uploads are already imported into
  the session workspace.
