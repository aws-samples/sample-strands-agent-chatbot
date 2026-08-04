"""
Tests for chat.py router

Tests cover:
- /ping endpoint
- /invocations endpoint (AG-UI format)
- Interrupt response handling
- Disconnect-aware streaming
- Error handling
- Lifecycle actions (warmup, stop, elicitation, execution_status, resume)
- Standalone GET endpoints (execution-status, resume)
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import Request
from fastapi.testclient import TestClient
import json
import uuid


def _agui_payload(
    message: str = "Hello",
    user_id: str = "test-user",
    session_id: str = "test-session",
    **state_overrides
) -> dict:
    """Helper to build an AG-UI payload for tests."""
    state = {"user_id": user_id, **state_overrides}
    return {
        "thread_id": session_id,
        "run_id": str(uuid.uuid4()),
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": message}],
        "tools": [],
        "context": [],
        "state": state,
    }


# ============================================================
# Ping Endpoint Tests
# ============================================================

class TestPingEndpoint:
    """Tests for the /ping health check endpoint."""

    def test_ping_returns_healthy(self):
        """Test that ping returns healthy status."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/ping")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_ping_is_get_method(self):
        """Test that ping only accepts GET requests."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # POST should fail
        response = client.post("/ping")
        assert response.status_code == 405


# ============================================================
# Agent Factory Tests
# ============================================================

class TestAgentFactory:
    """Tests for the agent factory integration."""

    @patch('routers.chat.create_agent')
    def test_defaults_to_skill_mode(self, mock_factory):
        """With no request_type in state, the router should default to 'skill'."""
        from routers.chat import router
        from fastapi import FastAPI

        mock_agent = MagicMock()
        async def mock_stream(*args, **kwargs):
            yield 'data: {"type": "complete"}\n\n'
        mock_agent.stream_async = mock_stream
        mock_factory.return_value = mock_agent

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        client.post(
            "/invocations",
            json=_agui_payload(session_id="test-session-123")
        )

        mock_factory.assert_called_once()
        call_kwargs = mock_factory.call_args.kwargs
        assert call_kwargs['request_type'] == "skill"
        assert call_kwargs['session_id'] == "test-session-123"


# ============================================================
# Disconnect-Aware Stream Tests
# ============================================================

class TestExecutionRegistry:
    """Tests for ExecutionRegistry and _create_tail_stream."""

    @pytest.mark.asyncio
    async def test_create_and_get_execution(self):
        """Test creating and retrieving an execution."""
        from streaming.execution_registry import ExecutionRegistry, ExecutionStatus
        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        execution = await registry.create_execution("sess1", "user1", "run1")
        assert execution.execution_id == "sess1:run1"
        assert execution.status == ExecutionStatus.RUNNING

        found = registry.get_execution("sess1:run1")
        assert found is execution

        latest = registry.get_latest_execution("sess1")
        assert latest is execution

    @pytest.mark.asyncio
    async def test_append_and_get_events(self):
        """Test appending events and cursor-based retrieval."""
        from streaming.execution_registry import ExecutionRegistry
        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        execution = await registry.create_execution("sess2", "user1", "run2")

        e1 = execution.append_event('data: {"type":"init"}\n\n', "init")
        e2 = execution.append_event('data: {"type":"response"}\n\n', "response")
        e3 = execution.append_event('data: {"type":"complete"}\n\n', "complete")

        assert e1.event_id == 1
        assert e2.event_id == 2
        assert e3.event_id == 3

        # Get events from cursor 0 (all)
        events = execution.get_events_from(0)
        assert len(events) == 3

        # Get events from cursor 2 (only event 3)
        events = execution.get_events_from(2)
        assert len(events) == 1
        assert events[0].event_id == 3

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        """Test that completed executions are cleaned up after TTL."""
        import time
        from streaming.execution_registry import ExecutionRegistry, ExecutionStatus
        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        execution = await registry.create_execution("sess3", "user1", "run3")
        execution.status = ExecutionStatus.COMPLETED
        execution.completed_at = time.time() - 400  # Past TTL

        removed = await registry.cleanup_expired()
        assert removed == 1
        assert registry.get_execution("sess3:run3") is None

    @pytest.mark.asyncio
    async def test_tail_stream_replays_and_closes(self):
        """Test that _create_tail_stream replays buffered events."""
        from routers.chat import _create_tail_stream
        from streaming.execution_registry import ExecutionRegistry, ExecutionStatus
        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        execution = await registry.create_execution("sess4", "user1", "run4")

        # Pre-buffer events
        execution.append_event('data: {"type":"init"}\n\n', "init")
        execution.append_event('data: {"type":"response"}\n\n', "response")
        execution.status = ExecutionStatus.COMPLETED
        execution.completed_at = 0

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)

        chunks = []
        async for chunk in _create_tail_stream(execution, cursor=0, http_request=mock_request):
            chunks.append(chunk)

        # Should have execution_meta + 2 events
        assert len(chunks) == 3
        assert "execution_meta" in chunks[0]
        assert "init" in chunks[1]
        assert "response" in chunks[2]

    @pytest.mark.asyncio
    async def test_tail_stream_stops_on_disconnect(self):
        """Test that tail stream stops when client disconnects."""
        from routers.chat import _create_tail_stream
        from streaming.execution_registry import ExecutionRegistry, ExecutionStatus
        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        execution = await registry.create_execution("sess5", "user1", "run5")
        # Keep status as RUNNING so the stream would normally wait

        mock_request = MagicMock(spec=Request)
        # Disconnect immediately after yielding metadata
        mock_request.is_disconnected = AsyncMock(side_effect=[False, True])

        chunks = []
        async for chunk in _create_tail_stream(execution, cursor=0, http_request=mock_request):
            chunks.append(chunk)

        # Should only have the metadata event before disconnect
        assert len(chunks) == 1
        assert "execution_meta" in chunks[0]


# ============================================================
# Invocations Endpoint Tests
# ============================================================

class TestInvocationsEndpoint:
    """Tests for the /invocations endpoint (AG-UI format)."""

    @pytest.fixture
    def mock_agent(self):
        """Create mock agent for testing."""
        agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield 'data: {"type": "init"}\n\n'
            yield 'data: {"type": "text", "content": "Hello"}\n\n'
            yield 'data: {"type": "complete"}\n\n'

        agent.stream_async = mock_stream
        return agent

    @patch('routers.chat.create_agent')
    def test_invocations_returns_streaming_response(self, mock_factory, mock_agent):
        """Test that invocations returns SSE streaming response."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json=_agui_payload()
        )

        assert response.status_code == 200
        assert response.headers.get("content-type") == "text/event-stream; charset=utf-8"

    @patch('routers.chat.create_agent')
    def test_invocations_sets_session_header(self, mock_factory, mock_agent):
        """Test that invocations sets X-Session-ID header."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json=_agui_payload(session_id="my-session-123")
        )

        assert response.headers.get("x-session-id") == "my-session-123"

    @patch('routers.chat.create_agent')
    def test_invocations_passes_disabled_skills(self, mock_factory, mock_agent):
        """Test that disabled skills from state are passed to agent."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        payload = _agui_payload(disabled_skills=["calculator", "web-search"])

        client.post("/invocations", json=payload)

        mock_factory.assert_called_once()
        call_kwargs = mock_factory.call_args.kwargs
        assert call_kwargs['disabled_skills'] == ["calculator", "web-search"]

    @patch('routers.chat.create_agent')
    def test_invocations_handles_files(self, mock_factory, mock_agent):
        """Test that binary file parts in messages are handled."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        payload = _agui_payload(message="Analyze this")
        payload["messages"] = [{
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this"},
                {"type": "binary", "mime_type": "image/png", "data": "dGVzdA==", "filename": "test.png"},
            ]
        }]

        response = client.post("/invocations", json=payload)

        assert response.status_code == 200

    def test_returns_422_on_missing_agui_fields(self):
        """Test that 422 is returned when thread_id/run_id are missing."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Missing required AG-UI fields
        response = client.post(
            "/invocations",
            json={"state": {"user_id": "test"}}
        )

        assert response.status_code == 422


# ============================================================
# Lifecycle Action Tests (warmup, stop, elicitation)
# ============================================================

class TestLifecycleActions:
    """Tests for lifecycle actions via state.action."""

    def test_warmup_returns_warm(self):
        """Test warmup action returns warm status."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "warmup-session",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "warmup", "user_id": "test-user"}
            }
        )

        assert response.status_code == 200
        assert response.json() == {"status": "warm"}

    @patch('agent.stop_signal.get_stop_signal_provider')
    def test_stop_sets_signal(self, mock_get_provider):
        """Test stop action sets stop signal."""
        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider
        target_run_id = str(uuid.uuid4())

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "test-session",
                "run_id": str(uuid.uuid4()),
                "state": {
                    "action": "stop",
                    "user_id": "test-user",
                    "run_id": target_run_id,
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stop_requested"
        assert data["session_id"] == "test-session"
        assert data["run_id"] == target_run_id
        mock_provider.request_stop.assert_called_once_with(
            "test-user",
            "test-session",
            target_run_id,
        )

    def test_stop_requires_target_run_id(self):
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "test-session",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "stop", "user_id": "test-user"},
            },
        )

        assert response.status_code == 400

    def test_execution_status_not_found(self):
        """Test execution_status action returns not_found for unknown execution."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "status-session",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "execution_status", "execution_id": "nonexistent:run123"}
            }
        )

        assert response.status_code == 200
        assert response.json() == {"status": "not_found"}

    def test_execution_status_missing_id(self):
        """Test execution_status action returns not_found when execution_id is missing."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "status-session",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "execution_status"}
            }
        )

        assert response.status_code == 200
        assert response.json() == {"status": "not_found"}

    @pytest.mark.asyncio
    async def test_execution_status_running(self):
        """Test execution_status returns running for active execution."""
        from routers.chat import router, registry

        execution = await registry.create_execution("sess-status", "user-1", "run-1")

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "status-session",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "execution_status", "execution_id": "sess-status:run-1"}
            }
        )

        assert response.status_code == 200
        assert response.json() == {"status": "running"}

        # Cleanup
        execution.status = __import__('streaming.execution_registry', fromlist=['ExecutionStatus']).ExecutionStatus.COMPLETED
        execution.completed_at = __import__('time').time()

    def test_resume_missing_execution_id(self):
        """Test resume action returns 400 when execution_id is missing."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "resume-session",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "resume"}
            }
        )

        assert response.status_code == 400

    def test_resume_not_found(self):
        """Test resume action returns 404 for unknown execution."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "resume-session",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "resume", "execution_id": "nonexistent:run123"}
            }
        )

        assert response.status_code == 404

    def test_get_execution_status_endpoint(self):
        """Test standalone GET /execution-status endpoint."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/execution-status?executionId=nonexistent:run1")
        assert response.status_code == 200
        assert response.json() == {"status": "not_found"}

    def test_get_resume_endpoint_not_found(self):
        """Test standalone GET /resume endpoint returns 404 for unknown execution."""
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/resume?executionId=nonexistent:run1&cursor=0")
        assert response.status_code == 404


# ============================================================
# Interrupt Response Tests
# ============================================================

class TestInterruptResponseHandling:
    """Tests for interrupt response handling in invocations."""

    @pytest.fixture
    def mock_agent(self):
        """Create mock agent for interrupt testing."""
        agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield 'data: {"type": "text", "content": "Continuing..."}\n\n'

        agent.stream_async = mock_stream
        return agent

    @patch('routers.chat.create_agent')
    def test_parses_interrupt_response(self, mock_factory, mock_agent):
        """Test that interrupt response is parsed from JSON array."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Frontend sends interrupt response as JSON array in the message
        interrupt_message = json.dumps([{
            "interruptResponse": {
                "interruptId": "interrupt-123",
                "response": "approved"
            }
        }])

        response = client.post(
            "/invocations",
            json=_agui_payload(message=interrupt_message)
        )

        assert response.status_code == 200

    @patch('routers.chat.create_agent')
    def test_handles_normal_message_not_json(self, mock_factory, mock_agent):
        """Test that normal text messages are not parsed as interrupt."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json=_agui_payload(message="Just a normal message")
        )

        assert response.status_code == 200

    @patch('routers.chat.create_agent')
    def test_handles_json_without_interrupt_response(self, mock_factory, mock_agent):
        """Test that JSON without interruptResponse is treated as normal."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json=_agui_payload(message=json.dumps({"data": "something"}))
        )

        assert response.status_code == 200


# ============================================================
# Error Handling Tests
# ============================================================

class TestInvocationsErrorHandling:
    """Tests for error handling in invocations endpoint."""

    @patch('routers.chat.create_agent')
    def test_returns_500_on_agent_error(self, mock_factory):
        """Test that 500 is returned when agent fails."""
        mock_factory.side_effect = Exception("Agent creation failed")

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json=_agui_payload()
        )

        assert response.status_code == 500
        assert "Agent processing failed" in response.json()["detail"]


# ============================================================
# Model Configuration Tests
# ============================================================

class TestModelConfiguration:
    """Tests for model configuration in invocations."""

    @pytest.fixture
    def mock_agent(self):
        """Create mock agent."""
        agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield 'data: {"type": "complete"}\n\n'

        agent.stream_async = mock_stream
        return agent

    @patch('routers.chat.create_agent')
    def test_passes_model_id(self, mock_factory, mock_agent):
        """Test that model_id is passed to agent."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        client.post(
            "/invocations",
            json=_agui_payload(model_id="us.anthropic.claude-opus-5")
        )

        call_kwargs = mock_factory.call_args.kwargs
        assert call_kwargs['model_id'] == "us.anthropic.claude-opus-5"

    @patch('routers.chat.create_agent')
    def test_passes_temperature(self, mock_factory, mock_agent):
        """Test that temperature is passed to agent."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        client.post(
            "/invocations",
            json=_agui_payload(temperature=0.3)
        )

        call_kwargs = mock_factory.call_args.kwargs
        assert call_kwargs['temperature'] == 0.3

    @patch('routers.chat.create_agent')
    def test_passes_system_prompt(self, mock_factory, mock_agent):
        """Test that system_prompt is passed to agent."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        client.post(
            "/invocations",
            json=_agui_payload(system_prompt="You are a coding assistant.")
        )

        call_kwargs = mock_factory.call_args.kwargs
        assert call_kwargs['system_prompt'] == "You are a coding assistant."
