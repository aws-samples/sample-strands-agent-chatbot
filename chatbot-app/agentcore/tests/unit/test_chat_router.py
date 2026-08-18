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
import asyncio
import base64
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import Request
from fastapi.testclient import TestClient
import json
import uuid
from types import SimpleNamespace


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


def _unsigned_test_token(claims: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


# ============================================================
# Ping Endpoint Tests
# ============================================================

class TestPingEndpoint:
    """/ping is owned by routers.health — see test_health_router.py.

    The chat router used to declare a second /ping. It never served a request:
    main.py registers health.router first and FastAPI keeps the first match, so
    the duplicate shadowed nothing and drifted out of spec unnoticed.
    """

    def test_chat_router_does_not_declare_ping(self):
        from routers.chat import router

        paths = {route.path for route in router.routes}
        assert "/ping" not in paths, (
            "/ping must be declared once, in routers.health; a second "
            "declaration is dead code that silently diverges"
        )


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

        latest = registry.get_latest_execution("sess1", "user1")
        assert latest is execution

    @pytest.mark.asyncio
    async def test_background_execution_does_not_supersede_busy_session(self):
        from streaming.execution_registry import ExecutionRegistry, ExecutionStatus
        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        foreground = await registry.create_execution("sess-busy", "user1", "run1")

        background = await registry.create_execution(
            "sess-busy",
            "user1",
            "background",
            supersede_running=False,
        )

        assert background is None
        assert foreground.status == ExecutionStatus.RUNNING
        assert registry.get_latest_execution("sess-busy", "user1") is foreground

    @pytest.mark.asyncio
    async def test_same_session_id_is_isolated_by_user(self):
        from streaming.execution_registry import ExecutionRegistry, ExecutionStatus

        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        first = await registry.create_execution(
            "shared-session",
            "user-a",
            "run-a",
        )
        second = await registry.create_execution(
            "shared-session",
            "user-b",
            "run-b",
        )

        assert first.status == ExecutionStatus.RUNNING
        assert second.status == ExecutionStatus.RUNNING
        assert registry.get_latest_execution("shared-session", "user-a") is first
        assert registry.get_latest_execution("shared-session", "user-b") is second

    @pytest.mark.asyncio
    async def test_cross_user_execution_id_collision_is_rejected(self):
        from streaming.execution_registry import ExecutionRegistry

        ExecutionRegistry.reset()
        registry = ExecutionRegistry()
        first = await registry.create_execution(
            "shared-session",
            "user-a",
            "same-run",
        )
        second = await registry.create_execution(
            "shared-session",
            "user-b",
            "same-run",
        )

        assert first is not None
        assert second is None

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
    async def test_overflow_preserves_monotonic_replay_ids(self):
        from streaming.execution_registry import ExecutionRegistry

        ExecutionRegistry.reset()
        execution = await ExecutionRegistry().create_execution(
            "overflow-session",
            "user1",
            "run1",
        )
        for index in range(execution.MAX_EVENTS + 1):
            execution.append_event(
                f'data: {{"type":"CUSTOM","name":"event-{index}"}}\n\n',
                "custom",
            )

        event_ids = [event.event_id for event in execution.events]
        assert event_ids == sorted(event_ids)
        assert execution.events[0].event_type == "buffer_truncated"
        assert '"type":"CUSTOM"' in execution.events[0].data

    @pytest.mark.asyncio
    async def test_buffer_enforces_byte_limit(self):
        from streaming.execution_registry import ExecutionRegistry

        ExecutionRegistry.reset()
        execution = await ExecutionRegistry().create_execution(
            "byte-limit-session",
            "user1",
            "run1",
        )
        execution.MAX_BUFFER_BYTES = 2048

        for index in range(20):
            execution.append_event(
                'data: {"type":"CUSTOM","name":"payload",'
                f'"value":"{index}-{"x" * 300}"}}\n\n',
                "CUSTOM",
            )

        assert execution.buffered_bytes <= execution.MAX_BUFFER_BYTES
        assert execution.events[0].event_type == "buffer_truncated"

    @pytest.mark.asyncio
    async def test_oversized_event_is_replaced_with_marker(self):
        from streaming.execution_registry import ExecutionRegistry

        ExecutionRegistry.reset()
        execution = await ExecutionRegistry().create_execution(
            "oversized-session",
            "user1",
            "run1",
        )
        execution.MAX_BUFFER_BYTES = 1024

        event = execution.append_event(
            f'data: {{"type":"CUSTOM","value":"{"x" * 2000}"}}\n\n',
            'CUSTOM"with-quote',
        )
        payload = json.loads(event.data.removeprefix("data: ").strip())

        assert event.event_type == "event_dropped"
        assert payload["name"] == "event_dropped"
        assert payload["value"]["eventType"] == 'CUSTOM"with-quote'
        assert execution.buffered_bytes <= execution.MAX_BUFFER_BYTES

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
        from streaming.execution_registry import ExecutionRegistry
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


class TestMailboxDelivery:
    def test_model_input_excludes_internal_correlation_ids(self):
        from routers import chat

        message = chat._build_background_research_message("# Finished report")

        assert message.startswith("<background-research-result>\n")
        assert "job_id" not in message
        assert "artifact_id" not in message
        assert "# Finished report" in message

    @pytest.mark.asyncio
    async def test_generic_event_loads_research_job_and_marks_delivered(
        self,
        monkeypatch,
    ):
        from agent import research_jobs
        from routers import chat

        record = {
            "jobId": "job-1",
            "sessionId": "session-1",
            "userId": "user-1",
            "artifactId": "artifact-1",
        }
        delivered = AsyncMock()
        marked = MagicMock()
        monkeypatch.setattr(research_jobs, "_get_job", lambda *_: record)
        monkeypatch.setattr(research_jobs, "_load_report", lambda _: "# Report")
        monkeypatch.setattr(
            research_jobs,
            "_build_artifact",
            lambda item, report: {"id": item["artifactId"], "content": report},
        )
        monkeypatch.setattr(research_jobs, "mark_delivered", marked)
        monkeypatch.setattr(chat, "deliver_research_job", delivered)
        event = SimpleNamespace(
            event_id="research-result:job-1",
            user_id="user-1",
            session_id="session-1",
            source={"type": "research_job", "id": "job-1"},
            correlation={"artifactId": "artifact-1"},
            payload_ref={"bucket": "bucket", "key": "report.md"},
        )

        result = await chat.deliver_mailbox_event(event)
        projections = result.session_events

        assert record["mailboxEventId"] == "research-result:job-1"
        delivered.assert_awaited_once()
        marked.assert_not_called()
        assert [item.event_type for item in projections] == [
            "artifact.upserted",
            "assistant.turn.completed",
        ]
        assert projections[1].payload["executionId"] == (
            "session-1:research-delivery-job-1"
        )
        assert projections[1].payload["logicalMessageId"] == (
            "mailbox:research-result:job-1:1"
        )
        result.after_ack()
        marked.assert_called_once_with(record)

    @pytest.mark.asyncio
    async def test_committed_event_skips_duplicate_agent_generation(
        self,
        monkeypatch,
    ):
        from routers import chat
        from streaming.execution_registry import ExecutionStatus

        class State:
            def __init__(self):
                self.values = {
                    "artifacts": {},
                    "mailbox_commits": {
                        "research-result:job-1": {"sourceId": "job-1"}
                    },
                }

            def get(self, key):
                return self.values.get(key)

            def set(self, key, value):
                self.values[key] = value

        execution = SimpleNamespace(
            execution_id="session-1:research-delivery-job-1",
            status=ExecutionStatus.RUNNING,
            task=None,
            completed_at=None,
            _new_event=asyncio.Event(),
        )

        class Registry:
            async def create_execution(self, *args, **kwargs):
                return execution

        session_manager = MagicMock()
        agent = SimpleNamespace(state=State())
        wrapper = SimpleNamespace(
            agent=agent,
            session_manager=session_manager,
            model_id="model-1",
            elicitation_bridge=None,
            close=MagicMock(),
        )
        monkeypatch.setattr(chat, "registry", Registry())
        monkeypatch.setattr(chat, "create_agent", MagicMock(return_value=wrapper))
        processor = MagicMock()
        monkeypatch.setattr(chat, "AGUIStreamEventProcessor", processor)

        await chat.deliver_research_job(
            {
                "jobId": "job-1",
                "sessionId": "session-1",
                "userId": "user-1",
                "artifactId": "artifact-1",
                "mailboxEventId": "research-result:job-1",
                "artifactPath": "/tmp/job-1.md",
            },
            {"id": "artifact-1", "content": "# Report"},
        )

        processor.assert_not_called()
        assert agent.state.get("artifacts")["artifact-1"] == {
            "id": "artifact-1",
            "content_ref": {"path": "/tmp/job-1.md"},
        }
        session_manager.sync_agent.assert_not_called()


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

    @patch('routers.chat.create_agent')
    def test_accepts_standard_camel_case_run_input(
        self,
        mock_factory,
        mock_agent,
    ):
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        payload = _agui_payload(session_id="camel-session")
        payload["threadId"] = payload.pop("thread_id")
        payload["runId"] = payload.pop("run_id")

        response = client.post("/invocations", json=payload)

        assert response.status_code == 200
        assert response.headers["x-thread-id"] == "camel-session"

    def test_rejects_claimed_user_that_differs_from_runtime_subject(
        self,
        monkeypatch,
    ):
        from routers.chat import router
        from fastapi import FastAPI

        monkeypatch.setenv("ENVIRONMENT", "production")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            headers={
                "Authorization": (
                    f"Bearer {_unsigned_test_token({'sub': 'authenticated-user'})}"
                )
            },
            json=_agui_payload(user_id="different-user"),
        )

        assert response.status_code == 403

    def test_allows_trusted_m2m_client_to_delegate_user_identity(
        self,
        monkeypatch,
    ):
        from routers.chat import router
        from fastapi import FastAPI

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("M2M_CLIENT_ID", "trusted-service")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            headers={
                "Authorization": (
                    "Bearer "
                    + _unsigned_test_token({
                        "client_id": "trusted-service",
                        "sub": "trusted-service-token-subject",
                    })
                )
            },
            json={
                "threadId": "m2m-session",
                "runId": str(uuid.uuid4()),
                "state": {
                    "action": "warmup",
                    "user_id": "delegated-user",
                },
            },
        )

        assert response.status_code == 200

    def test_rejects_untrusted_client_without_subject(
        self,
        monkeypatch,
    ):
        from routers.chat import router
        from fastapi import FastAPI

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("M2M_CLIENT_ID", "trusted-service")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            headers={
                "Authorization": (
                    f"Bearer {_unsigned_test_token({'client_id': 'other-service'})}"
                )
            },
            json={
                "threadId": "m2m-session",
                "runId": str(uuid.uuid4()),
                "state": {
                    "action": "warmup",
                    "user_id": "delegated-user",
                },
            },
        )

        assert response.status_code == 401

    def test_rejects_excessive_message_count(self):
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        payload = _agui_payload()
        payload["messages"] = [
            {"id": str(index), "role": "user", "content": "message"}
            for index in range(501)
        ]

        response = client.post("/invocations", json=payload)

        assert response.status_code == 422

    def test_rejects_control_characters_in_thread_id(self):
        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json=_agui_payload(session_id="bad\nsession"),
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

    def test_mailbox_drain_rejects_user_token(self, monkeypatch):
        from routers.chat import router
        from fastapi import FastAPI

        monkeypatch.setenv("M2M_CLIENT_ID", "dispatcher-client")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            headers={
                "Authorization": (
                    f"Bearer {_unsigned_test_token({'client_id': 'web-client'})}"
                )
            },
            json={
                "thread_id": "session-1",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "drain_mailbox", "user_id": "user-1"},
            },
        )

        assert response.status_code == 403

    def test_mailbox_drain_accepts_dispatcher_and_reports_empty(
        self,
        monkeypatch,
    ):
        from agent import mailbox, mailbox_runtime
        from routers.chat import router
        from fastapi import FastAPI

        monkeypatch.setenv("M2M_CLIENT_ID", "dispatcher-client")
        drain = AsyncMock(
            return_value=SimpleNamespace(
                processed=1,
                retried=0,
                dead=0,
                acquired=True,
            )
        )
        repository = MagicMock()
        repository.is_session_deleted.return_value = False
        repository.list_events.return_value = []
        monkeypatch.setattr(mailbox_runtime, "drain_session_mailbox", drain)
        monkeypatch.setattr(mailbox, "get_mailbox_repository", lambda: repository)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            headers={
                "Authorization": (
                    f"Bearer {_unsigned_test_token({'client_id': 'dispatcher-client'})}"
                )
            },
            json={
                "thread_id": "session-1",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "drain_mailbox", "user_id": "user-1"},
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "drained",
            "processed": 1,
            "retried": 0,
            "dead": 0,
            "acquired": True,
            "pending": 0,
        }
        drain.assert_awaited_once_with("user-1", "session-1")

    def test_mailbox_drain_absorbs_deleted_session_wake(
        self,
        monkeypatch,
    ):
        from agent import mailbox, mailbox_runtime
        from routers.chat import router
        from fastapi import FastAPI

        monkeypatch.setenv("M2M_CLIENT_ID", "dispatcher-client")
        drain = AsyncMock()
        repository = MagicMock()
        repository.is_session_deleted.return_value = True
        monkeypatch.setattr(mailbox_runtime, "drain_session_mailbox", drain)
        monkeypatch.setattr(mailbox, "get_mailbox_repository", lambda: repository)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            headers={
                "Authorization": (
                    f"Bearer {_unsigned_test_token({'client_id': 'dispatcher-client'})}"
                )
            },
            json={
                "thread_id": "session-1",
                "run_id": str(uuid.uuid4()),
                "state": {"action": "drain_mailbox", "user_id": "user-1"},
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "drained",
            "processed": 0,
            "retried": 0,
            "dead": 0,
            "acquired": False,
            "pending": 0,
            "deleted": True,
        }
        drain.assert_not_awaited()

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
                "thread_id": "sess-status",
                "run_id": str(uuid.uuid4()),
                "state": {
                    "action": "execution_status",
                    "execution_id": "sess-status:run-1",
                    "user_id": "user-1",
                }
            }
        )

        assert response.status_code == 200
        assert response.json() == {"status": "running"}

        # Cleanup
        execution.status = __import__('streaming.execution_registry', fromlist=['ExecutionStatus']).ExecutionStatus.COMPLETED
        execution.completed_at = __import__('time').time()

    @pytest.mark.asyncio
    async def test_execution_status_hides_other_users_execution(self):
        from routers.chat import router, registry
        from streaming.execution_registry import ExecutionStatus
        from fastapi import FastAPI

        execution = await registry.create_execution(
            "owned-session",
            "owner-user",
            "owned-run",
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "owned-session",
                "run_id": str(uuid.uuid4()),
                "state": {
                    "action": "execution_status",
                    "execution_id": execution.execution_id,
                    "user_id": "other-user",
                },
            },
        )

        assert response.status_code == 200
        assert response.json() == {"status": "not_found"}
        execution.status = ExecutionStatus.COMPLETED
        execution.completed_at = __import__('time').time()

    @pytest.mark.asyncio
    async def test_resume_hides_other_users_execution(self):
        from routers.chat import router, registry
        from streaming.execution_registry import ExecutionStatus
        from fastapi import FastAPI

        execution = await registry.create_execution(
            "resume-owned-session",
            "owner-user",
            "owned-run",
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/invocations",
            json={
                "thread_id": "resume-owned-session",
                "run_id": str(uuid.uuid4()),
                "state": {
                    "action": "resume",
                    "execution_id": execution.execution_id,
                    "user_id": "other-user",
                },
            },
        )

        assert response.status_code == 404
        execution.status = ExecutionStatus.COMPLETED
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

    def test_resume_rejects_negative_cursor(self):
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
                "state": {
                    "action": "resume",
                    "execution_id": "resume-session:run",
                    "cursor": -1,
                },
            },
        )

        assert response.status_code == 400

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

    def test_parses_standard_resume_entries(self):
        from ag_ui.core import ResumeEntry
        from routers.chat import _parse_resume_entries

        result = _parse_resume_entries([
            ResumeEntry(
                interruptId="interrupt-123",
                status="resolved",
                payload="approved",
            ),
        ])

        assert result == [{
            "interruptResponse": {
                "interruptId": "interrupt-123",
                "response": "approved",
            },
        }]

    def test_cancelled_resume_maps_to_declined(self):
        from ag_ui.core import ResumeEntry
        from routers.chat import _parse_resume_entries

        result = _parse_resume_entries([
            ResumeEntry(
                interruptId="interrupt-123",
                status="cancelled",
            ),
        ])

        assert result[0]["interruptResponse"]["response"] == "declined"


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

    def test_extracts_run_error_from_batched_sse_chunk(self):
        from routers.chat import _extract_event_type, _extract_event_types

        chunk = (
            'data: {"type":"TEXT_MESSAGE_END","messageId":"message-1"}\n\n'
            'data: {"type":"RUN_ERROR","message":"failed"}\n\n'
        )

        assert _extract_event_type(chunk) == "TEXT_MESSAGE_END"
        assert _extract_event_types(chunk) == [
            "TEXT_MESSAGE_END",
            "RUN_ERROR",
        ]


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

    @patch('routers.chat.create_agent')
    def test_adds_valid_workspace_attachments_to_turn_context(
        self, mock_factory, mock_agent
    ):
        """Workspace paths are validated before becoming agent context."""
        mock_factory.return_value = mock_agent

        from routers.chat import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        client.post(
            "/invocations",
            json=_agui_payload(
                system_prompt="Existing context.",
                workspace_paths=[
                    "uploads/packets-pass-a.jsonl",
                    "../not-allowed.jsonl",
                    "uploads/nested/not-allowed.jsonl",
                ],
            ),
        )

        prompt = mock_factory.call_args.kwargs['system_prompt']
        assert prompt.startswith("Existing context.")
        assert "uploads/packets-pass-a.jsonl" in prompt
        assert "../not-allowed.jsonl" not in prompt
        assert "uploads/nested/not-allowed.jsonl" not in prompt
