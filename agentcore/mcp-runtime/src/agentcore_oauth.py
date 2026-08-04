"""
AgentCore OAuth Helper for 3LO MCP Servers

Provides reusable OAuth/Identity functionality for MCP servers that need
per-user authentication via AgentCore Identity 3LO (Three-Legged OAuth).

Uses MCP elicit_url() protocol for seamless OAuth consent flow:
    from agentcore_oauth import OAuthHelper, get_token_with_elicitation

    oauth = OAuthHelper(
        provider_name="google-oauth-provider",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )

    # In your tool handler (receives ctx: Context from FastMCP)
    async def my_tool(ctx: Context):
        token = await get_token_with_elicitation(ctx, oauth, "Gmail")
        if token is None:
            return "Authorization was declined by the user."
        # Use token to call external API
"""
import os
import uuid
import logging
import boto3
from dataclasses import dataclass
from typing import List, Optional

from bedrock_agentcore.services.identity import IdentityClient
from bedrock_agentcore.runtime import BedrockAgentCoreContext

logger = logging.getLogger(__name__)


# ── Token Result ─────────────────────────────────────────────────────

@dataclass
class TokenResult:
    """Result from Identity API token retrieval.

    Either token is set (cache hit) or auth_url is set (consent needed).
    """
    token: Optional[str] = None
    auth_url: Optional[str] = None


# ── Callback URL Loading ─────────────────────────────────────────────────


def get_oauth_callback_url() -> str:
    """Load OAuth callback URL from SSM Parameter Store.

    After user completes consent, AgentCore redirects to this URL
    with a session_id query parameter. The frontend must then call
    CompleteResourceTokenAuth to finalize the token exchange.

    The SSM parameter /{project}/{env}/mcp/oauth2-callback-url is managed
    by ChatbotStack and updated automatically on each deployment.

    Returns:
        str: Full callback URL (includes /oauth-complete path)

    Raises:
        RuntimeError: If callback URL cannot be resolved
    """
    project_name = os.environ.get("PROJECT_NAME", "strands-agent-chatbot")
    environment = os.environ.get("ENVIRONMENT", "dev")
    region = os.environ.get("AWS_REGION", "us-west-2")
    ssm_param = f"/{project_name}/{environment}/mcp/oauth2-callback-url"

    try:
        ssm = boto3.client("ssm", region_name=region)
        response = ssm.get_parameter(Name=ssm_param)
        callback_url = response["Parameter"]["Value"].rstrip("/")
        logger.info(f"[OAuth] Callback URL from SSM: {callback_url}")
        return callback_url
    except Exception as e:
        logger.error(f"[OAuth] Failed to load callback URL from SSM ({ssm_param}): {e}")
        raise RuntimeError(
            f"OAuth callback URL not configured. "
            f"Configure SSM parameter {ssm_param} by deploying ChatbotStack."
        ) from e


# ── OAuth Helper Class ───────────────────────────────────────────────────

class OAuthHelper:
    """Helper class for OAuth token retrieval via AgentCore Identity.

    This class encapsulates the common pattern for getting OAuth tokens
    from AgentCore Token Vault. It handles:
    - Token retrieval from cache
    - Returning TokenResult with auth_url when consent is needed
    - Provider and scope configuration

    Usage:
        oauth = OAuthHelper(
            provider_name="google-oauth-provider",
            scopes=["https://www.googleapis.com/auth/gmail.modify"],
        )

        # In a tool handler with MCP Context:
        token = await get_token_with_elicitation(ctx, oauth, "Gmail")

    Attributes:
        provider_name: OAuth credential provider name registered in AgentCore
        scopes: List of OAuth scopes to request
    """

    def __init__(
        self,
        provider_name: str,
        scopes: List[str],
        region: Optional[str] = None,
    ):
        self.provider_name = provider_name
        self.scopes = scopes
        self.region = region or os.environ.get("AWS_REGION", "us-west-2")
        self.callback_url = get_oauth_callback_url()

        # Create IdentityClient once (boto3 client creation is expensive)
        self._identity_client = IdentityClient(self.region)

        logger.info(f"[OAuth] Initialized helper for provider: {provider_name}")
        logger.info(f"[OAuth] Scopes: {scopes}")
        logger.info(f"[OAuth] Callback URL: {self.callback_url}")

    async def get_access_token(self, force: bool = False, custom_state: Optional[str] = None) -> TokenResult:
        """Get OAuth access token from AgentCore Token Vault.

        This method bypasses the @requires_access_token decorator and directly
        calls the Identity API. This approach:
        1. Avoids SDK polling mechanism that blocks for up to 10 minutes
        2. Returns TokenResult with auth_url when consent is needed
        3. Solves the N+1 token request problem

        Args:
            force: When True, pass forceAuthentication=true to Identity so it
                always starts a new 3LO flow, even if a cached token exists.
                Use this after the provider returns 401/403 on a cached token
                (Vault cannot detect server-side revocations on its own).
            custom_state: Opaque string Identity echoes back to the callback
                URL. We pass the elicitation ID so the callback page can
                correlate the redirect without any browser-side storage.

        Returns:
            TokenResult: Contains either token (cache hit) or auth_url (consent needed)

        Raises:
            ValueError: When WorkloadAccessToken is not set in context
            RuntimeError: When Identity service returns unexpected response
        """
        # Get workload access token from context (set by AgentCoreContextMiddleware)
        workload_token = BedrockAgentCoreContext.get_workload_access_token()
        if not workload_token:
            raise ValueError(
                "WorkloadAccessToken not set in context. "
                "Ensure AgentCoreContextMiddleware is added to the app."
            )

        # Direct API call to get OAuth2 token
        try:
            kwargs = dict(
                resourceCredentialProviderName=self.provider_name,
                scopes=self.scopes,
                oauth2Flow="USER_FEDERATION",
                workloadIdentityToken=workload_token,
                resourceOauth2ReturnUrl=self.callback_url,
                forceAuthentication=force,
            )
            if custom_state:
                kwargs["customState"] = custom_state
            response = self._identity_client.dp_client.get_resource_oauth2_token(**kwargs)
        except Exception as e:
            logger.error(f"[OAuth] Failed to get token from Identity service: {e}")
            raise

        # Token cached in Token Vault? Return it directly
        if "accessToken" in response:
            logger.debug("[OAuth] Token retrieved from Token Vault (cache hit)")
            return TokenResult(token=response["accessToken"])

        # Need user consent? Return auth URL
        if "authorizationUrl" in response:
            auth_url = response["authorizationUrl"]
            logger.warning("[OAuth] User consent required - returning auth URL via elicitation")
            return TokenResult(auth_url=auth_url)

        # Unexpected response
        raise RuntimeError(
            f"Identity service returned neither accessToken nor authorizationUrl. "
            f"Response: {response}"
        )


# ── Elicitation Helper ───────────────────────────────────────────────────

async def get_token_with_elicitation(
    ctx,
    oauth: OAuthHelper,
    service_name: str,
    force: bool = False,
) -> Optional[str]:
    """Get OAuth token, using MCP elicit_url() if user consent is needed.

    This is the primary entry point for tool functions. It:
    1. Tries to get a cached token
    2. If consent is needed, calls ctx.elicit_url() to pause the tool
       and prompt the user via the MCP elicitation protocol
    3. After consent completes, retrieves the newly cached token

    Args:
        ctx: FastMCP Context (provides elicit_url())
        oauth: OAuthHelper instance for the service
        service_name: Human-readable name (e.g., "Gmail", "Google Calendar")
        force: Start a fresh 3LO flow even if a cached token exists. Used by
            call_with_oauth_retry() after the provider rejects a cached token.

    Returns:
        Access token string, or None if user declined/cancelled
    """
    from mcp.server.elicitation import AcceptedUrlElicitation

    # Generated before the token request so it can ride along as customState:
    # Identity echoes it onto the callback redirect, which is how the frontend
    # callback page correlates the redirect to this elicitation.
    elicitation_id = str(uuid.uuid4())
    result = await oauth.get_access_token(force=force, custom_state=elicitation_id)

    if result.token:
        return result.token

    if result.auth_url:
        elicit_result = await ctx.elicit_url(
            message=f"{service_name} authorization required",
            url=result.auth_url,
            elicitation_id=elicitation_id,
        )

        if isinstance(elicit_result, AcceptedUrlElicitation):
            # Token should be cached after user completed consent
            result2 = await oauth.get_access_token()
            return result2.token

    return None


# ── Auth-aware API call wrapper ──────────────────────────────────────────

async def call_with_oauth_retry(
    ctx,
    oauth: "OAuthHelper",
    service_name: str,
    api_call,
):
    """Run an API call with automatic re-auth on 401/403.

    AgentCore Token Vault cannot detect provider-side token revocations;
    a cached token may silently fail at the external API. When that happens,
    we force a fresh 3LO flow (forceAuthentication=True) and retry once.

    Args:
        ctx: FastMCP Context
        oauth: OAuthHelper for the service
        service_name: Human-readable name used in elicitation prompt
        api_call: async callable taking a single `access_token` argument and
            performing the provider API call (raises httpx.HTTPStatusError
            on HTTP errors).

    Returns:
        Whatever api_call returns, or the string
        "Authorization was declined by the user." if the user cancelled.

    Raises:
        Anything api_call raises other than 401/403 propagates unchanged.
    """
    import httpx

    token = await get_token_with_elicitation(ctx, oauth, service_name)
    if token is None:
        return "Authorization was declined by the user."

    try:
        return await api_call(token)
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (401, 403):
            raise
        logger.warning(
            f"[OAuth] {service_name} returned {e.response.status_code} on cached token; "
            f"forcing re-authentication"
        )

    token = await get_token_with_elicitation(ctx, oauth, service_name, force=True)
    if token is None:
        return "Authorization was declined by the user."
    return await api_call(token)
