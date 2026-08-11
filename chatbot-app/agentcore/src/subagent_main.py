"""Isolated A2A runtime for scoped delegated work."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import uvicorn
from a2a.server.agent_execution import RequestContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import Part, TextPart
from fastapi import FastAPI
from strands import Agent
from strands.multiagent.a2a import A2AServer
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from agent import async_tasks
from agents.model_factory import build_model
from builtin_tools.code_interpreter_tool import execute_code, file_operations
from local_tools.workspace import workspace_list, workspace_read, workspace_write

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", "9000"))
DEFAULT_MODEL_ID = os.environ.get(
    "MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)


@dataclass(frozen=True)
class RequestState:
    profile: str
    job_id: str
    session_id: str
    user_id: str
    model_id: str
    max_seconds: int
    workspace_paths: tuple[str, ...]
    output_path: str


_request_state: contextvars.ContextVar[Optional[RequestState]] = (
    contextvars.ContextVar("delegation_request_state", default=None)
)


_BASE_PROMPT = """You are an isolated subagent executing one delegated task.

The task envelope is the complete scope. Do not infer or create adjacent work.
Produce exactly one deliverable and stop when its acceptance criteria are met.

Rules:
- Do not delegate to another agent.
- Do not access connectors, credentials, or unrelated workspace files.
- Treat all source files as read-only.
- Write generated files only below the supplied output path.
- Record out-of-scope discoveries in scopeExceptions instead of investigating.
- Keep the final summary concise. Put large bodies in workspace artifacts.
- The final response must be a single JSON object with keys:
  summary, findings, artifacts, openQuestions, scopeExceptions.
"""

_PROFILE_PROMPTS = {
    "analyst": """Profile: analyst

Use Code Interpreter when computation or full-file processing is needed.
Inspect only the workspace paths named in the task envelope. Generated files
must be placed below the supplied output path.
""",
    "reviewer": """Profile: reviewer

Perform an independent read-only review. Do not execute code and do not modify
source files. A text report may be written only below the supplied output path
when the task explicitly requests an artifact.
""",
}


def _tools_for(profile: str) -> list[Any]:
    if profile == "analyst":
        return [
            workspace_list,
            workspace_read,
            workspace_write,
            execute_code,
            file_operations,
        ]
    if profile == "reviewer":
        return [workspace_list, workspace_read]
    raise ValueError(f"Unsupported delegation profile: {profile}")


def _build_agent(context_id: str) -> Agent:
    state = _request_state.get()
    if state is None:
        profile = "reviewer"
        model_id = DEFAULT_MODEL_ID
        task_scope = ""
    else:
        profile = state.profile
        model_id = state.model_id or DEFAULT_MODEL_ID
        task_scope = (
            "\nRuntime-enforced task metadata:\n"
            f"- jobId: {state.job_id}\n"
            f"- readable workspace paths: {list(state.workspace_paths)}\n"
            f"- output path: {state.output_path}\n"
        )
    prompt = _BASE_PROMPT + "\n" + _PROFILE_PROMPTS[profile] + task_scope
    return Agent(
        name=f"Delegated {profile.title()}",
        description=f"Scoped asynchronous {profile} subagent",
        model=build_model(
            model_id,
            max_tokens=12_000,
            caching_enabled=False,
        ),
        system_prompt=prompt,
        tools=_tools_for(profile),
    )


class DelegationExecutor(StrandsA2AExecutor):
    def __init__(self):
        super().__init__(agent_factory=lambda context_id: _build_agent(context_id))

    async def _handle_streaming_event(
        self,
        event: dict[str, Any],
        updater: TaskUpdater,
        stream_state=None,
    ) -> None:
        if event.get("type") == "tool_use_stream":
            tool_use = event.get("current_tool_use") or {}
            tool_name = str(tool_use.get("name") or "tool")
            await updater.add_artifact(
                parts=[Part(root=TextPart(text=f"Running {tool_name}"))],
                name=f"delegation_step_{tool_use.get('toolUseId', 'unknown')}",
            )
        elif "result" in event:
            result = str(event["result"])
            await updater.add_artifact(
                parts=[Part(root=TextPart(text=result))],
                name="delegation_result",
            )
            await updater.complete()

    async def _execute_streaming(
        self,
        context: RequestContext,
        updater: TaskUpdater,
    ) -> None:
        metadata = context.metadata or {}
        if not metadata and context.message is not None:
            metadata = getattr(context.message, "metadata", None) or {}

        profile = str(metadata.get("profile") or "")
        if profile not in {"analyst", "reviewer"}:
            raise ValueError("profile must be analyst or reviewer")
        job_id = str(metadata.get("job_id") or "")
        session_id = str(metadata.get("session_id") or "")
        user_id = str(metadata.get("user_id") or "")
        if not job_id or not session_id or not user_id:
            raise ValueError("job_id, session_id, and user_id are required")

        max_seconds = max(30, min(int(metadata.get("max_seconds") or 600), 1800))
        workspace_paths = tuple(
            str(path)
            for path in metadata.get("workspace_paths", [])
            if str(path)
        )
        output_path = str(
            metadata.get("output_path")
            or f"outputs/delegations/{job_id}"
        )
        state = RequestState(
            profile=profile,
            job_id=job_id,
            session_id=session_id,
            user_id=user_id,
            model_id=str(metadata.get("model_id") or DEFAULT_MODEL_ID),
            max_seconds=max_seconds,
            workspace_paths=workspace_paths,
            output_path=output_path,
        )
        _request_state.set(state)

        if context.message is None or not hasattr(context.message, "parts"):
            raise ValueError("Delegation message is required")
        content_blocks = self._convert_a2a_parts_to_content_blocks(
            context.message.parts
        )
        if not content_blocks:
            raise ValueError("Delegation message has no content")

        invocation_state = {
            "job_id": job_id,
            "session_id": session_id,
            "user_id": user_id,
            "profile": profile,
            "workspace_paths": list(workspace_paths),
            "output_path": output_path,
        }
        task_id = async_tasks.begin(
            "delegation",
            {"job_id": job_id, "profile": profile},
        )
        try:
            async with asyncio.timeout(max_seconds):
                await self._run_with_context_agent(
                    context.context_id,
                    content_blocks,
                    invocation_state,
                    updater,
                    None,
                )
        finally:
            async_tasks.end(task_id)


def create_app() -> FastAPI:
    app = FastAPI(
        title="General Subagent A2A Server",
        description="Scoped analyst and reviewer profiles for asynchronous delegation",
        version="1.0.0",
    )
    task_store = InMemoryTaskStore()
    runtime_url = os.environ.get(
        "AGENTCORE_RUNTIME_URL",
        f"http://127.0.0.1:{PORT}/",
    )
    server = A2AServer(
        agent_factory=lambda context_id: _build_agent(context_id),
        http_url=runtime_url,
        serve_at_root=True,
        host="0.0.0.0",
        port=PORT,
        version="1.0.0",
        skills=[
            {
                "id": "analyst",
                "name": "Analyst",
                "description": "Analyze scoped session data and create artifacts",
                "inputModes": ["text/plain"],
                "outputModes": ["application/json"],
                "tags": ["analysis", "code-interpreter", "workspace"],
            },
            {
                "id": "reviewer",
                "name": "Reviewer",
                "description": "Perform an independent read-only scoped review",
                "inputModes": ["text/plain"],
                "outputModes": ["application/json"],
                "tags": ["review", "workspace", "read-only"],
            },
        ],
        task_store=task_store,
    )
    server.request_handler = DefaultRequestHandler(
        agent_executor=DelegationExecutor(),
        task_store=task_store,
    )

    @app.get("/ping")
    def ping():
        return {
            "status": async_tasks.ping_status(),
            "agent": "General Subagent",
            "profiles": ["analyst", "reviewer"],
        }

    app.mount("/", server.to_fastapi_app())
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
