"""
A2A Agent Tools Module

Integrates AgentCore Runtime A2A agents as direct callable tools.
Uses A2A SDK to communicate with agents deployed on AgentCore Runtime.

Based on: amazon-bedrock-agentcore-samples orchestrator pattern
"""

import logging
import os
import asyncio
from typing import Optional, Dict, Any, AsyncGenerator, Literal
from uuid import uuid4
from strands.tools import tool
from strands.types.tools import ToolContext

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart, AgentCard

from agent.config.model_catalog import resolve_code_agent_model
from agent.mcp.client import BearerAuth
from a2a_response import A2ATextAccumulator

logger = logging.getLogger(__name__)

# Global cache
_cache = {
    'agent_urls': {},
    'agent_cards': {},
}


DEFAULT_TIMEOUT = 2400  # 40 minutes for complex coding tasks
AGENT_TIMEOUT = 2400    # 2400s (40 minutes) per agent call


# ============================================================
# Helper Functions
# ============================================================

def get_cached_agent_url(agent_id: str) -> Optional[str]:
    """Get and cache A2A agent invocation URL from Registry."""
    if agent_id not in _cache['agent_urls']:
        if agent_id == "agentcore_general-subagent":
            url = os.environ.get("GENERAL_SUBAGENT_RUNTIME_URL")
            if url:
                _cache['agent_urls'][agent_id] = url
                return url
        from registry.client import get_registry_client
        client = get_registry_client()
        if not client:
            logger.error(f"Registry client not available for {agent_id}")
            return None

        agent_name = agent_id.replace("agentcore_", "")
        url = client.get_a2a_endpoint_url(agent_name)
        if not url:
            logger.error(f"No Registry endpoint for A2A agent {agent_name}")
            return None

        _cache['agent_urls'][agent_id] = url
        logger.info(f"Cached URL for {agent_id}: {url[:60]}...")

    return _cache['agent_urls'][agent_id]


def get_http_client(region: str = "us-west-2", auth_token: Optional[str] = None):
    """Create an isolated HTTP client for one A2A call."""
    client_kwargs = {
        "timeout": httpx.Timeout(DEFAULT_TIMEOUT, connect=30.0),
        "limits": httpx.Limits(max_keepalive_connections=5, max_connections=10),
    }
    if auth_token:
        client_kwargs["auth"] = BearerAuth(
            auth_token.replace("Bearer ", "") if auth_token.startswith("Bearer ") else auth_token
        )
    return httpx.AsyncClient(**client_kwargs)


async def send_a2a_message(
    agent_id: str,
    message: str,
    session_id: Optional[str] = None,
    region: str = "us-west-2",
    metadata: Optional[dict] = None,
    auth_token: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream messages from A2A agent on AgentCore Runtime (ASYNC GENERATOR)

    Args:
        agent_id: Agent identifier (e.g., "agentcore_research-agent")
        message: User message to send
        session_id: Session ID from BFF (optional, will generate if not provided)
        region: AWS region
        metadata: Additional payload to send (user_id, preferences, context, etc.)

    Yields:
        Events from A2A agent:
        - {"type": "browser_session_detected", "browserSessionId": "...", "message": "..."}  # Immediate
        - {"status": "success", "content": [...]}  # Final result

    Example metadata:
        {
            "user_id": "user123",
            "language": "ko",
            "max_sources": 5,
            "depth": "detailed",
            "format_preference": "markdown"
        }
    """
    # Initialise finally-block variables up front so an early exception cannot
    # shadow them with UnboundLocalError.
    completed = False
    current_task_id = None
    client = None
    httpx_client = None
    owns_httpx_client = False

    try:
        # Check for local testing mode (per-agent env var)
        # e.g. LOCAL_RESEARCH_AGENT_URL, LOCAL_CODE_AGENT_URL, LOCAL_BROWSER_USE_AGENT_URL
        # Only this agent's own variable: falling back to the research URL sent
        # every other agent's traffic to the research runtime, which answers with
        # a research report instead of failing.
        env_key = "LOCAL_" + agent_id.replace("agentcore_", "").replace("-", "_").upper() + "_URL"
        local_runtime_url = os.environ.get(env_key)
        if local_runtime_url:
            runtime_url = local_runtime_url
            logger.debug(f"Local test mode ({agent_id}): {runtime_url}")
        else:
            runtime_url = get_cached_agent_url(agent_id)
            if not runtime_url:
                yield {
                    "status": "error",
                    "content": [{"text": f"Error: Could not resolve endpoint for {agent_id}"}]
                }
                return

        logger.debug(f"Invoking A2A agent {agent_id}")

        httpx_client = get_http_client(region, auth_token=auth_token)
        owns_httpx_client = True

        # Add session ID header (must be >= 33 characters)
        if not session_id:
            session_id = str(uuid4()) + "-" + str(uuid4())[:8]  # UUID (36) + dash + 8 chars = 45 chars

        # Ensure session ID meets minimum length requirement
        if len(session_id) < 33:
            session_id = session_id + "-" + str(uuid4())[:max(0, 33 - len(session_id) - 1)]

        headers = {
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id
        }
        httpx_client.headers.update(headers)

        agent_card = AgentCard(
            url=runtime_url,
            name=agent_id,
            description="",
            version="1.0.0",
            capabilities={"streaming": True},
            defaultInputModes=["text/plain"],
            defaultOutputModes=["text/plain"],
            skills=[],
        )
        config = ClientConfig(httpx_client=httpx_client, streaming=True)
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        msg = Message(
            kind="message",
            role=Role.user,
            parts=[Part(TextPart(kind="text", text=message))],
            message_id=uuid4().hex,
            metadata=metadata,
        )


        current_task_id = None
        completed = False
        response = A2ATextAccumulator()
        code_result_meta = None
        sent_code_steps = set()
        sent_code_todos = set()
        sent_research_steps = set()
        sent_delegation_steps = set()

        def _extract_text(parts):
            for p in (parts or []):
                if hasattr(p, 'root') and hasattr(p.root, 'text'):
                    return p.root.text
                if hasattr(p, 'text'):
                    return p.text
            return ""

        def _process_artifact(artifact):
            """Process a single artifact, return yield-able event or None."""
            nonlocal code_result_meta
            name = getattr(artifact, 'name', 'unnamed')
            text = _extract_text(getattr(artifact, 'parts', []))

            if name.startswith('research_step_'):
                try:
                    n = int(name.split('_')[-1])
                    if n not in sent_research_steps and text:
                        sent_research_steps.add(n)
                        return {"type": "research_step", "stepNumber": n, "content": text}
                except (ValueError, IndexError):
                    pass
            elif name.startswith('delegation_step_'):
                step_id = name.removeprefix('delegation_step_')
                if step_id not in sent_delegation_steps and text:
                    sent_delegation_steps.add(step_id)
                    return {
                        "type": "delegation_step",
                        "stepNumber": len(sent_delegation_steps),
                        "content": text,
                    }
            elif name.startswith('code_step_'):
                try:
                    n = int(name.split('_')[-1])
                    if n not in sent_code_steps and text:
                        sent_code_steps.add(n)
                        return {"type": "code_step", "stepNumber": n, "content": text}
                except (ValueError, IndexError):
                    pass
            elif name.startswith('code_todos_'):
                try:
                    n = int(name.split('_')[-1])
                    if n not in sent_code_todos and text:
                        import json as _json
                        sent_code_todos.add(n)
                        return {"type": "code_todo_update", "todos": _json.loads(text)}
                except Exception:
                    pass
            elif name == 'code_result':
                try:
                    import json as _json
                    result_data = _json.loads(text)
                    response.add(result_data.get("summary", ""))
                    code_result_meta = {
                        "files_changed": result_data.get("files_changed", []),
                        "todos": result_data.get("todos", []),
                        "steps": result_data.get("steps", 0),
                    }
                except Exception:
                    response.add(text)
            elif text:
                response.add(text)
            return None

        async with asyncio.timeout(AGENT_TIMEOUT):
            async for event in client.send_message(msg):
                if isinstance(event, Message):
                    for part in (event.parts or []):
                        t = _extract_text([part])
                        if t:
                            response.add(t)
                    break

                if isinstance(event, tuple) and len(event) == 2:
                    task, update_event = event

                    if current_task_id is None and hasattr(task, 'id'):
                        current_task_id = task.id
                        yield {
                            "type": "a2a_task_started",
                            "taskId": current_task_id,
                        }

                    task_status = task.status if hasattr(task, 'status') else task
                    state = str(getattr(task_status, 'state', 'unknown'))

                    # Accumulate text from status message
                    if hasattr(task_status, 'message') and task_status.message:
                        txt = _extract_text(getattr(task_status.message, 'parts', []))
                        if txt:
                            response.add(txt)

                    # Process artifacts
                    for artifact in (getattr(task, 'artifacts', None) or []):
                        evt = _process_artifact(artifact)
                        if evt:
                            yield evt

                    if 'failed' in state:
                        error_msg = _extract_text(
                            getattr(task_status.message, 'parts', []) if hasattr(task_status, 'message') and task_status.message else []
                        )
                        yield {"status": "error", "content": [{"text": response.text or error_msg or "Agent task failed"}]}
                        return

                    if 'completed' in state:
                        break

                    if update_event and getattr(update_event, 'final', False):
                        break

        # Yield structured code-agent metadata before the final result (frontend use)
        if code_result_meta is not None:
            yield {
                "type": "code_result_meta",
                **code_result_meta,
            }

        # Yield final result
        completed = True
        logger.debug(f"Final A2A response: {len(response.text)} chars")
        yield {
            "status": "success",
            "content": [{
                "text": response.text or "Task completed successfully"
            }]
        }

    except asyncio.TimeoutError:
        logger.warning(f"Timeout calling {agent_id} agent")
        yield {
            "status": "error",
            "content": [{
                "text": f"Agent {agent_id} timed out after {AGENT_TIMEOUT}s"
            }]
        }
    except Exception as e:
        logger.error(f"Error calling {agent_id}: {e}")
        logger.exception(e)
        yield {
            "status": "error",
            "content": [{
                "text": f"Error: {str(e)}"
            }]
        }
    finally:
        if not completed and current_task_id and client:
            try:
                await client.cancel_task(current_task_id)
                logger.info(f"[A2A] Cancelled task {current_task_id} on {agent_id}")
            except Exception as e:
                logger.warning(f"[A2A] Failed to cancel task {current_task_id} on {agent_id}: {e}")
        if owns_httpx_client and httpx_client:
            try:
                await httpx_client.aclose()
            except Exception as e:
                logger.warning(f"[A2A] Failed to close HTTP client for {agent_id}: {e}")


# ============================================================
# Factory Function - Creates Direct A2A Agent Tool
# ============================================================

def create_a2a_tool(agent_id: str):
    """
    Create a direct callable tool for the A2A agent

    Args:
        agent_id: Tool ID (e.g., "agentcore_research-agent", "agentcore_code-agent")

    Returns:
        Strands tool function, or None if not found
    """
    from registry.client import get_registry_client
    client = get_registry_client()
    skill_name = agent_id.replace("agentcore_", "")
    skill = client.get_a2a_skill(skill_name) if client else None
    if not skill:
        logger.warning(f"Unknown A2A agent: {agent_id}")
        return None

    agent_description = skill.description

    logger.debug(f"Creating A2A tool: {agent_id}")

    region = os.environ.get('AWS_REGION', 'us-west-2')
    agent_url = get_cached_agent_url(agent_id)
    if not agent_url:
        logger.error(f"Failed to get endpoint for {agent_id}")
        return None

    # Helper function to extract context
    def extract_context(tool_context):
        session_id = None
        user_id = None
        model_id = None
        _auth_token = None

        if tool_context:
            session_id = tool_context.invocation_state.get("session_id")
            user_id = tool_context.invocation_state.get("user_id")
            model_id = tool_context.invocation_state.get("model_id")

            if not session_id and hasattr(tool_context.agent, '_session_manager'):
                session_id = tool_context.agent._session_manager.session_id

            if not user_id and hasattr(tool_context.agent, 'user_id'):
                user_id = tool_context.agent.user_id

            if not model_id:
                if hasattr(tool_context.agent, 'model_id'):
                    model_id = tool_context.agent.model_id
                elif hasattr(tool_context.agent, 'model') and hasattr(tool_context.agent.model, 'model_id'):
                    model_id = tool_context.agent.model.model_id

            _auth_token = tool_context.invocation_state.get("auth_token")

        if not session_id:
            session_id = os.environ.get('SESSION_ID')
        if not user_id:
            user_id = os.environ.get('USER_ID')

        return session_id, user_id, model_id, _auth_token

    # Generate correct tool name BEFORE creating function
    correct_name = agent_id.replace("agentcore_", "").replace("-", "_")

    # Create different tool implementations based on agent type
    if "code" in agent_id:
        # Code Agent - task parameter, streams code_step events
        async def tool_impl(
            task: str,
            task_complexity: Literal["low", "medium", "high"] = "medium",
            reset_session: bool = False,
            compact_session: bool = False,
            tool_context: ToolContext = None,
        ) -> AsyncGenerator[Dict[str, Any], None]:
            """
            task: The coding task to delegate.
            reset_session: Set True to clear conversation history and start fresh
                           (equivalent to /clear in Claude Code). Workspace files
                           are preserved — only the conversation context is wiped.
            compact_session: Set True to summarise conversation history before
                             running the task (equivalent to /compact in Claude Code).
                             Useful when prior context is long but still relevant.
            task_complexity: Claude model tier: low, medium, or high.
            """
            session_id, user_id, model_id, _auth_token = extract_context(tool_context)
            model_selection = resolve_code_agent_model(task_complexity)

            metadata = {
                "session_id": session_id,
                "user_id": user_id,
                "source": "main_agent",
                # Claude Agent SDK has its own provider-specific model setting.
                "model_id": model_selection.effective_model_id,
                "orchestrator_model_id": model_id,
                "task_complexity": model_selection.task_complexity,
                "model_selection": model_selection.as_record(),
                "reset_session": reset_session,
                "compact_session": compact_session,
            }

            # Track partial progress in invocation_state so that if stop signal
            # interrupts this tool, the event processor can inject a meaningful
            # tool_result with progress context into Strands conversation history.
            progress = {
                "agent": agent_id,
                "task": task[:500],
                "steps": [],
                "files_changed": [],
                "todos": [],
                "status": "running",
            }
            if tool_context:
                tool_context.invocation_state["_a2a_partial_progress"] = progress

            async for event in send_a2a_message(agent_id, task, session_id, region, metadata=metadata, auth_token=_auth_token):
                # Update partial progress from streamed events
                if isinstance(event, dict) and tool_context:
                    event_type = event.get("type")
                    if event_type == "code_step":
                        progress["steps"].append(event.get("content", ""))
                    elif event_type == "code_result_meta":
                        progress["files_changed"] = event.get("files_changed", [])
                        progress["todos"] = event.get("todos", [])
                        progress["status"] = event.get("status", "completed")

                yield event

            # Clear progress on normal completion (no longer partial)
            if tool_context:
                tool_context.invocation_state.pop("_a2a_partial_progress", None)

        tool_impl.__name__ = correct_name
        tool_impl.__doc__ = f"""{agent_description}

        Args:
            task: The coding task to delegate.
            task_complexity: Claude model tier: low, medium, or high.
            reset_session: Clear conversation history while preserving files.
            compact_session: Compact prior conversation before the task.
        """
        agent_tool = tool(context=True)(tool_impl)
        agent_tool._skill_name = skill_name

    else:
        # Research Agent (default) - return immediately while a durable job owns
        # the A2A stream, persistence, and eventual delivery to the supervisor.
        async def tool_impl(plan: str, tool_context: ToolContext = None) -> str:
            session_id, user_id, model_id, _auth_token = extract_context(tool_context)

            # Scope the research agent's session to this call rather than reusing
            # the chat's session. The research agent derives its report workspace
            # and chart S3 prefix from this id, so two researches in one chat
            # would otherwise share a workspace and overwrite each other's
            # research_report.md. Derived from toolUseId so it is stable for
            # retries of the same call.
            tool_use_id = ""
            if tool_context and getattr(tool_context, 'tool_use', None):
                tool_use_id = tool_context.tool_use.get('toolUseId', '') or ""
            research_session_id = f"{session_id}-{tool_use_id}" if tool_use_id else session_id

            # Prepare metadata. session_id is the per-research one so the agent's
            # workspace matches the session header below.
            metadata = {
                "session_id": research_session_id,
                "user_id": user_id,
                "source": "main_agent",
                "model_id": model_id,
                "language": "en",
            }


            artifact_id = f"research-{tool_use_id}" if tool_use_id else f"research-{uuid4().hex}"

            from agent.research_jobs import start_research_job

            def event_factory():
                return send_a2a_message(
                    agent_id,
                    plan,
                    research_session_id,
                    region,
                    metadata=metadata,
                    auth_token=_auth_token,
                )

            receipt = start_research_job(
                session_id=session_id or research_session_id,
                user_id=user_id or "anonymous",
                plan=plan,
                artifact_id=artifact_id,
                event_factory=event_factory,
                model_id=model_id,
            )
            return __import__("json").dumps(receipt)

        # Set correct function name and docstring BEFORE decorating
        tool_impl.__name__ = correct_name
        tool_impl.__doc__ = agent_description

        # Now apply the decorator to get the tool
        agent_tool = tool(context=True)(tool_impl)
        agent_tool._skill_name = skill_name

    logger.debug(f"A2A tool created: {agent_tool.__name__}")
    return agent_tool


# Cleanup on shutdown
async def cleanup():
    return None
