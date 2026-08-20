"""Native Strands MCP clients for Gateway and AgentCore Runtime endpoints."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.client.streamable_http import streamable_http_client
from strands.tools.mcp import MCPClient

logger = logging.getLogger(__name__)


class BearerAuth(httpx.Auth):
    """Add a bearer token to every MCP transport request."""

    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


@asynccontextmanager
async def _streamable_http_transport(url: str, auth: httpx.Auth | None):
    timeout = httpx.Timeout(30.0, read=300.0)
    async with httpx.AsyncClient(
        auth=auth,
        timeout=timeout,
        follow_redirects=True,
    ) as http_client, streamable_http_client(
        url,
        http_client=http_client,
        terminate_on_close=False,
    ) as streams:
        yield streams


def canonical_mcp_tool_name(name: str) -> str:
    """Return the schema name from a namespaced Gateway tool name."""
    return name.split("___", 1)[1] if "___" in name else name


def _enabled_tool_names(enabled_tool_ids: list[str]) -> set[str]:
    names = set()
    for tool_id in enabled_tool_ids:
        for prefix in ("gateway_", "mcp_"):
            if tool_id.startswith(prefix):
                tool_id = tool_id.removeprefix(prefix)
                break
        names.add(tool_id)
    return names


def _native_tool_filters(enabled_tool_ids: list[str]) -> dict[str, list[Any]]:
    """Build Strands MCPClient filters from Connector-enabled tool IDs."""
    allowed_names = _enabled_tool_names(enabled_tool_ids)

    def is_enabled(tool) -> bool:
        server_name = tool.mcp_tool.name
        return (
            server_name in allowed_names
            or canonical_mcp_tool_name(server_name) in allowed_names
        )

    return {"allowed": [is_enabled]}


def _registry_endpoint(source: str) -> str | None:
    from registry.client import get_registry_client

    client = get_registry_client()
    if not client:
        logger.debug("Registry client not available for MCP source %s", source)
        return None
    return client.get_mcp_endpoint(source)


def get_gateway_url() -> str | None:
    return _registry_endpoint("gateway")


def get_runtime_url() -> str | None:
    return _registry_endpoint("mcp")


def _make_bearer_auth(auth_token: str | None) -> httpx.Auth | None:
    if not auth_token:
        return None
    return BearerAuth(auth_token.removeprefix("Bearer "))


def create_mcp_client(
    endpoint_url: str,
    auth_token: str | None = None,
    enabled_tool_ids: list[str] | None = None,
    elicitation_bridge=None,
) -> MCPClient:
    """Create a native Strands MCPClient for a Streamable HTTP endpoint."""
    auth = _make_bearer_auth(auth_token)
    return MCPClient(
        lambda: _streamable_http_transport(endpoint_url, auth),
        tool_filters=(
            _native_tool_filters(enabled_tool_ids)
            if enabled_tool_ids is not None
            else None
        ),
        elicitation_callback=(
            elicitation_bridge.elicitation_callback
            if elicitation_bridge
            else None
        ),
    )


def create_gateway_mcp_client(
    gateway_url: str | None = None,
    auth_token: str | None = None,
    enabled_tool_ids: list[str] | None = None,
    elicitation_bridge=None,
) -> MCPClient | None:
    gateway_url = gateway_url or get_gateway_url()
    if not gateway_url:
        logger.debug("Gateway URL is not available")
        return None
    return create_mcp_client(
        gateway_url,
        auth_token=auth_token,
        enabled_tool_ids=enabled_tool_ids,
        elicitation_bridge=elicitation_bridge,
    )


def create_runtime_mcp_client(
    runtime_url: str | None = None,
    auth_token: str | None = None,
    enabled_tool_ids: list[str] | None = None,
    elicitation_bridge=None,
) -> MCPClient | None:
    runtime_url = runtime_url or get_runtime_url()
    if not runtime_url:
        logger.debug("MCP Runtime URL is not available")
        return None
    if not auth_token:
        logger.debug("MCP Runtime requires an authenticated user")
        return None
    return create_mcp_client(
        runtime_url,
        auth_token=auth_token,
        enabled_tool_ids=enabled_tool_ids,
        elicitation_bridge=elicitation_bridge,
    )


MCP_ENABLED = os.environ.get("GATEWAY_MCP_ENABLED", "true").lower() == "true"


def get_gateway_client_if_enabled(**kwargs) -> MCPClient | None:
    if not MCP_ENABLED:
        return None
    return create_gateway_mcp_client(**kwargs)


def get_runtime_client_if_enabled(**kwargs) -> MCPClient | None:
    if not MCP_ENABLED:
        return None
    return create_runtime_mcp_client(**kwargs)
