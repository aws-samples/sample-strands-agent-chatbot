# Deployment Guide

Complete deployment instructions for the AgentCore-based chatbot platform.

## Prerequisites

- **AWS Account** with Bedrock access (Claude models enabled)
- **AWS CLI** configured with credentials
- **Docker** installed and running
- **Node.js** and **Python** installed
- **Terraform** >= 1.11.0 (required for S3 native state locking via `use_lockfile`)
- **AgentCore** enabled in your AWS account region

## Architecture Overview

```
User -> CloudFront -> ALB -> Frontend+BFF (Fargate)
                              | HTTP
                         AgentCore Runtime
                         (Strands Agent container)
                              |
            +-----------------+-----------------+
            |                 |                 |
            v JWT             v A2A             v AWS SDK
     AgentCore Gateway   Research Agent    Built-in Tools
     (MCP endpoints)     Runtime           (Code Interpreter,
            |                               Browser + Nova Act)
     Lambda Functions (5x)
     +- Wikipedia, ArXiv,
        Google, Tavily, Finance

     AgentCore Memory
     +- Conversation history
        User preferences & facts
```

## Quick Deployment

### Deploy All Components

```bash
# 1. Configure environment
cp infra/environments/dev/terraform.tfvars.example infra/environments/dev/terraform.tfvars
# Edit terraform.tfvars with your settings

# 2. Deploy everything
./infra/scripts/deploy.sh apply
```

This deploys:
- Frontend + BFF (Fargate)
- AgentCore Runtime with Memory
- AgentCore Gateway + Lambda tools
- A2A Agent Runtimes (Research, Code)

**Estimated Time**: 20-30 minutes

For each configured OAuth provider, register the provider callback URI printed
at the end of deployment before testing its tools. See
[Google OAuth Setup](docs/guides/GOOGLE_OAUTH_SETUP.md) and
[GitHub OAuth Setup](docs/guides/GITHUB_OAUTH_SETUP.md).

### Remove All Components

```bash
./infra/scripts/deploy.sh destroy
```

## What Gets Deployed

### 1. Frontend + BFF Stack
- **Service**: ECS Fargate
- **Components**: Next.js UI + API routes (BFF)
- **Infrastructure**: ALB, CloudFront, Cognito
- **Module**: `infra/modules/chat`

### 2. AgentCore Runtime
- **Container**: Strands Agent with local tools
- **Documentation**: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html
- **Module**: `infra/modules/runtime`

### 3. AgentCore Memory
- **Purpose**: Persistent conversation storage with user preferences/facts retrieval
- **Features**: Short-term (conversation history) + Long-term (user context)
- **Documentation**: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
- **Module**: `infra/modules/memory`

### 4. Built-in Tools
- **Protocol**: AWS SDK + WebSocket
- **Tools**:
  - Code Interpreter: Python code execution for diagrams/charts
  - Browser Automation: Web navigation and data extraction (Nova Act AI)
- **Documentation**: https://docs.aws.amazon.com/bedrock/latest/userguide/

### 5. AgentCore Gateway
- **Purpose**: MCP tool endpoints with JWT authentication (Cognito)
- **Architecture**: Lambda functions exposed as MCP endpoints via AgentCore Gateway
- **Tools**: 5 Lambda functions (12 tools total)
  - Wikipedia (2 tools)
  - ArXiv (2 tools)
  - Google Search (2 tools)
  - Tavily (2 tools)
  - Finance (4 tools)
- **Documentation**: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html
- **Module**: `infra/modules/gateway` + `infra/modules/gateway-lambda-tool`

### 6. A2A Agent Runtimes
- **Protocol**: A2A (Agent-to-Agent)
- **Agents**: Research Agent, Code Agent
- **Module**: `infra/modules/runtime` (type = `a2a_agent`)

## Step-by-Step Deployment

### Step 1: Configure Environment

```bash
cp infra/environments/dev/terraform.tfvars.example infra/environments/dev/terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
project_name = "strands-agent-chatbot"
environment  = "dev"
aws_region   = "us-west-2"

# AgentCore Configuration
agentcore_model_id    = "us.anthropic.claude-sonnet-4-20250514-v1:0"
agentcore_temperature = 0.7

# Gateway Tools API Keys (optional - can also use Secrets Manager)
tavily_api_key          = ""
google_api_key          = ""
google_search_engine_id = ""
```

### Step 2: Deploy

```bash
# Deploy all components
./infra/scripts/deploy.sh apply

# Or deploy individual modules:
./infra/scripts/deploy.sh apply -target=module.chat          # Frontend + BFF only
./infra/scripts/deploy.sh apply -target=module.runtime_orchestrator  # Orchestrator Runtime only
./infra/scripts/deploy.sh apply -target=module.gateway       # Gateway + Lambda tools only
./infra/scripts/deploy.sh apply -target=module.runtime_research_agent  # Research Agent only

# Preview changes before applying
./infra/scripts/deploy.sh plan
```

### Step 3: Configure API Keys (if not in tfvars)

```bash
# Tavily API Key
aws secretsmanager put-secret-value \
  --secret-id strands-agent-chatbot/mcp/tavily-api-key \
  --secret-string "YOUR_TAVILY_KEY"

# Google Search Credentials
aws secretsmanager put-secret-value \
  --secret-id strands-agent-chatbot/mcp/google-credentials \
  --secret-string '{"api_key":"YOUR_KEY","search_engine_id":"YOUR_ID"}'
```

### Step 4: Access Application

After deployment completes, Terraform outputs the CloudFront URL:

```bash
cd infra/environments/dev
terraform output cloudfront_url
```

Visit the URL and:
1. Click "Sign up"
2. Create account with email and password
3. Verify email with code
4. Sign in

## Local Development

```bash
# 1. Setup
cd chatbot-app
./setup.sh

# 2. Configure AWS credentials
cp .env.example .env
# Edit with your AWS credentials

# 3. Start services
./start.sh
```

**Access**:
- Frontend: http://localhost:3000
- AgentCore Runtime: http://localhost:8000
- API Docs: http://localhost:8000/docs

**What runs locally**:
- Frontend (Next.js)
- AgentCore Runtime (Strands Agent)
- Local Tools (5 tools)
- Built-in Tools (Code Interpreter, Browser via AWS API)

**Requires cloud** (not available locally):
- AgentCore Gateway
- AgentCore Memory (uses local file storage instead)

**Note**: Local mode runs AgentCore Runtime in a container but still uses AWS Bedrock API for model calls and built-in tools.

## Post-Deployment Configuration

### Enable Gateway Tools

Tools are disabled by default. Enable via UI:
1. Sign in to application
2. Click gear icon -> Settings
3. Navigate to "Gateway Tools" section
4. Toggle desired tools ON
5. Click "Save"

### Verify Deployment

```bash
# Run end-to-end API test
./infra/scripts/test-api.sh

# Also invoke Code Agent and complete the Research approval flow
RUN_MODEL_SMOKE=1 ./infra/scripts/test-api.sh

# Expected output:
# Cognito login: OK
# Gateway tools/list: OK
# Orchestrator invoke: OK
```

## Troubleshooting

### Container Build Failures

```bash
# Check CodeBuild logs
aws logs tail /aws/codebuild/agentcore-runtime-build --follow
```

### Runtime Execution Errors

```bash
# Check AgentCore Runtime logs
aws logs tail /aws/bedrock-agentcore/runtimes/your-runtime-arn --follow
```

### Gateway Connection Issues

```bash
# Verify gateway deployment
aws bedrock-agentcore list-gateways

# Check gateway targets
aws bedrock-agentcore list-gateway-targets \
  --gateway-id your-gateway-id
```

### Terraform State Issues

```bash
# Re-initialize backend (safe, no data loss)
./infra/scripts/deploy.sh init

# Force unlock if state is locked
cd infra/environments/dev
terraform force-unlock LOCK-ID
```

### Migrating from DynamoDB-based locking

Older deployments used a DynamoDB `${project}-tflock` table for state locking. This repo now uses S3 native locking (`use_lockfile = true`), which requires Terraform >= 1.11. Existing deployments should cut over in two phases.

**Phase 1 — dual-lock (all operators upgrade to TF >= 1.11):**

Temporarily edit `infra/environments/dev/backend.tf` locally to keep both locks active while the team upgrades:

```hcl
backend "s3" {
  key            = "dev/terraform.tfstate"
  encrypt        = true
  use_lockfile   = true
  dynamodb_table = "strands-agent-chatbot-tflock"  # temporary, keep during rollout
}
```

Run `terraform init -reconfigure` in `infra/environments/dev/`. Confirm `plan`/`apply` works. Leave this in place until every operator is on TF >= 1.11.

**Phase 2 — cutover (drop DynamoDB):**

1. Pull the new shipped `backend.tf` (S3-only, no `dynamodb_table`).
2. `./infra/scripts/deploy.sh init` — runs `terraform init -reconfigure` against the existing state.
3. `cd infra/bootstrap && terraform init && terraform apply` — this destroys `aws_dynamodb_table.tflock`. No manual `aws dynamodb delete-table` call is required.

### Rolling back state

The bootstrap bucket has versioning enabled, so any prior `terraform.tfstate` object version can be restored in place:

```bash
# List prior versions of the state object
aws s3api list-object-versions \
  --bucket "${PROJECT_NAME}-tfstate-${ACCOUNT_ID}-us-east-1" \
  --prefix dev/terraform.tfstate

# Restore a specific versionId as the current object
aws s3api copy-object \
  --bucket "${PROJECT_NAME}-tfstate-${ACCOUNT_ID}-us-east-1" \
  --key dev/terraform.tfstate \
  --copy-source "${PROJECT_NAME}-tfstate-${ACCOUNT_ID}-us-east-1/dev/terraform.tfstate?versionId=VERSION_ID"

# Confirm drift against live infra
cd infra/environments/dev && terraform plan
```

### Local Development Issues

```bash
# Check AgentCore Runtime logs
docker logs -f agentcore

# Common issues:
# - Port 8000 already in use (kill existing process or change port)
# - Port 3000 already in use (kill existing process)
# - AWS credentials not configured (run aws configure)
# - Bedrock access denied (check IAM permissions)
# - AgentCore not enabled in region (contact AWS support)
```

## Updating Deployment

### Update Frontend or Runtime

```bash
# Update frontend only (rebuilds container if source changed)
./infra/scripts/deploy.sh apply -target=module.chat

# Update orchestrator runtime only
./infra/scripts/deploy.sh apply -target=module.runtime_orchestrator

# Update all (only changed modules rebuild)
./infra/scripts/deploy.sh apply
```

Terraform uses source file hashing for change detection. If source files haven't changed, `terraform apply` produces no changes (no forced redeployment).

### Update Gateway Lambda Functions

```bash
./infra/scripts/deploy.sh apply -target=module.gateway
```

### Update Tool Configuration

```bash
# Edit tool config
vim chatbot-app/frontend/src/config/tools-config.json

# Redeploy frontend
./infra/scripts/deploy.sh apply -target=module.chat
```

## Cleanup

### Remove All Components

```bash
./infra/scripts/deploy.sh destroy
```

### Remove Individual Components

```bash
# Remove specific modules
./infra/scripts/deploy.sh destroy -target=module.runtime_research_agent
./infra/scripts/deploy.sh destroy -target=module.gateway
./infra/scripts/deploy.sh destroy -target=module.runtime_orchestrator
./infra/scripts/deploy.sh destroy -target=module.chat
```

### Clean ECR Repositories

```bash
# Delete ECR repositories (optional, after destroy)
aws ecr delete-repository \
  --repository-name strands-agent-chatbot/orchestrator \
  --force

aws ecr delete-repository \
  --repository-name strands-agent-chatbot/frontend \
  --force
```

## Security Best Practices

1. **Rotate Secrets Regularly**
   ```bash
   aws secretsmanager rotate-secret \
     --secret-id strands-agent-chatbot/mcp/tavily-api-key
   ```

2. **Enable WAF** on CloudFront for DDoS protection

3. **Review IAM Roles** quarterly to ensure least privilege

4. **Enable VPC Flow Logs** for network monitoring

5. **Use Cognito MFA** for admin users

## Infrastructure Details

The Terraform infrastructure is organized as:

```
infra/
+-- bootstrap/             # S3 state bucket (one-time)
+-- environments/dev/      # Root module wiring all components
+-- modules/
|   +-- auth               # Cognito (pool, clients, resource server)
|   +-- memory             # AgentCore Memory
|   +-- data               # DynamoDB tables
|   +-- runtime            # AgentCore Runtime (generic, supports all types)
|   +-- gateway            # AgentCore Gateway
|   +-- gateway-lambda-tool  # Lambda tool (for_each pattern)
|   +-- chat               # ECS Fargate + ALB + CloudFront
+-- scripts/deploy.sh      # Auto-bootstrap + terraform orchestrator
```

For detailed migration status and design decisions, see `infra/PHASE-PLAN.md`.

## Support

- **Architecture**: See README.md for architecture overview
- **AgentCore Details**: See AGENTCORE.md for AgentCore usage
- **Issues**: [GitHub Issues](https://github.com/aws-samples/sample-strands-agent-with-agentcore/issues)
