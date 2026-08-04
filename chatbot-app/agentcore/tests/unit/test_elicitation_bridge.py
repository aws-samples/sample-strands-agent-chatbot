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


def test_dynamodb_store_keys_by_elicitation_id_alone():
    """The OAuth callback page only knows the elicitation ID (echoed back as
    customState); it has no chat-session context. The DynamoDB key must
    therefore be derivable from the elicitation ID by itself."""
    from agent.mcp.elicitation_bridge import DynamoDBElicitationStore

    key_a = DynamoDBElicitationStore._get_key(None, "session-a", "eid-1")
    key_b = DynamoDBElicitationStore._get_key(None, "session-b", "eid-1")

    assert key_a == key_b
    assert "eid-1" in key_a["userId"]["S"]
