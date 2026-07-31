# Three-Legged OAuth (3LO) Flow

This document describes how per-user OAuth authentication works for MCP tools (e.g., Gmail) via AgentCore Identity.

## Overview

3LO enables tools to access user-specific external services (Gmail, Calendar, etc.) by obtaining OAuth tokens on behalf of individual users. The flow involves three parties:

1. **User** — grants consent via Google sign-in
2. **AgentCore Identity** — manages token exchange and storage
3. **External Service** (Google) — issues access tokens

Once a user completes consent, the token is stored in **AgentCore Token Vault** so subsequent tool calls skip the consent step.

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────────┐     ┌────────────┐
│ Frontend │     │   BFF    │     │  AgentCore   │     │  MCP 3LO   │
│ (Next.js)│     │ (Routes) │     │   Runtime    │     │  Server    │
└────┬─────┘     └────┬─────┘     └──────┬───────┘     └─────┬──────┘
     │                │                   │                    │
     │  1. Chat msg   │                   │                    │
     │  + auth_token  │                   │                    │
     │───────────────>│  2. /invocations  │                    │
     │                │  + Bearer JWT     │                    │
     │                │──────────────────>│  3. MCP call       │
     │                │                   │  + WorkloadToken   │
     │                │                   │───────────────────>│
     │                │                   │                    │
     │                │                   │                    │ 4. get_resource_
     │                │                   │               ┌────│    oauth2_token()
     │                │                   │               │    │
     │                │                   │               │ AgentCore
     │                │                   │               │ Identity
     │                │                   │               │    │
     │                │                   │    5a. Token  └───>│
     │                │                   │    (cache hit)     │
     │                │                   │<───────────────────│
     │                │                   │                    │
     │                │    OR             │                    │
     │                │                   │    5b. auth_url    │
     │                │                   │    (no token)      │
     │                │                   │<───────────────────│
     │                │   tool_result:    │                    │
     │   SSE event    │   oauth_required  │                    │
     │<───────────────│<──────────────────│                    │
     │                │                   │                    │
     │  6. Continue   │                   │                    │
     │     opens popup│                   │                    │
     │  ┌──────────┐  │                   │                    │
     │  │ Google   │  │                   │                    │
     │  │ Consent  │  │                   │                    │
     │  └────┬─────┘  │                   │                    │
     │       │        │                   │                    │
     │  7. Redirect to /oauth-complete?session_id=xxx         │
     │       │        │                   │                    │
     │  8. POST /api/stream/elicitation-complete               │
     │───────────────>│                   │                    │
     │                │  9. DynamoDB completion signal         │
     │                │<─────────────────>│ CompleteResource-  │
     │                │                   │ TokenAuth          │
     │                │                   │                    │
     │                │  10. Pending MCP call resumes          │
     │                │                   │───────────────────>│
     │                │                   │    Token found!    │
     │   Gmail data   │   Gmail data      │    Gmail data      │
     │<───────────────│<──────────────────│<───────────────────│
```

## Step-by-Step Flow

### Step 1-3: Initial Tool Request

The frontend sends a chat message with the Cognito JWT as `auth_token`. The BFF forwards this to the AgentCore Runtime, which invokes the MCP 3LO server. The Runtime injects a `WorkloadAccessToken` header derived from the JWT.

**Key files:**
- `chatbot-app/frontend/src/app/api/stream/chat/route.ts` — extracts JWT, passes as `authToken`
- `chatbot-app/agentcore/src/agent/mcp/mcp_runtime_client.py` — sets `Authorization: Bearer {jwt}` header
- MCP 3LO Runtime (deployed on AgentCore) — extracts `WorkloadAccessToken` from headers

### Step 4-5: Token Lookup

The MCP server's `OAuthHelper.get_access_token()` calls `get_resource_oauth2_token()` with:

| Parameter | Value | Source |
|-----------|-------|--------|
| `resourceCredentialProviderName` | `"google-oauth-provider"` | Registered during deploy |
| `scopes` | Gmail scopes | Hardcoded in server |
| `oauth2Flow` | `"USER_FEDERATION"` | Per-user tokens |
| `workloadIdentityToken` | Derived from Cognito JWT | `WorkloadAccessToken` header |
| `resourceOauth2ReturnUrl` | `https://{cloudfront}/oauth-complete` | SSM parameter |

**Two possible outcomes:**
- **Cache hit** — `accessToken` returned, tool proceeds normally
- **Cache miss** — `authorizationUrl` returned, `OAuthRequiredException` raised

**Key file:** MCP 3LO Runtime handles token retrieval via `get_resource_oauth2_token()` internally

### Step 6: OAuth Popup

The MCP URL elicitation is streamed to the frontend as an
`oauth_elicitation` event. The frontend displays an authorization dialog.
The popup opens only after the user selects **Continue**, which keeps the
operation inside a browser user gesture and avoids popup blocking.

**Key file:** `chatbot-app/frontend/src/components/OAuthElicitationDialog.tsx`

### Step 7: Google Consent

The user signs in to Google and grants permissions in the popup. Google redirects the popup to:

```
https://{cloudfront}/oauth-complete?session_id={agentcore_session_id}
```

### Step 8-9: Token Completion

The `/oauth-complete` page extracts the AgentCore `session_id` and posts the
pending chat session and elicitation IDs to the authenticated BFF endpoint.
The BFF validates ownership and writes a completion signal to DynamoDB. The
orchestrator's waiting elicitation bridge reads that signal and invokes
`CompleteResourceTokenAuth` with the user's Cognito token. AgentCore then
stores the provider token in the Token Vault.

**Key files:**
- `chatbot-app/frontend/src/app/oauth-complete/page.tsx` — callback page
- `chatbot-app/frontend/src/app/api/stream/elicitation-complete/route.ts` — authenticated completion signal
- `chatbot-app/agentcore/src/agent/mcp/elicitation_bridge.py` — waits for completion and finalizes token auth

### Step 10: Resume

The bridge accepts the MCP URL elicitation after token completion. The paused
MCP tool retrieves the cached access token and resumes in the same request;
the user does not need to submit the chat request again.

## Token Persistence

### How Token Vault Identifies Users

The Token Vault associates stored OAuth tokens with a user identity. Two identifiers are used at different points in the flow:

| Phase | Identifier | Value |
|-------|-----------|-------|
| **Token request** (MCP server) | `workloadIdentityToken` | Derived from Cognito JWT by AgentCore Runtime |
| **Token completion** (BFF) | `userIdentifier.userToken` | Raw Cognito JWT from frontend |

AgentCore Identity extracts the `sub` (subject) claim from the JWT to identify the user. As long as the user's Cognito identity remains the same, the token should persist across sessions.

### Token Expiry

Google OAuth access tokens expire after 1 hour. AgentCore Identity handles refresh tokens internally — when the stored access token expires, it uses the refresh token to obtain a new one without requiring user consent again.

If the refresh token itself is revoked (e.g., user revokes access in Google Account settings), the next tool call will trigger a new consent flow.

### Known Behavior: Re-consent Between Sessions

Users may experience repeated OAuth consent prompts when:

1. **Cognito JWT expiry** — If the Cognito session expires and the user re-authenticates, the new JWT has a different `jti` (JWT ID). If AgentCore Identity matches tokens by the full JWT rather than just the `sub` claim, the stored OAuth token won't be found.

2. **Google OAuth app in "Testing" status** — Google limits test apps to 7-day refresh token lifetime. After 7 days, consent is required again. To fix this, publish the OAuth app or add the user as a test user.

3. **Scope changes** — If the MCP server's requested scopes change between deployments, the existing token may not cover the new scopes, triggering re-consent.

4. **Token Vault TTL** — AgentCore Token Vault may have a TTL policy on stored tokens. If tokens expire in the vault, re-consent is needed.

### Verifying Token Status

Check if a credential provider exists and view its configuration:

```python
import boto3
client = boto3.client('bedrock-agentcore-control', region_name='us-west-2')
response = client.get_oauth2_credential_provider(name='google-oauth-provider')
print(response)
```

## File Reference

| File | Role |
|------|------|
| `chatbot-app/agentcore/src/agent/mcp/mcp_runtime_client.py` | MCP Runtime client with JWT auth |
| `chatbot-app/agentcore/src/agent/mcp/elicitation_bridge.py` | Bridges OAuth elicitation between MCP server and frontend |
| `chatbot-app/agentcore/src/streaming/agui_event_processor.py` | Streams `oauth_elicitation` events to frontend |
| `chatbot-app/frontend/src/components/OAuthElicitationDialog.tsx` | Presents the user-triggered authorization action |
| `chatbot-app/frontend/src/app/oauth-complete/page.tsx` | Handles the provider return and signals completion |
| `chatbot-app/frontend/src/app/api/stream/elicitation-complete/route.ts` | Validates and stores elicitation completion |
| `infra/modules/oauth-providers/main.tf` | Registers OAuth credential providers (Terraform) |
