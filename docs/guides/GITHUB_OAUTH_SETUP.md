# GitHub OAuth Setup Guide

This guide configures GitHub tool access through AgentCore 3LO
(Three-Legged OAuth).

## Prerequisites

- The application has been deployed at least once
- A GitHub account that can create an OAuth App

## Step 1: Create a GitHub OAuth App

1. Open [GitHub Developer settings > OAuth Apps](https://github.com/settings/developers).
2. Select **New OAuth App**.
3. Enter an application name.
4. Set **Homepage URL** to the deployed CloudFront URL.
5. Use a temporary valid URL for **Authorization callback URL**, such as the
   CloudFront URL. The AgentCore callback replaces it in Step 3.
6. Select **Register application**.
7. Copy the **Client ID**, then generate and copy a **Client secret**.

Use an OAuth App, not a GitHub App. The AgentCore provider in this repository
uses GitHub's OAuth authorization and token endpoints.

## Step 2: Configure and Deploy

Run:

```bash
./infra/scripts/deploy.sh apply
```

Enter the GitHub Client ID and Client Secret when prompted. The credentials
are stored in AWS Secrets Manager. At the end, the script prints:

```text
OAuth Callback URIs (register in each provider's console):
  github-oauth-provider: https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<provider-id>
```

## Step 3: Register the AgentCore Callback

1. Return to [GitHub Developer settings > OAuth Apps](https://github.com/settings/developers).
2. Select the OAuth App.
3. Replace **Authorization callback URL** with the exact
   `github-oauth-provider` URL printed by the deploy script.
4. Select **Update application**.

Do not use the CloudFront `/oauth-complete` URL as the GitHub callback.
GitHub first returns to the AgentCore provider callback. AgentCore then
returns the browser to `/oauth-complete`.

GitHub OAuth Apps have one callback URL field. If separate environments have
different AgentCore providers, create a separate OAuth App for each
environment.

## Retrieve the Current Callback

```bash
infra/.deploy-venv/bin/python -c \
  "import boto3; print(boto3.client('bedrock-agentcore-control', region_name='us-west-2').get_oauth2_credential_provider(name='github-oauth-provider')['callbackUrl'])"
```

The callback remains stable while the AgentCore OAuth provider exists.
Terraform protects the provider with `prevent_destroy` because recreating it
changes the callback ID.

## Verification

Ask the chatbot to access a GitHub repository or list issues. On first use,
select **Continue** in the authorization dialog and approve the GitHub OAuth
request. The pending tool call resumes after authorization completes.
