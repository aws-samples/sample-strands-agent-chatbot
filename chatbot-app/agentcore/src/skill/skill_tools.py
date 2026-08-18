"""
Skill infrastructure tools for progressive disclosure.

  skill_dispatcher  — Level 2: loads SKILL.md instructions for the LLM
  skill_executor    — Level 3: executes a skill's tool internally and returns the result
"""

import asyncio
import concurrent.futures
import copy
import json
import logging
import os
from datetime import timedelta

from strands import tool
from strands.types.tools import ToolContext

from skill.tool_names import canonical_tool_name

logger = logging.getLogger(__name__)

# Module-level registry reference, set by SkillChatAgent during init
_registry = None
_DEFAULT_SKILL_RESULT_MAX_CHARS = 100_000


def _skill_result_max_chars() -> int:
    try:
        return max(1_000, int(os.getenv(
            "SKILL_RESULT_MAX_CHARS",
            str(_DEFAULT_SKILL_RESULT_MAX_CHARS),
        )))
    except ValueError:
        return _DEFAULT_SKILL_RESULT_MAX_CHARS


def _truncate_result_text(text: str, max_chars: int) -> str:
    """Keep a bounded start/end preview of oversized tool text."""
    if len(text) <= max_chars:
        return text
    marker = f"\n... [tool result truncated, {len(text) - max_chars:,} chars omitted] ...\n"
    if max_chars <= len(marker):
        return marker[:max_chars]
    preview_chars = max(0, max_chars - len(marker))
    start_chars = (preview_chars + 1) // 2
    end_chars = preview_chars // 2
    return text[:start_chars] + marker + (text[-end_chars:] if end_chars else "")


def _strip_inline_base64_envelope(value):
    """Remove binary text from common JSON envelopes while retaining metadata."""
    if not isinstance(value, dict):
        return value
    if str(value.get("encoding", "")).lower() != "base64":
        return value
    if not isinstance(value.get("content"), str):
        return value

    sanitized = dict(value)
    sanitized.pop("content", None)
    sanitized["content_omitted"] = True
    sanitized.setdefault(
        "message",
        "Inline base64 content was omitted. Use the appropriate file-processing tool.",
    )
    return sanitized


def _content_embeds_metadata(content) -> bool:
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        try:
            parsed = json.loads(block["text"])
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and "metadata" in parsed:
            return True
    return False


def _sanitize_content_text(text: str, max_chars: int) -> str:
    """Sanitize a text block while keeping structured response envelopes valid."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return _truncate_result_text(text, max_chars)

    sanitized = _strip_inline_base64_envelope(parsed)
    if isinstance(sanitized, dict) and isinstance(sanitized.get("text"), str):
        nested_text = sanitized["text"]
        without_text = dict(sanitized)
        without_text["text"] = ""
        overhead = len(json.dumps(without_text, ensure_ascii=False, default=str))
        nested_limit = max(0, max_chars - overhead)
        for _ in range(3):
            sanitized["text"] = _truncate_result_text(nested_text, nested_limit)
            serialized = json.dumps(sanitized, ensure_ascii=False, default=str)
            overflow = len(serialized) - max_chars
            if overflow <= 0:
                return serialized
            nested_limit = max(0, nested_limit - overflow)

    serialized = json.dumps(sanitized, ensure_ascii=False, default=str)
    return _truncate_result_text(serialized, max_chars)


def _sanitize_skill_result(result, max_chars: int | None = None):
    """Bound textual tool output without modifying native image/document blocks."""
    limit = max_chars or _skill_result_max_chars()

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError):
            return _truncate_result_text(result, limit)

        sanitized = _strip_inline_base64_envelope(parsed)
        serialized = json.dumps(sanitized, ensure_ascii=False, default=str)
        return _truncate_result_text(serialized, limit)

    if isinstance(result, dict):
        sanitized = copy.deepcopy(_strip_inline_base64_envelope(result))
        content = sanitized.get("content")
        if isinstance(content, list):
            remaining = limit
            for block in content:
                if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                    continue
                text = block["text"]
                block["text"] = _sanitize_content_text(text, remaining)
                remaining = max(0, remaining - len(block["text"]))
            if "metadata" in sanitized and _content_embeds_metadata(content):
                sanitized.pop("metadata", None)
            return sanitized

        serialized = json.dumps(sanitized, ensure_ascii=False, default=str)
        if len(serialized) > limit:
            return _truncate_result_text(serialized, limit)
        return sanitized

    text = str(result)
    return _truncate_result_text(text, limit)


def set_dispatcher_registry(registry) -> None:
    """Wire up the dispatcher/executor with a SkillRegistry instance."""
    global _registry
    _registry = registry


def _run_async(coro):
    """Run an async coroutine from a synchronous context.

    Handles the case where an event loop may already be running
    (e.g., inside an async framework like FastAPI).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


_SKILL_STREAM_TYPES = ("code_step", "code_todo_update", "code_result_meta",
                       "code_agent_started", "code_agent_heartbeat",
                       "research_step", "research_progress")


async def _consume_async_generator(
    agen,
    session_id=None,
    user_id=None,
    run_id=None,
    is_code_agent=False,
):
    """Consume an async generator and return the final result text.

    A2A tools yield event dicts while running, then a final
    {"status": "success"/"error", "content": [{"text": "..."}]} event.
    Intermediate progress events (code_step, code_todo_update, code_result_meta)
    are forwarded to the side-channel queue so process_stream can drain and
    yield them to the frontend in real time.
    """
    import time

    # Only Code Agent uses the dedicated code progress UI.
    if session_id and is_code_agent:
        from streaming import skill_event_bus
        q = skill_event_bus.get_queue(session_id)
        if q is not None:
            q.put_nowait({"type": "code_agent_started"})

    final_text = None
    final_status = "success"
    start_time = time.time()
    last_activity = start_time
    code_steps: list[str] = []  # Accumulate code steps for tool result
    stop_event = None
    if user_id and session_id and run_id:
        from agent.stop_signal import get_local_stop_event
        stop_event = get_local_stop_event(user_id, session_id, run_id)

    iterator = agen.__aiter__()
    while True:
        next_event_task = asyncio.create_task(iterator.__anext__())
        stopped = False
        while not next_event_task.done():
            await asyncio.wait({next_event_task}, timeout=0.1)
            if stop_event and stop_event.is_set():
                stopped = True
                next_event_task.cancel()
                try:
                    await next_event_task
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
                break

        if stopped:
            logger.info(
                "[SkillExecutor] Cancelling active A2A task for "
                f"{user_id}:{session_id}:{run_id}"
            )
            if hasattr(iterator, "aclose"):
                await iterator.aclose()
            return json.dumps({
                "status": "cancelled",
                "message": "A2A task stopped by user",
            })

        try:
            event = next_event_task.result()
        except StopAsyncIteration:
            break

        now = time.time()
        if not isinstance(event, dict):
            # Send heartbeat if no real event for 10+ seconds
            if session_id and is_code_agent and (now - last_activity) >= 10:
                from streaming import skill_event_bus
                q = skill_event_bus.get_queue(session_id)
                if q is not None:
                    raw = int(now - start_time)
                    q.put_nowait({
                        "type": "code_agent_heartbeat",
                        "elapsed_seconds": (raw // 10) * 10,
                    })
                last_activity = now
            continue

        last_activity = now
        # Forward intermediate skill events to the side-channel queue (thread-safe)
        if event.get("type") in _SKILL_STREAM_TYPES and session_id:
            from streaming import skill_event_bus
            q = skill_event_bus.get_queue(session_id)
            if q is not None:
                q.put_nowait(event)

        # Accumulate code steps so the agent knows what happened
        if event.get("type") == "code_step":
            step_content = event.get("content", "")
            if step_content:
                code_steps.append(step_content)

        status = event.get("status")
        if status in ("success", "error"):
            content = event.get("content", [])
            if content and isinstance(content[0], dict):
                final_text = content[0].get("text", "")
            final_status = status

    if final_status == "error":
        return json.dumps({"status": "error", "error": final_text or "A2A tool failed"})

    # Include code steps in the tool result so the agent understands the execution process
    # Filter out the last step if it duplicates the final summary text
    if code_steps and final_text:
        last = code_steps[-1]
        if last.strip() == final_text.strip() or final_text.strip().startswith(last.strip()):
            code_steps = code_steps[:-1]
    if code_steps:
        steps_log = "\n".join(f"- {s}" for s in code_steps)
        result = f"{final_text}\n\n<execution_steps>\n{steps_log}\n</execution_steps>"
        return result

    return final_text or json.dumps({"status": "success", "result": ""})


@tool
def skill_dispatcher(skill_name: str, reference: str = "", source: str = "") -> str:
    """Activate a skill, read a reference document, or read a tool's source code.

    **Basic activation** — call with just skill_name to receive SKILL.md instructions:
        skill_dispatcher(skill_name="web-search")

    **Read reference doc** — call with a reference filename for additional documentation:
        skill_dispatcher(skill_name="powerpoint-presentations", reference="editing-guide.md")

    **Read source code** — call with a function name to read its implementation:
        skill_dispatcher(skill_name="powerpoint-presentations", source="create_presentation")

    Args:
        skill_name: Name of the skill to activate (e.g. "web-search")
        reference: Optional filename of a reference document to read from the skill directory.
        source: Optional function name to read its source code implementation.

    Returns:
        JSON with skill instructions, reference content, or source code
    """
    if _registry is None:
        return json.dumps({
            "error": "SkillRegistry not initialized.",
            "status": "error",
        })

    try:
        # Source code mode: return function implementation
        if source:
            code = _registry.load_source(skill_name, source)
            logger.info(f"Skill source loaded: '{skill_name}/{source}'")
            return json.dumps({
                "skill": skill_name,
                "function": source,
                "source_code": code,
                "status": "ok",
            })

        # Reference file mode: return the requested document
        if reference:
            content = _registry.load_reference(skill_name, reference)
            logger.info(f"Skill reference loaded: '{skill_name}/{reference}'")
            return json.dumps({
                "skill": skill_name,
                "reference": reference,
                "content": content,
                "status": "ok",
            })

        # Normal activation: return SKILL.md + tool list with schemas + sources + references + scripts
        instructions = _registry.load_instructions(skill_name)
        tools = _registry.get_tools(skill_name)
        sources = _registry.list_sources(skill_name)
        references = _registry.list_references(skill_name)
        scripts = _registry.list_scripts(skill_name)

        # Build tool info with input schemas so the LLM knows exact parameters
        tool_schemas = []
        for t in tools:
            spec = getattr(t, "tool_spec", None)
            if spec and isinstance(spec, dict):
                schema = spec.get("inputSchema", {}).get("json", {})
                tool_schemas.append({
                    "name": canonical_tool_name(t),
                    "description": spec.get("description", ""),
                    "parameters": schema,
                })
            else:
                tool_schemas.append({"name": canonical_tool_name(t)})

        logger.info(f"Skill dispatched: '{skill_name}' — tools: {[s['name'] for s in tool_schemas]}")

        result = {
            "skill": skill_name,
            "instructions": instructions,
            "available_tools": tool_schemas,
            "status": "activated",
            "next_step": "Use skill_executor to call tools or run scripts.",
        }

        if sources:
            result["available_sources"] = [s["function"] for s in sources]

        if references:
            result["available_references"] = references

        if scripts:
            result["available_scripts"] = scripts
            result["script_usage"] = (
                "To run a script: skill_executor("
                "skill_name='...', script_name='...', script_input={...})"
            )

        return json.dumps(result)

    except KeyError as e:
        return json.dumps({
            "error": str(e),
            "available_skills": _registry.skill_names,
            "status": "error",
        })

    except (FileNotFoundError, ValueError) as e:
        return json.dumps({
            "error": str(e),
            "status": "error",
        })

    except Exception as e:
        logger.error(f"Error dispatching skill '{skill_name}': {e}")
        return json.dumps({"error": str(e), "status": "error"})


@tool(context=True)
def skill_executor(
    tool_context: ToolContext,
    skill_name: str,
    tool_name: str = None,
    tool_input = None,
    script_name: str = None,
    script_input = None,
) -> str:
    """Execute a tool or script from an activated skill.

    Tool execution:
        skill_executor(
            skill_name="web-search",
            tool_name="ddg_web_search",
            tool_input={"query": "AI", "max_results": 5}
        )

    Script execution:
        skill_executor(
            skill_name="web-search",
            script_name="cleanup_cache.py",
            script_input={"days": 30}
        )

    Args:
        skill_name: Name of the activated skill (e.g. "web-search")
        tool_name: Name of the tool to execute (mutually exclusive with script_name)
        tool_input: Dictionary of input parameters for the tool
        script_name: Name of the script to run (mutually exclusive with tool_name)
        script_input: Dictionary of input parameters for the script

    Returns:
        The tool/script execution result
    """
    if _registry is None:
        return json.dumps({
            "error": "SkillRegistry not initialized.",
            "status": "error",
        })

    # Normalize tool_input / script_input: LLM sometimes passes a JSON string instead of a dict
    def _coerce_to_dict(value):
        if value is None or isinstance(value, dict):
            return value
        if isinstance(value, str):
            # Strip trailing XML artifacts the LLM may append (e.g. "\n</invoke>")
            cleaned = value.strip()
            if '</invoke>' in cleaned:
                cleaned = cleaned[:cleaned.rfind('</invoke>')].rstrip()
            try:
                parsed = json.loads(cleaned)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    tool_input = _coerce_to_dict(tool_input)
    script_input = _coerce_to_dict(script_input)

    # Validation: must specify either tool_name or script_name, not both
    if tool_name and script_name:
        return json.dumps({
            "error": "Cannot specify both tool_name and script_name",
            "status": "error",
        })

    if not tool_name and not script_name:
        return json.dumps({
            "error": "Must specify either tool_name or script_name",
            "status": "error",
        })

    try:
        # ========== Script Execution Path ==========
        if script_name:
            result = _execute_script(
                tool_context=tool_context,
                skill_name=skill_name,
                script_name=script_name,
                script_input=script_input or {},
            )
            return _sanitize_skill_result(result)

        # ========== Tool Execution Path ==========
        if tool_name:
            result = _execute_tool(
                tool_context=tool_context,
                skill_name=skill_name,
                tool_name=tool_name,
                tool_input=tool_input or {},
            )
            return _sanitize_skill_result(result)

    except Exception as e:
        logger.error(f"Error executing {skill_name}/{tool_name or script_name}: {e}")
        return json.dumps({
            "error": str(e),
            "skill": skill_name,
            "status": "error",
        })


def _execute_tool(
    tool_context: ToolContext,
    skill_name: str,
    tool_name: str,
    tool_input: dict,
) -> str:
    """Execute a tool (existing logic extracted for clarity)."""
    try:
        # Find the tool in the skill's tool list
        tools = _registry.get_tools(skill_name)
        target_tool = None
        for t in tools:
            if canonical_tool_name(t) == tool_name:
                target_tool = t
                break

        if target_tool is None:
            available = [canonical_tool_name(t) for t in tools]
            return json.dumps({
                "error": f"Tool '{tool_name}' not found in skill '{skill_name}'.",
                "available_tools": available,
                "status": "error",
            })

        logger.info(f"Executing {skill_name}/{tool_name} with input: {tool_input}")

        # Determine execution path based on tool type
        is_mcp_tool = hasattr(target_tool, 'mcp_client')

        # Timeout for MCP tool calls (seconds).
        # Must exceed the OAuth elicitation bridge timeout (300s) to allow
        # the user to complete 3LO consent without being prematurely killed.
        MCP_TOOL_TIMEOUT = 360

        if is_mcp_tool:
            # MCP tool — delegate to mcp_client.call_tool_sync()
            # Uses the original MCP tool name for server communication
            mcp_result = target_tool.mcp_client.call_tool_sync(
                tool_use_id=tool_context.tool_use.get("toolUseId", "skill-exec"),
                name=target_tool.mcp_tool.name,
                arguments=tool_input,
                read_timeout_seconds=timedelta(seconds=MCP_TOOL_TIMEOUT),
            )

            # Extract text content from MCPToolResult for the LLM
            content_parts = mcp_result.get("content", [])
            texts = []
            for part in content_parts:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(part["text"])

            result = "\n".join(texts) if texts else json.dumps(mcp_result)

        else:
            # Local tool — direct function call
            call_kwargs = dict(tool_input)
            context_param = target_tool._metadata._context_param
            if context_param:
                target_context = ToolContext(
                    tool_use=tool_context.tool_use,
                    agent=tool_context.agent,
                    invocation_state=tool_context.invocation_state,
                )
                call_kwargs[context_param] = target_context

            func = target_tool._tool_func
            result = func(**call_kwargs)

            # Handle coroutines (async local tools)
            if asyncio.iscoroutine(result):
                result = _run_async(result)

            # Handle async generators (A2A tools that stream events)
            elif hasattr(result, '__aiter__'):
                session_id = tool_context.invocation_state.get("session_id")
                user_id = tool_context.invocation_state.get("user_id")
                run_id = tool_context.invocation_state.get("run_id")
                target_name = canonical_tool_name(target_tool)
                result = _run_async(_consume_async_generator(
                    result,
                    session_id=session_id,
                    user_id=user_id,
                    run_id=run_id,
                    is_code_agent="code" in target_name,
                ))

        logger.info(f"Executed {skill_name}/{tool_name} successfully")
        return result

    except KeyError as e:
        return json.dumps({
            "error": str(e),
            "available_skills": _registry.skill_names,
            "status": "error",
        })

    except Exception as e:
        logger.error(f"Error executing {skill_name}/{tool_name}: {e}")
        return json.dumps({
            "error": str(e),
            "skill": skill_name,
            "tool": tool_name,
            "status": "error",
        })


def _dict_to_cli_args(params: dict) -> str:
    """Convert a dict to CLI argument string.

    Conversion rules:
      - key "fetch_url" or "fetch-url" → --fetch-url
      - bool True  → --flag (present)
      - bool False → omitted
      - list       → --key v1 --key v2
      - other      → --key 'value'
    """
    import shlex

    args = []
    for key, value in params.items():
        flag = "--" + key.replace("_", "-")

        if isinstance(value, bool):
            if value:
                args.append(flag)
        elif isinstance(value, list):
            for item in value:
                args.append(f"{flag} {shlex.quote(str(item))}")
        else:
            args.append(f"{flag} {shlex.quote(str(value))}")

    return " ".join(args)


def _execute_script(
    tool_context: ToolContext,
    skill_name: str,
    script_name: str,
    script_input: dict,
) -> str:
    """Execute a script from a skill's scripts/ directory using shell tool."""
    import os
    import sys
    from strands_tools.shell import shell

    try:
        # Get script info from registry
        script_info = _registry.get_script(skill_name, script_name)
        script_path = script_info["path"]

        logger.info(f"Executing script: {skill_name}/{script_name}")
        logger.debug(f"Script path: {script_path}")
        logger.debug(f"Script input: {script_input}")

        # Security: verify script is within skill directory
        skill_dir = os.path.join(_registry.skills_dir, skill_name)
        script_abs = os.path.abspath(script_path)
        skill_abs = os.path.abspath(skill_dir)

        if not script_abs.startswith(skill_abs):
            return json.dumps({
                "error": "Security violation: script outside skill directory",
                "status": "error",
            })

        # Build command based on file extension
        if script_name.endswith('.py'):
            cmd = f"{sys.executable} {script_path}"
        elif script_name.endswith('.sh'):
            cmd = f"/bin/bash {script_path}"
        else:
            return json.dumps({
                "error": f"Unsupported script type: {script_name}",
                "status": "error",
            })

        # Convert script_input dict to CLI arguments
        if script_input:
            cmd = cmd + " " + _dict_to_cli_args(script_input)

        # Get user context from invocation_state
        session_id = tool_context.invocation_state.get("session_id", "")
        user_id = tool_context.invocation_state.get("user_id", "")

        # Set environment variables via shell command
        env_vars = [
            f"SKILL_NAME={skill_name}",
            f"SCRIPT_NAME={script_name}",
        ]
        if session_id:
            env_vars.append(f"SESSION_ID={session_id}")
        if user_id:
            env_vars.append(f"USER_ID={user_id}")

        # Prepend env vars to command
        cmd = " ".join(env_vars) + " " + cmd

        logger.debug(f"Executing command: {cmd}")

        # Execute script with shell tool
        result = shell(
            command=cmd,
            work_dir=skill_dir,
            timeout=300,  # 5 minutes default
            non_interactive=True,  # Auto-execute without user prompt
        )

        # Parse result from shell tool
        # shell() returns dict with status and content
        if isinstance(result, dict):
            status = result.get("status", "error")
            content = result.get("content", [])

            # Extract text from content
            output_texts = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    output_texts.append(item["text"])

            output = "\n".join(output_texts)

            logger.info(
                f"Script execution completed: {skill_name}/{script_name} "
                f"(status={status})"
            )

            return json.dumps({
                "status": status,
                "script": script_name,
                "output": output,
            })
        else:
            # Fallback: treat as string result
            return json.dumps({
                "status": "success",
                "script": script_name,
                "output": str(result),
            })

    except KeyError as e:
        return json.dumps({
            "error": str(e),
            "available_scripts": _registry.list_scripts(skill_name),
            "status": "error",
        })

    except Exception as e:
        logger.error(f"Script execution failed: {e}", exc_info=True)
        return json.dumps({
            "error": f"Script execution failed: {str(e)}",
            "status": "error",
        })
