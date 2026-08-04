"""
Tests for AgentCore OAuth helper — TokenResult, OAuthHelper.get_access_token,
get_token_with_elicitation, and call_with_oauth_retry.

External services (SSM, Identity API, MCP) are fully mocked.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================
# TokenResult
# ============================================================

class TestTokenResult:
    def test_import(self):
        from agentcore_oauth import TokenResult
        result = TokenResult(token="tok-123")
        assert result.token == "tok-123"
        assert result.auth_url is None

    def test_auth_url_result(self):
        from agentcore_oauth import TokenResult
        result = TokenResult(auth_url="https://consent.example.com/auth")
        assert result.token is None
        assert result.auth_url.startswith("https://")

    def test_default_fields_are_none(self):
        from agentcore_oauth import TokenResult
        result = TokenResult()
        assert result.token is None
        assert result.auth_url is None


# ============================================================
# get_oauth_callback_url
# ============================================================

class TestGetOAuthCallbackUrl:
    def test_loads_url_from_ssm(self):
        from agentcore_oauth import get_oauth_callback_url

        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "https://chatbot.example.com/oauth-complete"}
        }

        with patch("agentcore_oauth.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ssm
            url = get_oauth_callback_url()

        assert url == "https://chatbot.example.com/oauth-complete"

    def test_strips_trailing_slash(self):
        from agentcore_oauth import get_oauth_callback_url

        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "https://example.com/oauth-complete/"}
        }

        with patch("agentcore_oauth.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ssm
            url = get_oauth_callback_url()

        assert not url.endswith("/")

    def test_raises_runtime_error_on_ssm_failure(self):
        from agentcore_oauth import get_oauth_callback_url

        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = Exception("SSM not available")

        with patch("agentcore_oauth.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ssm
            with pytest.raises(RuntimeError, match="OAuth callback URL not configured"):
                get_oauth_callback_url()


# ============================================================
# OAuthHelper.get_access_token
# ============================================================

class TestOAuthHelperGetAccessToken:
    def _make_helper(self):
        with patch("agentcore_oauth.get_oauth_callback_url", return_value="https://cb.example.com"), \
             patch("agentcore_oauth.IdentityClient") as mock_identity_cls:
            from agentcore_oauth import OAuthHelper
            helper = OAuthHelper(
                provider_name="google-oauth-provider",
                scopes=["https://www.googleapis.com/auth/gmail.modify"],
            )
            helper._identity_client = mock_identity_cls.return_value
            return helper

    def test_returns_token_on_cache_hit(self):
        helper = self._make_helper()
        helper._identity_client.dp_client.get_resource_oauth2_token.return_value = {
            "accessToken": "cached-token-abc"
        }

        async def _run():
            with patch("agentcore_oauth.BedrockAgentCoreContext") as mock_ctx:
                mock_ctx.get_workload_access_token.return_value = "workload-tok"
                return await helper.get_access_token()

        result = asyncio.run(_run())
        assert result.token == "cached-token-abc"
        assert result.auth_url is None

    def test_returns_auth_url_when_consent_needed(self):
        helper = self._make_helper()
        helper._identity_client.dp_client.get_resource_oauth2_token.return_value = {
            "authorizationUrl": "https://accounts.google.com/o/oauth2/auth?..."
        }

        async def _run():
            with patch("agentcore_oauth.BedrockAgentCoreContext") as mock_ctx:
                mock_ctx.get_workload_access_token.return_value = "workload-tok"
                return await helper.get_access_token()

        result = asyncio.run(_run())
        assert result.token is None
        from urllib.parse import urlparse
        parsed = urlparse(result.auth_url)
        assert parsed.netloc == "accounts.google.com"

    def test_raises_when_workload_token_missing(self):
        helper = self._make_helper()

        async def _run():
            with patch("agentcore_oauth.BedrockAgentCoreContext") as mock_ctx:
                mock_ctx.get_workload_access_token.return_value = None
                await helper.get_access_token()

        with pytest.raises(ValueError, match="WorkloadAccessToken not set"):
            asyncio.run(_run())

    def test_raises_on_unexpected_identity_response(self):
        helper = self._make_helper()
        helper._identity_client.dp_client.get_resource_oauth2_token.return_value = {
            "unexpectedKey": "value"
        }

        async def _run():
            with patch("agentcore_oauth.BedrockAgentCoreContext") as mock_ctx:
                mock_ctx.get_workload_access_token.return_value = "workload-tok"
                await helper.get_access_token()

        with pytest.raises(RuntimeError, match="neither accessToken nor authorizationUrl"):
            asyncio.run(_run())

    def test_force_flag_passed_to_identity_api(self):
        helper = self._make_helper()
        helper._identity_client.dp_client.get_resource_oauth2_token.return_value = {
            "accessToken": "fresh-token"
        }

        async def _run():
            with patch("agentcore_oauth.BedrockAgentCoreContext") as mock_ctx:
                mock_ctx.get_workload_access_token.return_value = "workload-tok"
                await helper.get_access_token(force=True)

        asyncio.run(_run())
        call_kwargs = helper._identity_client.dp_client.get_resource_oauth2_token.call_args.kwargs
        assert call_kwargs.get("forceAuthentication") is True


# ============================================================
# get_token_with_elicitation
# ============================================================

class TestGetTokenWithElicitation:
    def _make_oauth(self):
        with patch("agentcore_oauth.get_oauth_callback_url", return_value="https://cb.example.com"), \
             patch("agentcore_oauth.IdentityClient"):
            from agentcore_oauth import OAuthHelper
            return OAuthHelper("provider", ["scope"])

    def test_returns_token_directly_on_cache_hit(self):
        oauth = self._make_oauth()
        from agentcore_oauth import TokenResult, get_token_with_elicitation

        oauth.get_access_token = AsyncMock(return_value=TokenResult(token="direct-token"))

        # Mock mcp.server.elicitation so the import inside get_token_with_elicitation works
        mock_mcp = MagicMock()
        mock_mcp.server.elicitation.AcceptedUrlElicitation = type("AcceptedUrlElicitation", (), {})
        with patch.dict("sys.modules", {"mcp": mock_mcp, "mcp.server": mock_mcp.server, "mcp.server.elicitation": mock_mcp.server.elicitation}):
            token = asyncio.run(get_token_with_elicitation(MagicMock(), oauth, "Gmail"))

        assert token == "direct-token"

    def test_returns_none_when_user_declines(self):
        oauth = self._make_oauth()
        from agentcore_oauth import TokenResult, get_token_with_elicitation

        oauth.get_access_token = AsyncMock(return_value=TokenResult(auth_url="https://auth.url"))

        AcceptedClass = type("AcceptedUrlElicitation", (), {})
        mock_mcp = MagicMock()
        mock_mcp.server.elicitation.AcceptedUrlElicitation = AcceptedClass

        ctx = MagicMock()
        ctx.elicit_url = AsyncMock(return_value=MagicMock())  # not an AcceptedUrlElicitation

        with patch.dict("sys.modules", {"mcp": mock_mcp, "mcp.server": mock_mcp.server, "mcp.server.elicitation": mock_mcp.server.elicitation}):
            token = asyncio.run(get_token_with_elicitation(ctx, oauth, "Gmail"))

        assert token is None

    def test_retrieves_token_after_elicitation_accepted(self):
        oauth = self._make_oauth()
        from agentcore_oauth import TokenResult, get_token_with_elicitation

        AcceptedClass = type("AcceptedUrlElicitation", (), {})
        mock_mcp = MagicMock()
        mock_mcp.server.elicitation.AcceptedUrlElicitation = AcceptedClass

        call_count = 0

        seen_custom_states = []

        async def mock_get_access_token(force=False, custom_state=None):
            nonlocal call_count
            call_count += 1
            seen_custom_states.append(custom_state)
            if call_count == 1:
                return TokenResult(auth_url="https://consent.url")
            return TokenResult(token="post-consent-token")

        oauth.get_access_token = mock_get_access_token

        ctx = MagicMock()
        accepted = AcceptedClass()
        ctx.elicit_url = AsyncMock(return_value=accepted)

        with patch.dict("sys.modules", {"mcp": mock_mcp, "mcp.server": mock_mcp.server, "mcp.server.elicitation": mock_mcp.server.elicitation}):
            token = asyncio.run(get_token_with_elicitation(ctx, oauth, "Gmail"))

        assert token == "post-consent-token"
        # The elicitation ID given to elicit_url must equal the customState of
        # the auth-URL request: Identity echoes it back to the callback page,
        # which is the only correlation channel between popup and elicitation.
        elicit_kwargs = ctx.elicit_url.call_args.kwargs
        assert seen_custom_states[0] == elicit_kwargs["elicitation_id"]


# ============================================================
# call_with_oauth_retry
# ============================================================

class TestCallWithOAuthRetry:
    def _make_oauth(self):
        with patch("agentcore_oauth.get_oauth_callback_url", return_value="https://cb.example.com"), \
             patch("agentcore_oauth.IdentityClient"):
            from agentcore_oauth import OAuthHelper
            return OAuthHelper("provider", ["scope"])

    def test_returns_api_result_on_success(self):
        from agentcore_oauth import call_with_oauth_retry

        oauth = self._make_oauth()
        api_call = AsyncMock(return_value={"data": "ok"})

        async def _run():
            with patch("agentcore_oauth.get_token_with_elicitation", AsyncMock(return_value="tok")):
                return await call_with_oauth_retry(MagicMock(), oauth, "Gmail", api_call)

        assert asyncio.run(_run()) == {"data": "ok"}

    def test_retries_once_on_401(self):
        import httpx
        from agentcore_oauth import call_with_oauth_retry

        oauth = self._make_oauth()
        call_count = 0

        async def flaky_api(token):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock()
                resp.status_code = 401
                raise httpx.HTTPStatusError("401", request=MagicMock(), response=resp)
            return "success after retry"

        async def _run():
            with patch("agentcore_oauth.get_token_with_elicitation", AsyncMock(return_value="tok")):
                return await call_with_oauth_retry(MagicMock(), oauth, "Gmail", flaky_api)

        assert asyncio.run(_run()) == "success after retry"
        assert call_count == 2

    def test_returns_declined_message_when_token_is_none(self):
        from agentcore_oauth import call_with_oauth_retry

        oauth = self._make_oauth()

        async def _run():
            with patch("agentcore_oauth.get_token_with_elicitation", AsyncMock(return_value=None)):
                return await call_with_oauth_retry(MagicMock(), oauth, "Gmail", AsyncMock())

        assert asyncio.run(_run()) == "Authorization was declined by the user."

    def test_non_auth_errors_propagate_unchanged(self):
        import httpx
        from agentcore_oauth import call_with_oauth_retry

        oauth = self._make_oauth()

        async def api_500(token):
            resp = MagicMock()
            resp.status_code = 500
            raise httpx.HTTPStatusError("500", request=MagicMock(), response=resp)

        async def _run():
            with patch("agentcore_oauth.get_token_with_elicitation", AsyncMock(return_value="tok")):
                await call_with_oauth_retry(MagicMock(), oauth, "Gmail", api_500)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(_run())
