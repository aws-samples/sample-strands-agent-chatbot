"""Tests for native Strands MCP filtering and skill execution."""

import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import Mock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


class TestMCPTransport:
    @pytest.mark.asyncio
    async def test_uses_preconfigured_http_client(self, monkeypatch):
        from agent.mcp import client as mcp_client

        captured = {}
        expected_streams = (Mock(), Mock(), Mock())

        class FakeHttpClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

        def fake_async_client(**kwargs):
            captured["client_kwargs"] = kwargs
            return FakeHttpClient()

        @asynccontextmanager
        async def fake_streamable_http_client(url, *, http_client):
            captured["url"] = url
            captured["http_client"] = http_client
            yield expected_streams

        monkeypatch.setattr(mcp_client.httpx, "AsyncClient", fake_async_client)
        monkeypatch.setattr(
            mcp_client,
            "streamable_http_client",
            fake_streamable_http_client,
        )

        auth = mcp_client.BearerAuth("token")
        async with mcp_client._streamable_http_transport(
            "https://gateway.example.com/mcp",
            auth,
        ) as streams:
            assert streams is expected_streams

        assert captured["url"] == "https://gateway.example.com/mcp"
        assert captured["client_kwargs"]["auth"] is auth
        assert captured["client_kwargs"]["follow_redirects"] is True
        assert isinstance(captured["client_kwargs"]["timeout"], httpx.Timeout)


class TestNativeToolFilters:
    def test_canonicalizes_gateway_names(self):
        from agent.mcp.client import canonical_mcp_tool_name

        assert canonical_mcp_tool_name("web-search___ddg_web_search") == "ddg_web_search"
        assert canonical_mcp_tool_name("search_emails") == "search_emails"

    def test_matches_connector_ids_without_mutating_tool(self):
        from agent.mcp.client import _native_tool_filters

        tool = Mock()
        tool.mcp_tool.name = "web-search___ddg_web_search"
        tool.tool_name = "web-search___ddg_web_search"
        original_name = tool.tool_name

        filters = _native_tool_filters([
            "gateway_ddg_web_search",
            "mcp_search_emails",
        ])
        predicate = filters["allowed"][0]

        assert predicate(tool) is True
        assert tool.tool_name == original_name

        other = Mock()
        other.mcp_tool.name = "weather___get_today_weather"
        assert predicate(other) is False

    def test_gateway_client_uses_strands_tool_filters(self):
        from agent.mcp import client as mcp_client

        bridge = Mock()
        with patch.object(mcp_client, "MCPClient") as client_class:
            mcp_client.create_gateway_mcp_client(
                gateway_url="https://gateway.example.com/mcp",
                auth_token="Bearer token",
                enabled_tool_ids=["gateway_ddg_web_search"],
                elicitation_bridge=bridge,
            )

        kwargs = client_class.call_args.kwargs
        assert kwargs["tool_filters"]["allowed"]
        assert kwargs["elicitation_callback"] is bridge.elicitation_callback

    def test_runtime_client_uses_direct_endpoint_and_requires_auth(self):
        from agent.mcp import client as mcp_client

        with patch.object(mcp_client, "create_mcp_client") as create_client:
            assert mcp_client.create_runtime_mcp_client(
                runtime_url="https://runtime.example.com/invocations",
            ) is None

            mcp_client.create_runtime_mcp_client(
                runtime_url="https://runtime.example.com/invocations",
                auth_token="Bearer user-token",
                enabled_tool_ids=["mcp_search_emails"],
            )

        create_client.assert_called_once_with(
            "https://runtime.example.com/invocations",
            auth_token="Bearer user-token",
            enabled_tool_ids=["mcp_search_emails"],
            elicitation_bridge=None,
        )


class TestSkillExecutorMCPPath:
    def _make_mcp_tool(self):
        tool = Mock()
        tool.tool_name = "tavily___tavily_search"
        tool.mcp_client = Mock()
        tool.mcp_client.call_tool_sync = Mock(return_value={
            "content": [{"text": "search results"}]
        })
        tool.mcp_tool = Mock()
        tool.mcp_tool.name = "tavily___tavily_search"
        return tool

    def _make_tool_context(self):
        ctx = Mock()
        ctx.tool_use = {"toolUseId": "test-123"}
        ctx.invocation_state = {"session_id": "session-abc"}
        return ctx

    @patch("skill.skill_tools._registry")
    def test_execute_tool_uses_canonical_name_and_calls_original_mcp_name(
        self,
        mock_registry,
    ):
        from skill.skill_tools import _execute_tool

        tool = self._make_mcp_tool()
        mock_registry.get_tools.return_value = [tool]

        result = _execute_tool(
            tool_context=self._make_tool_context(),
            skill_name="tavily-search",
            tool_name="tavily_search",
            tool_input={"query": "test"},
        )

        assert result == "search results"
        tool.mcp_client.call_tool_sync.assert_called_once_with(
            tool_use_id="test-123",
            name="tavily___tavily_search",
            arguments={"query": "test"},
            read_timeout_seconds=timedelta(seconds=360),
        )

    @patch("skill.skill_tools._registry")
    def test_execute_tool_calls_local_tool(self, mock_registry):
        from skill.skill_tools import _execute_tool

        tool = Mock()
        tool.tool_name = "web_search"
        tool._tool_func = Mock(return_value="local result")
        tool._metadata = Mock()
        tool._metadata._context_param = None
        del tool.mcp_client
        del tool.mcp_tool
        mock_registry.get_tools.return_value = [tool]

        result = _execute_tool(
            tool_context=self._make_tool_context(),
            skill_name="web-search",
            tool_name="web_search",
            tool_input={"query": "test"},
        )

        assert result == "local result"
        tool._tool_func.assert_called_once_with(query="test")

    @patch("skill.skill_tools._registry")
    def test_execute_tool_reports_mcp_call_failure(self, mock_registry):
        from skill.skill_tools import _execute_tool

        tool = self._make_mcp_tool()
        tool.mcp_client.call_tool_sync.side_effect = Exception("Connection closed")
        mock_registry.get_tools.return_value = [tool]

        result = _execute_tool(
            tool_context=self._make_tool_context(),
            skill_name="tavily-search",
            tool_name="tavily_search",
            tool_input={"query": "test"},
        )

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Connection closed" in parsed["error"]
