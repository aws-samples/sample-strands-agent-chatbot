"""
Tests for voice.py router

Tests cover:
- _get_param_from_request (generic param extraction)
- /voice/sessions endpoint (list active sessions)
- /voice/sessions/{session_id} endpoint (stop session)
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock


class TestGetParamFromRequest:
    """Tests for _get_param_from_request function."""

    def test_extracts_from_custom_header(self):
        """Cloud mode: extract from custom header."""
        from routers.voice import _get_param_from_request

        mock_ws = MagicMock()
        mock_ws.headers.get.return_value = "test-session-123"

        result = _get_param_from_request(mock_ws, "session-id", None)

        assert result == "test-session-123"
        mock_ws.headers.get.assert_called_with("x-amzn-bedrock-agentcore-runtime-custom-session-id")

    def test_falls_back_to_query_param(self):
        """Local mode: fall back to query param when header absent."""
        from routers.voice import _get_param_from_request

        mock_ws = MagicMock()
        mock_ws.headers.get.return_value = None

        result = _get_param_from_request(mock_ws, "user-id", "user-456")

        assert result == "user-456"

    def test_header_takes_precedence(self):
        """Header takes precedence over query param."""
        from routers.voice import _get_param_from_request

        mock_ws = MagicMock()
        mock_ws.headers.get.return_value = "from-header"

        result = _get_param_from_request(mock_ws, "session-id", "from-query")

        assert result == "from-header"

    def test_returns_none_when_both_absent(self):
        """Returns None when neither header nor query param present."""
        from routers.voice import _get_param_from_request

        mock_ws = MagicMock()
        mock_ws.headers.get.return_value = None

        result = _get_param_from_request(mock_ws, "session-id", None)

        assert result is None

    def test_runtime_session_header_is_supported(self):
        """Cloud mode uses AgentCore's native isolated session header."""
        from routers.voice import _get_session_id_from_request

        mock_ws = MagicMock()
        mock_ws.headers.get.side_effect = lambda name: (
            "runtime-session-123"
            if name == "x-amzn-bedrock-agentcore-runtime-session-id"
            else None
        )

        assert _get_session_id_from_request(mock_ws, "query-session") == "runtime-session-123"


class TestVoiceSessionsEndpoint:
    """Tests for the /voice/sessions endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with voice router."""
        from routers.voice import router, _active_sessions

        # Clear any existing sessions
        _active_sessions.clear()

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_list_sessions_empty(self, client):
        """Test listing sessions when none are active."""
        response = client.get("/voice/sessions")

        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == []
        assert data["count"] == 0

    def test_list_sessions_with_active(self, client):
        """Test listing sessions with active sessions."""
        from routers.voice import _active_sessions

        # Simulate active sessions
        _active_sessions["session-1"] = "mock_agent_1"
        _active_sessions["session-2"] = "mock_agent_2"

        response = client.get("/voice/sessions")

        assert response.status_code == 200
        data = response.json()
        assert set(data["active_sessions"]) == {"session-1", "session-2"}
        assert data["count"] == 2

        # Cleanup
        _active_sessions.clear()


class TestStopSessionEndpoint:
    """Tests for the /voice/sessions/{session_id} DELETE endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with voice router."""
        from routers.voice import router, _active_sessions

        _active_sessions.clear()

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_stop_nonexistent_session(self, client):
        """Test stopping a session that doesn't exist."""
        response = client.delete("/voice/sessions/nonexistent-id")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_found"
        assert data["session_id"] == "nonexistent-id"

    def test_stop_existing_session(self, client):
        """Test stopping an existing session."""
        from routers.voice import _active_sessions
        from unittest.mock import MagicMock, AsyncMock

        # Create mock agent with async stop method
        mock_agent = MagicMock()
        mock_agent.stop = AsyncMock()

        _active_sessions["test-session"] = mock_agent

        response = client.delete("/voice/sessions/test-session")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"
        assert data["session_id"] == "test-session"

        # Verify session was removed
        assert "test-session" not in _active_sessions

        # Verify stop was called
        mock_agent.stop.assert_called_once()
