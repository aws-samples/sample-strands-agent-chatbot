from agent.mcp.elicitation_bridge import LocalElicitationStore


def test_completion_is_correlated_to_exact_elicitation_id():
    store = LocalElicitationStore()
    store.register_pending("session-1", "elicitation-1", "user-1")

    store.signal_complete("session-1", "elicitation-2", "oauth-session")

    assert store.get_completion("session-1", "elicitation-1") == (False, None)
    assert store.get_completion("session-1", "elicitation-2") == (
        True,
        "oauth-session",
    )

    store.clear("session-1", "elicitation-1")
    store.clear("session-1", "elicitation-2")
