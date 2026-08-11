"""Supervisor tools for durable asynchronous delegation."""

from __future__ import annotations

import json
from typing import Literal, Optional

from strands import ToolContext, tool

from agent.config.model_catalog import normalize_task_complexity
from agent.delegation_jobs import (
    cancel_job,
    list_jobs,
    start_job,
)
from skill import register_skill

_MAX_GOAL_CHARS = 4_000
_MAX_CONTEXT_CHARS = 6_000
_MAX_LIST_ITEMS = 20


def _bounded_text(value: str, limit: int, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return normalized


def _bounded_list(values: Optional[list[str]], field: str) -> list[str]:
    normalized = [str(value).strip() for value in (values or []) if str(value).strip()]
    if len(normalized) > _MAX_LIST_ITEMS:
        raise ValueError(f"{field} must contain at most {_MAX_LIST_ITEMS} items")
    return normalized


def _context(
    tool_context: ToolContext,
) -> tuple[str, str, str, str, str, str]:
    state = tool_context.invocation_state
    session_id = str(state.get("session_id") or "")
    user_id = str(state.get("user_id") or "anonymous")
    run_id = str(state.get("run_id") or "")
    model_id = str(state.get("model_id") or "")
    auth_token = str(state.get("auth_token") or "")
    if not session_id:
        raise ValueError("Delegation requires an active session")
    tool_use = getattr(tool_context, "tool_use", None) or {}
    tool_use_id = str(tool_use.get("toolUseId") or "")
    if not tool_use_id:
        raise ValueError("Delegation requires a stable tool use ID")
    return session_id, user_id, run_id, tool_use_id, model_id, auth_token


def _agent_job_view(record: dict) -> dict:
    request = record.get("request") or {}
    view = {
        "profile": record.get("profile"),
        "status": record.get("executionStatus"),
        "delivery_status": record.get("deliveryStatus"),
        "goal": request.get("goal"),
        "deliverable": request.get("deliverable"),
    }
    for source, target in (
        ("resultSummary", "summary"),
        ("artifacts", "artifacts"),
        ("error", "error"),
    ):
        if record.get(source):
            view[target] = record[source]
    return view


def _matching_jobs(
    user_id: str,
    session_id: str,
    *,
    profile: str = "",
    goal_contains: str = "",
    active_only: bool = False,
) -> list[dict]:
    normalized_profile = profile.strip().lower()
    if normalized_profile and normalized_profile not in {"analyst", "reviewer"}:
        raise ValueError("profile must be analyst or reviewer")
    normalized_goal = goal_contains.strip().casefold()
    terminal = {"succeeded", "failed", "cancelled", "timed_out"}
    matches = []
    for record in list_jobs(user_id, session_id):
        request = record.get("request") or {}
        if normalized_profile and record.get("profile") != normalized_profile:
            continue
        if (
            normalized_goal
            and normalized_goal not in str(request.get("goal") or "").casefold()
        ):
            continue
        if active_only and record.get("executionStatus") in terminal:
            continue
        matches.append(record)
    return sorted(
        matches,
        key=lambda record: str(record.get("createdAt") or ""),
        reverse=True,
    )


@tool(context=True)
def delegate_task(
    profile: str,
    goal: str,
    deliverable: str,
    acceptance_criteria: Optional[list[str]] = None,
    workspace_paths: Optional[list[str]] = None,
    constraints: Optional[list[str]] = None,
    context_summary: str = "",
    max_seconds: int = 600,
    task_complexity: Optional[Literal["low", "medium", "high"]] = None,
    tool_context: ToolContext = None,
) -> str:
    """Delegate one independent, scoped task to an asynchronous specialist.

    The call returns immediately after the durable job is accepted. Use this
    only when the parent can continue without waiting for the result.

    Args:
        profile: Specialist profile. Allowed values: analyst, reviewer.
        goal: One concrete objective.
        deliverable: One expected output.
        acceptance_criteria: Observable completion criteria.
        workspace_paths: Session workspace files relevant to the task.
        constraints: Explicit non-goals or restrictions.
        context_summary: Minimal task-local context, never the full conversation.
        max_seconds: Execution deadline from 30 to 1800 seconds.
        task_complexity: Optional model tier: low, medium, or high. Anthropic
            and OpenAI parent models use the matching catalog tier; other
            providers keep the parent model.
    """
    if profile not in {"analyst", "reviewer"}:
        raise ValueError("profile must be analyst or reviewer")
    if not 30 <= int(max_seconds) <= 1800:
        raise ValueError("max_seconds must be between 30 and 1800")

    session_id, user_id, run_id, tool_use_id, model_id, _auth_token = _context(
        tool_context
    )
    complexity = normalize_task_complexity(task_complexity)
    request = {
        "schemaVersion": 2,
        "goal": _bounded_text(goal, _MAX_GOAL_CHARS, "goal"),
        "deliverable": _bounded_text(
            deliverable,
            1_000,
            "deliverable",
        ),
        "acceptanceCriteria": _bounded_list(
            acceptance_criteria,
            "acceptance_criteria",
        ),
        "workspacePaths": _bounded_list(workspace_paths, "workspace_paths"),
        "constraints": _bounded_list(constraints, "constraints"),
        "contextSummary": (
            _bounded_text(context_summary, _MAX_CONTEXT_CHARS, "context_summary")
            if context_summary.strip()
            else ""
        ),
        "taskComplexity": complexity or "",
        "budget": {
            "maxSeconds": int(max_seconds),
            "maxToolCalls": 20 if profile == "analyst" else 12,
        },
    }
    idempotency_key = f"{session_id}:{run_id}:{tool_use_id}"

    start_job(
        user_id=user_id,
        session_id=session_id,
        idempotency_key=idempotency_key,
        profile=profile,
        request=request,
        parent_run_id=run_id,
        parent_tool_use_id=tool_use_id,
        model_id=model_id,
    )
    return json.dumps({
        "status": "accepted",
        "profile": profile,
        "message": (
            "The background delegation was accepted. Progress is tracked in "
            "the activity panel and the result will be delivered automatically."
        ),
    })


@tool(context=True)
def get_delegation(
    profile: str = "",
    goal_contains: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Return delegation status without exposing internal job identifiers.

    Args:
        profile: Optional specialist profile filter: analyst or reviewer.
        goal_contains: Optional text used to match the delegated goal.
    """
    session_id, user_id, *_ = _context(tool_context)
    matches = _matching_jobs(
        user_id,
        session_id,
        profile=profile,
        goal_contains=goal_contains,
    )
    return json.dumps({
        "status": "ok" if matches else "not_found",
        "delegations": [_agent_job_view(record) for record in matches],
    }, default=str)


@tool(context=True)
def cancel_delegation(
    profile: str = "",
    goal_contains: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Cancel one matching active delegation without using an internal ID.

    Args:
        profile: Optional specialist profile filter: analyst or reviewer.
        goal_contains: Optional text used to match the delegated goal.
    """
    session_id, user_id, *_ = _context(tool_context)
    matches = _matching_jobs(
        user_id,
        session_id,
        profile=profile,
        goal_contains=goal_contains,
        active_only=True,
    )
    if not matches:
        return json.dumps({"status": "not_found"})
    if len(matches) > 1:
        return json.dumps({
            "status": "ambiguous",
            "message": "More than one active delegation matches; narrow the goal.",
            "delegations": [_agent_job_view(record) for record in matches],
        }, default=str)
    record = matches[0]
    cancelled = cancel_job(
        user_id,
        session_id,
        str(record["jobId"]),
    )
    return json.dumps({
        "status": "cancelled",
        "delegation": _agent_job_view(cancelled),
    }, default=str)


register_skill(
    "delegate-work",
    tools=[delegate_task, get_delegation, cancel_delegation],
)
