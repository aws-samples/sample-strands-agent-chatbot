# Google OAuth Setup Guide

This guide walks through configuring Google OAuth for Gmail tool access via AgentCore 3LO (Three-Legged OAuth).

## Prerequisites

- Frontend + BFF deployed (CloudFront URL available)
- Cognito authentication stack deployed
- A Google Cloud project

## Step 1: Create Google OAuth Client

1. Go to [Google Cloud Console > Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials** > **OAuth client ID**
3. Select **Web application** as the application type
4. Give it a name (e.g., `Strands Agent Chatbot`)
5. Leave **Authorized redirect URIs** empty for now (you will add it in Step 4)
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

## Step 2: Enable Gmail API

1. Go to [Gmail API Library](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
2. Click **Enable**

## Step 3: Run the Deploy Script

From the repository root:

```bash
./infra/scripts/deploy.sh apply
```

When prompted, enter your Google OAuth Client ID and Client Secret:

```
Enter Google OAuth Client ID (or press Enter to skip): <your-client-id>
Enter Google OAuth Client Secret: <your-client-secret>
```

The script registers the credential provider with AgentCore and prints its
**provider callback URL** after Terraform completes:

```
OAuth Callback URIs (register in each provider's console):
  google-oauth-provider: https://bedrock-agentcore.us-west-2.amazonaws.com/identities/oauth2/callback/<provider-id>
```

## Step 4: Add Redirect URI to Google Cloud Console

1. Go back to [Google Cloud Console > Credentials](https://console.cloud.google.com/apis/credentials)
2. Click on the OAuth client you created in Step 1
3. Under **Authorized redirect URIs**, click **Add URI**
4. Paste the `google-oauth-provider` callback URL from the deploy output. It
   must match exactly, including the region and provider ID, with no trailing
   slash.
5. Click **Save**

Do not register the CloudFront `/oauth-complete` URL here. That URL is where
AgentCore returns the browser after provider authorization; Google redirects
to the AgentCore provider callback first.

## Step 5: Configure OAuth Consent Screen (If Not Done)

If your Google Cloud project hasn't configured the OAuth consent screen yet:

1. Go to [Google Auth Platform > Audience](https://console.cloud.google.com/auth/audience)
2. Select **External** user type (or **Internal** for Google Workspace)
3. Fill in the required fields (app name, user support email, developer contact)
4. Add the scope: `https://www.googleapis.com/auth/gmail.readonly`
5. Add every account that will test Gmail or Calendar under **Test users** if
   the app is in **Testing** status

## Troubleshooting

`Error 400: redirect_uri_mismatch` means the URI in Step 4 is missing or does
not exactly match the current AgentCore provider callback. Run the deploy
script again to print the current URI, or retrieve it directly:

```bash
infra/.deploy-venv/bin/python -c \
  "import boto3; print(boto3.client('bedrock-agentcore-control', region_name='us-west-2').get_oauth2_credential_provider(name='google-oauth-provider')['callbackUrl'])"
```

## Verification

After deployment, ask the chatbot to list recent Gmail messages. On first use,
select **Continue** in the authorization dialog and complete Google consent.
