"""Verify the resolved runtime dependencies provide the APIs the server uses.

conftest.py mocks the AWS SDKs so the unit tests can run offline, which means
nothing else here exercises what pip actually installs. That gap let mcp 2.0 —
which removed mcp.server.fastmcp — resolve into a deployed image and crash the
runtime on startup. These tests import the real packages, so they fail at
install time instead.
"""

import importlib


def _import(name: str):
    """Import for real, bypassing conftest's MagicMock entries."""
    import sys

    saved = {k: v for k, v in sys.modules.items() if k == name or k.startswith(name + ".")}
    for k in saved:
        del sys.modules[k]
    try:
        return importlib.import_module(name)
    finally:
        sys.modules.update(saved)


def test_fastmcp_is_importable():
    """mcp_server.py does `from mcp.server.fastmcp import FastMCP`."""
    mod = _import("mcp.server.fastmcp")
    assert hasattr(mod, "FastMCP")
    assert hasattr(mod, "Context")


def test_context_supports_url_elicitation():
    """The 3LO flow pauses tools with ctx.elicit_url()."""
    ctx = _import("mcp.server.fastmcp").Context
    assert hasattr(ctx, "elicit_url")


def test_accepted_url_elicitation_is_importable():
    """agentcore_oauth.get_token_with_elicitation checks this result type."""
    mod = _import("mcp.server.elicitation")
    assert hasattr(mod, "AcceptedUrlElicitation")
