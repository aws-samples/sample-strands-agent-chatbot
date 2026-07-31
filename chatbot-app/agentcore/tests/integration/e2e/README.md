# E2E Integration Tests

Integration tests that drive the deployed stack through the BFF
`/api/stream/chat` endpoint and assert — from the AG-UI SSE stream — that each
distinct protocol path (Local Python, Builtin SDK/WebSocket, Gateway MCP,
A2A, Skill dispatch, Memory) actually works. The Research Agent case completes
the approval interrupt roundtrip before asserting the deployed agent result.

Assertions are event-based (`TOOL_CALL_START.tool_call_name`,
`TOOL_CALL_RESULT.content`, `RUN_FINISHED`), so LLM text nondeterminism does
not cause flakes. The only text-based check is the memory-roundtrip test.

## One-time setup

Create a dedicated test user (do **not** add to Terraform — keep credentials
out of state):

```bash
export E2E_COGNITO_USER_POOL_ID=$(cd ../../../../../infra/environments/dev && terraform output -raw cognito_user_pool_id)
export E2E_TEST_USERNAME=e2e-test-user
export E2E_TEST_PASSWORD='Choose-A-Strong-Password-123!'

./scripts/create_test_user.sh
```

The script creates the user and immediately sets a permanent password, so no
FORCE_CHANGE_PASSWORD challenge is required on first login.

## Running

```bash
cd chatbot-app/agentcore

# Populate env from terraform outputs
pushd ../../infra/environments/dev >/dev/null
export E2E_BFF_URL=$(terraform output -raw chat_cloudfront_url)
export E2E_COGNITO_REGION=$(terraform output -raw aws_region)
export E2E_COGNITO_USER_POOL_ID=$(terraform output -raw cognito_user_pool_id)
export E2E_COGNITO_CLIENT_ID=$(terraform output -raw cognito_web_client_id)
popd >/dev/null

export E2E_TEST_USERNAME=e2e-test-user
export E2E_TEST_PASSWORD='...'

# Run the suite (the `e2e` marker is excluded by default; opt in explicitly)
python -m pytest tests/integration/e2e -m e2e -v

# Optional: enable 3LO cases (requires a prior OAuth consent in the browser
# for the test user against Google/GitHub providers)
RUN_3LO=1 python -m pytest tests/integration/e2e -m e2e -v -k "3lo"

# Run a single case by id:
python -m pytest tests/integration/e2e -m e2e -v -k "gateway_arxiv"
```

Expected: 11 passes out of the box, 13 with `RUN_3LO=1`.

## Env var reference

| Var | Source | Notes |
|-----|--------|-------|
| `E2E_BFF_URL` | `terraform output -raw chat_cloudfront_url` | Deployed CloudFront, e.g. `https://d123.cloudfront.net` |
| `E2E_COGNITO_REGION` | `terraform output -raw aws_region` | |
| `E2E_COGNITO_USER_POOL_ID` | `terraform output -raw cognito_user_pool_id` | Needed only by the user-creation script |
| `E2E_COGNITO_CLIENT_ID` | `terraform output -raw cognito_web_client_id` | Web client (no secret) — avoids SECRET_HASH |
| `E2E_TEST_USERNAME` | manually chosen | e.g. `e2e-test-user` |
| `E2E_TEST_PASSWORD` | manually chosen | Must satisfy the user pool's password policy |
| `RUN_3LO` | optional | `1` to enable 3LO test cases; default skip |

## Interpreting failures

The assertion failure messages include the actual list of tools the agent
called, so the cause is usually obvious:

- **"Expected a tool matching (...), got []"** — agent didn't invoke any tool.
  Likely a prompt-routing issue: make the prompt more explicit about the tool
  path you want to exercise.
- **"Expected (...), got ['some_other_tool']"** — agent routed to a different
  tool. Check whether a skill / tools-config.json change broke the mapping.
- **"No TOOL_CALL_RESULT for X"** — tool started but never returned. Usually a
  backend crash; check the agent runtime logs.
- **"All results for X look like errors: [...]"** — tool ran but failed. If
  this is a Gateway tool, check the Lambda's CloudWatch logs; likely an auth
  issue (SigV4) or a bad external API key.
- **"RUN_FINISHED not observed"** — the stream closed without the terminal
  event. Extend the timeout or check backend logs.

## Scope

This suite is L4 (agent turn via BFF). L1 health checks, L2 direct Lambda
invoke, L3 skill dispatcher unit tests are intentionally excluded — they live
either under `tests/unit/` or are not implemented. Voice / BidiAgent is a
separate concern (WebSocket, not SSE).
