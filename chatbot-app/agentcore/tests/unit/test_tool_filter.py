"""
Tests for unified tool_filter module.
"""

import pytest
import re
from unittest.mock import Mock, patch, MagicMock

from agent.tool_filter import (
    ToolFilterRegistry,
    FilteredToolResult,
    ToolFilters,
    filter_tools,
    get_tool_filter_registry,
)


class TestToolFilters:
    """Tests for ToolFilters dataclass."""

    def test_empty_filters(self):
        """Empty filters should allow all tools."""
        filters = ToolFilters()
        assert filters.allowed is None
        assert filters.rejected is None

    def test_allowed_filters(self):
        """Allowed patterns should be settable."""
        filters = ToolFilters(allowed=["calculator", "gateway_*"])
        assert filters.allowed == ["calculator", "gateway_*"]

    def test_rejected_filters(self):
        """Rejected patterns should be settable."""
        filters = ToolFilters(rejected=["agentcore_*"])
        assert filters.rejected == ["agentcore_*"]


class TestFilteredToolResult:
    """Tests for FilteredToolResult dataclass."""

    def test_default_values(self):
        """Default values should be empty collections."""
        result = FilteredToolResult()
        assert result.tools == []
        assert result.metadata == {}
        assert result.clients == {}
        assert result.validation_errors == []
        assert result.tool_ids_by_source == {}

    def test_with_values(self):
        """Should accept values on construction."""
        mock_tool = Mock()
        mock_client = Mock()

        result = FilteredToolResult(
            tools=[mock_tool],
            metadata={"key": "value"},
            clients={"gateway": mock_client},
            validation_errors=["error1"],
            tool_ids_by_source={"local": ["calculator"]},
        )

        assert result.tools == [mock_tool]
        assert result.metadata == {"key": "value"}
        assert result.clients == {"gateway": mock_client}
        assert result.validation_errors == ["error1"]
        assert result.tool_ids_by_source == {"local": ["calculator"]}


class TestToolFilterRegistry:
    """Tests for ToolFilterRegistry class."""

    @pytest.fixture
    def mock_registry(self):
        """Create a mock local tool registry."""
        return {
            "calculator": Mock(name="calculator_tool"),
            "fetch_url_content": Mock(name="fetch_tool"),
            "create_visualization": Mock(name="viz_tool"),
        }

    @pytest.fixture
    def registry(self, mock_registry):
        """Create ToolFilterRegistry with mocked dependencies."""
        return ToolFilterRegistry(
            local_registry=mock_registry,
            gateway_client_factory=None,
            a2a_tool_factory=None,
        )

    def test_classify_local_tool(self, registry):
        """Should classify local tools correctly."""
        assert registry.classify_tool_id("calculator") == "local"
        assert registry.classify_tool_id("fetch_url_content") == "local"

    def test_classify_gateway_tool(self, registry):
        """Should classify gateway tools by prefix."""
        assert registry.classify_tool_id("gateway_wikipedia_search") == "gateway"
        assert registry.classify_tool_id("gateway_arxiv_search") == "gateway"

    def test_classify_a2a_tool(self, registry):
        """Should classify A2A tools by prefix."""
        assert registry.classify_tool_id("agentcore_research-agent") == "a2a"

    def test_classify_unknown_tool(self, registry):
        """Should return 'unknown' for unrecognized tools."""
        assert registry.classify_tool_id("nonexistent_tool") == "unknown"

    def test_filter_empty_list(self, registry):
        """Empty enabled_tool_ids should return empty result."""
        result = registry.filter_tools(enabled_tool_ids=[])
        assert result.tools == []
        assert result.validation_errors == []

    def test_filter_none_list(self, registry):
        """None enabled_tool_ids should return empty result."""
        result = registry.filter_tools(enabled_tool_ids=None)
        assert result.tools == []

    def test_filter_local_tools(self, registry, mock_registry):
        """Should filter local tools correctly."""
        result = registry.filter_tools(
            enabled_tool_ids=["calculator", "fetch_url_content"]
        )

        assert len(result.tools) == 2
        assert mock_registry["calculator"] in result.tools
        assert mock_registry["fetch_url_content"] in result.tools
        assert result.tool_ids_by_source["local"] == ["calculator", "fetch_url_content"]

    def test_filter_unknown_tool_adds_error(self, registry):
        """Unknown tool should add validation error."""
        result = registry.filter_tools(enabled_tool_ids=["nonexistent_tool"])

        assert result.tools == []
        assert len(result.validation_errors) == 1
        assert "nonexistent_tool" in result.validation_errors[0]

    def test_pattern_matching_exact(self, registry):
        """Exact string pattern should match."""
        assert registry._matches_pattern("calculator", "calculator") is True
        assert registry._matches_pattern("calculator", "other") is False

    def test_pattern_matching_wildcard(self, registry):
        """Wildcard pattern should match."""
        assert registry._matches_pattern("gateway_wiki", "gateway_*") is True
        assert registry._matches_pattern("local_tool", "gateway_*") is False

    def test_pattern_matching_regex(self, registry):
        """Regex pattern should match."""
        pattern = re.compile(r"gateway_.*_search")
        assert registry._matches_pattern("gateway_wiki_search", pattern) is True
        assert registry._matches_pattern("gateway_wiki", pattern) is False

    def test_pattern_matching_callable(self, registry):
        """Callable pattern should match."""
        is_short = lambda x: len(x) < 10
        assert registry._matches_pattern("short", is_short) is True
        assert registry._matches_pattern("very_long_tool_name", is_short) is False

    def test_tool_filters_allowed(self, registry, mock_registry):
        """Should filter by allowed patterns."""
        filters = ToolFilters(allowed=["calculator"])
        result = registry.filter_tools(
            enabled_tool_ids=["calculator", "fetch_url_content"],
            filters=filters,
        )

        assert len(result.tools) == 1
        assert mock_registry["calculator"] in result.tools

    def test_tool_filters_rejected(self, registry, mock_registry):
        """Should filter by rejected patterns."""
        filters = ToolFilters(rejected=["fetch_*"])
        result = registry.filter_tools(
            enabled_tool_ids=["calculator", "fetch_url_content"],
            filters=filters,
        )

        assert len(result.tools) == 1
        assert mock_registry["calculator"] in result.tools

    def test_log_prefix(self, registry):
        """Log prefix should be passed through."""
        # Just verify it doesn't crash with log_prefix
        result = registry.filter_tools(
            enabled_tool_ids=["calculator"],
            log_prefix="[TestAgent]"
        )
        assert len(result.tools) == 1

    def test_user_federation_can_be_disabled(self, mock_registry):
        """Machine clients must not load per-user 3LO MCP tools."""
        gateway_factory = Mock()
        registry = ToolFilterRegistry(
            local_registry=mock_registry,
            gateway_client_factory=gateway_factory,
        )

        result = registry.filter_tools(
            enabled_tool_ids=["mcp_search_emails"],
            auth_token="Bearer machine-token",
            allow_user_federation=False,
        )

        gateway_factory.assert_not_called()
        assert result.tools == []
        assert "disabled for this client" in result.validation_errors[0]

    def test_user_federation_requires_user_token(self, mock_registry):
        """Federated tools must not be sent to Gateway without user auth."""
        gateway_factory = Mock()
        registry = ToolFilterRegistry(
            local_registry=mock_registry,
            gateway_client_factory=gateway_factory,
        )

        result = registry.filter_tools(
            enabled_tool_ids=["mcp_search_emails"],
            allow_user_federation=True,
        )

        gateway_factory.assert_not_called()
        assert result.tools == []
        assert "authenticated user" in result.validation_errors[0]


class TestGatewayTools:
    """Tests for Gateway MCP tool filtering."""

    @pytest.fixture
    def mock_gateway_client(self):
        """Create a mock Gateway MCP client."""
        return Mock(name="gateway_client")

    @pytest.fixture
    def mock_gateway_factory(self, mock_gateway_client):
        """Create a mock Gateway client factory."""
        def factory(enabled_tool_ids=None, **kwargs):
            if enabled_tool_ids:
                return mock_gateway_client
            return None
        return factory

    @pytest.fixture
    def registry_with_gateway(self, mock_gateway_factory):
        """Create registry with gateway support."""
        return ToolFilterRegistry(
            local_registry={},
            gateway_client_factory=mock_gateway_factory,
            a2a_tool_factory=None,
        )

    def test_filter_gateway_tools(self, registry_with_gateway, mock_gateway_client):
        """Should load gateway tools via factory."""
        result = registry_with_gateway.filter_tools(
            enabled_tool_ids=["gateway_wikipedia_search", "gateway_arxiv_search"]
        )

        assert len(result.tools) == 1  # Gateway client is added once
        assert mock_gateway_client in result.tools
        assert result.clients["gateway"] == mock_gateway_client
        assert "gateway_wikipedia_search" in result.tool_ids_by_source["gateway"]

    def test_gateway_factory_returns_none(self):
        """Should handle gateway factory returning None gracefully."""
        def failing_factory(enabled_tool_ids=None, **kwargs):
            return None

        registry = ToolFilterRegistry(
            local_registry={},
            gateway_client_factory=failing_factory,
            a2a_tool_factory=None,
        )

        result = registry.filter_tools(
            enabled_tool_ids=["gateway_wikipedia_search"]
        )

        assert result.tools == []
        assert len(result.validation_errors) == 1
        assert "Gateway" in result.validation_errors[0]

    @patch("agent.tool_filter.ToolFilterRegistry._create_elicitation_bridge")
    def test_federated_tools_use_direct_runtime(
        self,
        mock_create_bridge,
        mock_gateway_client,
        mock_gateway_factory,
    ):
        """User-federated tools use a separate Runtime session."""
        bridge = Mock(name="elicitation_bridge")
        runtime_client = Mock(name="runtime_client")
        runtime_factory = Mock(return_value=runtime_client)
        mock_create_bridge.return_value = bridge
        registry = ToolFilterRegistry(
            local_registry={},
            gateway_client_factory=mock_gateway_factory,
            mcp_client_factory=runtime_factory,
        )

        result = registry.filter_tools(
            enabled_tool_ids=[
                "gateway_wikipedia_search",
                "mcp_search_emails",
            ],
            auth_token="Bearer user-token",
            session_id="session-1",
            user_id="user-1",
        )

        assert result.tools == [mock_gateway_client, runtime_client]
        assert result.clients["gateway"] == mock_gateway_client
        assert result.clients["mcp"] == runtime_client
        assert result.clients["elicitation_bridge"] == bridge
        assert result.tool_ids_by_source["gateway"] == ["gateway_wikipedia_search"]
        assert result.tool_ids_by_source["mcp"] == ["mcp_search_emails"]
        runtime_factory.assert_called_once_with(
            enabled_tool_ids=["mcp_search_emails"],
            auth_token="Bearer user-token",
            elicitation_bridge=bridge,
        )


class TestA2ATools:
    """Tests for A2A agent tool filtering."""

    @pytest.fixture
    def mock_a2a_tool(self):
        """Create a mock A2A tool."""
        return Mock(name="a2a_tool")

    @pytest.fixture
    def mock_a2a_factory(self, mock_a2a_tool):
        """Create a mock A2A tool factory."""
        def factory(agent_id):
            if agent_id == "agentcore_research-agent":
                return mock_a2a_tool
            return None
        return factory

    @pytest.fixture
    def registry_with_a2a(self, mock_a2a_factory):
        """Create registry with A2A support."""
        return ToolFilterRegistry(
            local_registry={},
            gateway_client_factory=None,
            a2a_tool_factory=mock_a2a_factory,
        )

    def test_filter_a2a_tools(self, registry_with_a2a, mock_a2a_tool):
        """Should load A2A tools via factory."""
        result = registry_with_a2a.filter_tools(
            enabled_tool_ids=["agentcore_research-agent"]
        )

        assert len(result.tools) == 1
        assert mock_a2a_tool in result.tools
        assert "agentcore_research-agent" in result.tool_ids_by_source["a2a"]

    def test_a2a_factory_returns_none(self, registry_with_a2a):
        """Should handle unknown A2A agent gracefully."""
        result = registry_with_a2a.filter_tools(
            enabled_tool_ids=["agentcore_unknown-agent"]
        )

        assert result.tools == []
        assert len(result.validation_errors) == 1
        assert "agentcore_unknown-agent" in result.validation_errors[0]


class TestMixedTools:
    """Tests for mixed tool sources."""

    @pytest.fixture
    def full_registry(self):
        """Create registry with all tool sources."""
        local_tools = {
            "calculator": Mock(name="calc"),
            "fetch_url_content": Mock(name="fetch"),
        }
        gateway_client = Mock(name="gateway")
        a2a_tool = Mock(name="a2a")

        def gateway_factory(enabled_tool_ids=None, **kwargs):
            return gateway_client if enabled_tool_ids else None

        def a2a_factory(agent_id):
            return a2a_tool if "research" in agent_id else None

        return ToolFilterRegistry(
            local_registry=local_tools,
            gateway_client_factory=gateway_factory,
            a2a_tool_factory=a2a_factory,
        )

    def test_filter_mixed_tools(self, full_registry):
        """Should filter tools from all sources."""
        result = full_registry.filter_tools(
            enabled_tool_ids=[
                "calculator",
                "gateway_wikipedia_search",
                "agentcore_research-agent",
            ]
        )

        assert len(result.tools) == 3  # local + gateway_client + a2a
        assert result.tool_ids_by_source["local"] == ["calculator"]
        assert "gateway_wikipedia_search" in result.tool_ids_by_source["gateway"]
        assert "agentcore_research-agent" in result.tool_ids_by_source["a2a"]


class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_get_tool_filter_registry_singleton(self):
        """Should return same instance on multiple calls."""
        registry1 = get_tool_filter_registry()
        registry2 = get_tool_filter_registry()
        assert registry1 is registry2

    @patch("agent.tool_filter.get_tool_filter_registry")
    def test_filter_tools_delegates(self, mock_get_registry):
        """filter_tools should delegate to registry."""
        mock_registry = Mock()
        mock_result = FilteredToolResult(tools=[Mock()])
        mock_registry.filter_tools.return_value = mock_result
        mock_get_registry.return_value = mock_registry

        result = filter_tools(
            enabled_tool_ids=["calculator"],
            log_prefix="[Test]"
        )

        mock_registry.filter_tools.assert_called_once_with(
            enabled_tool_ids=["calculator"],
            filters=None,
            log_prefix="[Test]",
            auth_token=None,
            session_id=None,
            user_id=None,
            allow_user_federation=True,
        )
        assert result == mock_result
