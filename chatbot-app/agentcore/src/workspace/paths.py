"""Deterministic physical paths for session workspace storage."""

from __future__ import annotations

import hashlib


def code_interpreter_workspace_id(user_id: str, session_id: str) -> str:
    """Return a bounded, opaque directory name for one user/session pair."""
    source = f"{user_id}\0{session_id}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:48]


def code_interpreter_prefix(user_id: str, session_id: str) -> str:
    """Return the S3 key prefix for a Code Interpreter session workspace."""
    workspace_id = code_interpreter_workspace_id(user_id, session_id)
    return f"code-interpreter-workspace/{workspace_id}/"
