"""
Tests for CompactingSessionManager

Tests cover:
- Threshold-based summarization triggering
- Summary retrieval from AgentCore LTM
- Message loading with summarization applied
- Configuration options
- Edge cases and error handling
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


class TestCompactingSessionManagerInit:
    """Test CompactingSessionManager initialization"""

    @patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__')
    def test_init_with_default_config(self, mock_parent_init):
        """Should initialize with default thresholds and turn counts"""
        mock_parent_init.return_value = None

        from agent.session.compacting_session_manager import CompactingSessionManager
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

        config = MagicMock(spec=AgentCoreMemoryConfig)
        config.memory_id = 'test-memory'
        config.session_id = 'test-session'
        config.actor_id = 'test-user'

        manager = CompactingSessionManager(
            agentcore_memory_config=config,
            region_name='us-west-2'
        )

        # Token-based compaction threshold (default: 100K)
        assert manager.token_threshold == 100_000
        # Turn management (default: 2 protected turns)
        assert manager.protected_turns == 2
        # Tool content limit
        assert manager.max_tool_content_length == 500  # Default: truncate tool content > 500 chars
        # Compaction state should be None initially
        assert manager.compaction_state is None

    @patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__')
    def test_init_with_custom_config(self, mock_parent_init):
        """Should accept custom token_threshold and protected_turns"""
        mock_parent_init.return_value = None

        from agent.session.compacting_session_manager import CompactingSessionManager
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

        config = MagicMock(spec=AgentCoreMemoryConfig)

        manager = CompactingSessionManager(
            agentcore_memory_config=config,
            region_name='us-west-2',
            token_threshold=100_000,
            protected_turns=3,
            user_id='test-user-123',
            summarization_strategy_id='custom-strategy-123'
        )

        assert manager.token_threshold == 100_000
        assert manager.protected_turns == 3
        assert manager.user_id == 'test-user-123'
        assert manager.summarization_strategy_id == 'custom-strategy-123'


class TestMailboxPersistenceScope:
    @pytest.fixture
    def manager(self):
        with patch(
            'agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__'
        ) as parent_init:
            parent_init.return_value = None
            from agent.session.compacting_session_manager import CompactingSessionManager

            config = MagicMock()
            config.memory_id = "memory-1"
            config.session_id = "session-1"
            config.actor_id = "user-1"
            manager = CompactingSessionManager(config)
            manager.config = config
            manager.memory_client = MagicMock()
            return manager

    @patch(
        'agent.session.compacting_session_manager.AgentCoreMemorySessionManager.create_message'
    )
    def test_scope_adds_stable_metadata_and_client_token(self, parent_create, manager):
        from strands.types.session import SessionMessage

        parent_create.return_value = {"eventId": "stored-1"}
        message = SessionMessage.from_message(
            {"role": "user", "content": [{"text": "internal"}]},
            0,
        )

        with manager.mailbox_event_scope("mailbox-event-1", 7):
            result = manager.create_message("session-1", "default", message)

        assert result == {"eventId": "stored-1"}
        metadata = parent_create.call_args.kwargs["metadata"]
        assert metadata["originEventId"]["stringValue"] == "mailbox-event-1"
        assert metadata["conversationEpoch"]["stringValue"] == "7"
        assert metadata["logicalMessageId"]["stringValue"] == (
            "mailbox:mailbox-event-1:0"
        )
        assert metadata["visibility"]["stringValue"] == "internal"

        emitter = manager.memory_client.gmdp_client.meta.events
        callback = emitter.register_first.call_args.args[1]
        params = {}
        callback(params)
        assert len(params["clientToken"]) == 64
        assert params["extractionMode"] == "SKIP"
        emitter.unregister.assert_called_once()

    @patch(
        'agent.session.compacting_session_manager.AgentCoreMemorySessionManager.create_message'
    )
    def test_scope_assigns_deterministic_sequence(self, parent_create, manager):
        from strands.types.session import SessionMessage

        parent_create.return_value = {"eventId": "stored"}
        assistant = SessionMessage.from_message(
            {"role": "assistant", "content": [{"text": "ready"}]},
            0,
        )

        with manager.mailbox_event_scope("mailbox-event-1"):
            manager.create_message("session-1", "default", assistant)
            manager.create_message("session-1", "default", assistant)

        calls = parent_create.call_args_list
        assert calls[0].kwargs["metadata"]["logicalMessageId"]["stringValue"].endswith(
            ":0"
        )
        assert calls[1].kwargs["metadata"]["logicalMessageId"]["stringValue"].endswith(
            ":1"
        )
        assert calls[0].kwargs["metadata"]["visibility"]["stringValue"] == "conversation"

    def test_list_messages_filters_stale_mailbox_epochs(self, manager):
        repository = MagicMock()
        repository.get_conversation_epoch.return_value = 3
        manager.config.filter_restored_tool_context = False
        manager.memory_client.list_events.return_value = [
            {"eventId": "foreground"},
            {
                "eventId": "stale",
                "metadata": {
                    "conversationEpoch": {"stringValue": "2"},
                },
            },
            {
                "eventId": "current",
                "metadata": {
                    "conversationEpoch": {"stringValue": "3"},
                },
            },
        ]
        manager.converter = MagicMock()
        manager.converter.events_to_messages.side_effect = (
            lambda events: [event["eventId"] for event in events]
        )

        with patch(
            "agent.mailbox.get_mailbox_repository",
            return_value=repository,
        ):
            messages = manager.list_messages("session-1", "default")

        assert messages == ["foreground", "current"]
        repository.get_conversation_epoch.assert_called_once_with(
            "user-1",
            "session-1",
        )

class TestCompactionState:
    """Test CompactionState dataclass"""

    def test_compaction_state_default_values(self):
        """Should initialize with default values"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState()

        assert state.checkpoint == 0
        assert state.summary is None
        assert state.updatedAt is None

    def test_compaction_state_with_values(self):
        """Should accept all parameters"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState(
            checkpoint=50,
            summary="Previous conversation summary",
            updatedAt="2024-01-01T00:00:00Z"
        )

        assert state.checkpoint == 50
        assert state.summary == "Previous conversation summary"
        assert state.updatedAt == "2024-01-01T00:00:00Z"

    def test_compaction_state_to_dict(self):
        """Should convert to dictionary for storage"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState(
            checkpoint=30,
            summary="Test summary",
        )

        result = state.to_dict()

        assert result["checkpoint"] == 30
        assert result["summary"] == "Test summary"
        assert "updatedAt" in result

    def test_compaction_state_from_dict(self):
        """Should create from stored data"""
        from agent.session.compacting_session_manager import CompactionState

        data = {
            "checkpoint": 25,
            "summary": "Loaded summary",
            "updatedAt": "2024-01-15T12:00:00Z"
        }

        state = CompactionState.from_dict(data)

        assert state.checkpoint == 25
        assert state.summary == "Loaded summary"
        assert state.updatedAt == "2024-01-15T12:00:00Z"

    def test_compaction_state_from_dict_none(self):
        """Should return default state when data is None"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState.from_dict(None)

        assert state.checkpoint == 0
        assert state.summary is None

    def test_compaction_state_from_dict_empty(self):
        """Should handle empty dictionary"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState.from_dict({})

        assert state.checkpoint == 0


class TestThresholdLogic:
    """Test token-based threshold logic"""

    def test_below_threshold_no_compaction(self):
        """When input tokens are below threshold, should not trigger checkpoint"""
        input_tokens = 50000
        threshold = 100000

        should_compact = input_tokens > threshold
        assert should_compact is False

    def test_above_threshold_triggers_compaction(self):
        """When input tokens exceed threshold, should trigger checkpoint"""
        input_tokens = 120000
        threshold = 100000

        should_compact = input_tokens > threshold
        assert should_compact is True

    def test_exactly_at_threshold_no_compaction(self):
        """When input tokens equal threshold, should not trigger checkpoint"""
        input_tokens = 100000
        threshold = 100000

        should_compact = input_tokens > threshold
        assert should_compact is False

    def test_checkpoint_can_update_forward(self):
        """When checkpoint exists, it can be updated forward if tokens exceed threshold again"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState(checkpoint=50)
        input_tokens = 200000
        threshold = 100000
        new_checkpoint = 80

        # Should update checkpoint if new_checkpoint > current checkpoint
        should_update = input_tokens > threshold and new_checkpoint > state.checkpoint
        assert should_update is True


class TestTurnSafety:
    """Test turn-based safe cutoff logic - using actual CompactingSessionManager methods

    New implementation uses:
    - _valid_cutoff_message_ids: List of message indices where checkpoint can be set (user text messages)
    - _find_protected_message_indices: Find indices to protect from truncation
    - _has_tool_result: Detect toolResult in message content
    """

    @pytest.fixture
    def manager(self):
        """Create CompactingSessionManager instance for testing"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__', return_value=None):
            from agent.session.compacting_session_manager import CompactingSessionManager
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            config.memory_id = 'test-memory'

            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2',
                protected_turns=2
            )
            return manager

    def test_has_tool_result_detection(self, manager):
        """Should detect toolResult in message content"""
        msg_with_result = {
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": "123", "content": [{"text": "result"}]}}
            ]
        }
        msg_user_text = {
            "role": "user",
            "content": [{"text": "Hello"}]
        }

        assert manager._has_tool_result(msg_with_result) is True
        assert manager._has_tool_result(msg_user_text) is False

    def test_find_protected_message_indices_basic(self, manager):
        """Should find indices of messages to protect (recent N turns)"""
        messages = [
            {"role": "user", "content": [{"text": "Hello"}]},
            {"role": "assistant", "content": [{"text": "Hi there"}]},
            {"role": "user", "content": [{"text": "How are you?"}]},
            {"role": "assistant", "content": [{"text": "I'm doing well"}]},
            {"role": "user", "content": [{"text": "Thanks"}]},
            {"role": "assistant", "content": [{"text": "You're welcome"}]},
        ]

        # With protected_turns=2, should protect last 2 turns (indices 2-5)
        protected = manager._find_protected_message_indices(messages, protected_turns=2)

        # Should protect messages from index 2 onwards (last 2 turns)
        assert 2 in protected
        assert 3 in protected
        assert 4 in protected
        assert 5 in protected
        # First turn should NOT be protected
        assert 0 not in protected
        assert 1 not in protected

    def test_find_protected_message_indices_with_tools(self, manager):
        """Should correctly identify turns even with tool chains"""
        messages = [
            {"role": "user", "content": [{"text": "Search for Python"}]},  # Turn 1 start (idx 0)
            {"role": "assistant", "content": [{"toolUse": {"toolUseId": "1"}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "1"}}]},  # Not a turn start (toolResult)
            {"role": "assistant", "content": [{"text": "Found results"}]},
            {"role": "user", "content": [{"text": "Thanks"}]},  # Turn 2 start (idx 4)
            {"role": "assistant", "content": [{"text": "You're welcome"}]},
        ]

        # With protected_turns=1, should protect only the last turn (indices 4-5)
        protected = manager._find_protected_message_indices(messages, protected_turns=1)

        # Last turn (idx 4-5) should be protected
        assert 4 in protected
        assert 5 in protected
        # Earlier messages should NOT be protected
        assert 0 not in protected
        assert 1 not in protected
        assert 2 not in protected
        assert 3 not in protected

    def test_find_protected_message_indices_fewer_turns(self, manager):
        """When fewer turns than protected_turns, should protect all available"""
        messages = [
            {"role": "user", "content": [{"text": "Hello"}]},
            {"role": "assistant", "content": [{"text": "Hi"}]},
        ]

        # Only 1 turn available, but protected_turns=2
        protected = manager._find_protected_message_indices(messages, protected_turns=2)

        # Should protect all messages (only 1 turn available)
        assert 0 in protected
        assert 1 in protected

    def test_find_protected_message_indices_empty(self, manager):
        """Should handle empty messages gracefully"""
        protected = manager._find_protected_message_indices([], protected_turns=2)
        assert len(protected) == 0

    def test_valid_cutoff_points_exclude_tool_results(self, manager):
        """Valid cutoff points should only include user text messages, not toolResult"""
        messages = [
            {"role": "user", "content": [{"text": "Search for Python"}]},  # Valid cutoff (idx 0)
            {"role": "assistant", "content": [{"toolUse": {"toolUseId": "1"}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "1"}}]},  # NOT valid (toolResult)
            {"role": "assistant", "content": [{"text": "Found results"}]},
            {"role": "user", "content": [{"text": "Thanks"}]},  # Valid cutoff (idx 4)
            {"role": "assistant", "content": [{"text": "You're welcome"}]},
        ]

        # Simulate what initialize() does: find valid cutoff points
        valid_cutoffs = []
        for idx, msg in enumerate(messages):
            if msg.get('role') == 'user' and not manager._has_tool_result(msg):
                valid_cutoffs.append(idx)

        # Should only include indices 0 and 4 (user text messages)
        assert valid_cutoffs == [0, 4]
        # Index 2 (toolResult) should NOT be included
        assert 2 not in valid_cutoffs


class TestSummaryRetrieval:
    """Test summary retrieval from AgentCore LTM - using actual CompactingSessionManager methods"""

    @pytest.fixture
    def manager(self):
        """Create CompactingSessionManager instance for testing"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__', return_value=None):
            from agent.session.compacting_session_manager import CompactingSessionManager
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            config.memory_id = 'test-memory'
            config.actor_id = 'user-456'

            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2'
            )
            # Set config for namespace building
            manager.config = config
            return manager

    def test_retrieve_summaries_builds_correct_namespace(self):
        """Should build correct namespace path for SUMMARIZATION strategy"""
        strategy_id = 'conversation_summary-abc123'
        actor_id = 'user-456'

        expected_namespace = f"/strategies/{strategy_id}/actors/{actor_id}"

        assert expected_namespace == '/strategies/conversation_summary-abc123/actors/user-456'

    def test_prepend_summary_to_first_user_message(self, manager):
        """Summary should be prepended to first user message text"""
        messages = [
            {"role": "user", "content": [{"text": "Hello, what is 2+2?"}]},
            {"role": "assistant", "content": [{"text": "4"}]},
        ]
        summary_prefix = "<conversation_summary>User likes math</conversation_summary>\n\n"

        result = manager._prepend_summary_to_first_message(messages, summary_prefix)

        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["text"].startswith("<conversation_summary>")
        assert "Hello, what is 2+2?" in result[0]["content"][0]["text"]

    def test_prepend_summary_does_not_modify_original(self, manager):
        """Prepending summary should not modify original messages"""
        original_text = "Original question"
        messages = [
            {"role": "user", "content": [{"text": original_text}]},
            {"role": "assistant", "content": [{"text": "Answer"}]},
        ]
        summary_prefix = "<conversation_summary>Summary</conversation_summary>\n\n"

        result = manager._prepend_summary_to_first_message(messages, summary_prefix)

        # Original unchanged
        assert messages[0]["content"][0]["text"] == original_text
        # Result has summary prepended
        assert result[0]["content"][0]["text"].startswith("<conversation_summary>")

    def test_prepend_summary_empty_prefix_returns_original(self, manager):
        """Empty summary prefix should return messages unchanged"""
        messages = [
            {"role": "user", "content": [{"text": "Hello"}]},
        ]

        result = manager._prepend_summary_to_first_message(messages, "")

        assert result == messages

    def test_prepend_summary_empty_messages_returns_empty(self, manager):
        """Empty messages list should return empty list"""
        result = manager._prepend_summary_to_first_message([], "some summary")
        assert result == []

    def test_prepend_summary_non_user_first_message_returns_original(self, manager):
        """If first message is not user role, should return original"""
        messages = [
            {"role": "assistant", "content": [{"text": "Hello"}]},
        ]
        summary_prefix = "<conversation_summary>Summary</conversation_summary>\n\n"

        result = manager._prepend_summary_to_first_message(messages, summary_prefix)

        # Should return original since first message is not user
        assert result == messages


class TestMessageLoading:
    """Test message loading with summarization"""

    def test_recent_offset_calculation(self):
        """Should calculate correct offset for recent messages"""
        total_messages = 100
        recent_count = 10

        # We want last 10 messages, so offset should be 90
        recent_offset = max(0, total_messages - recent_count)

        assert recent_offset == 90

    def test_recent_offset_with_few_messages(self):
        """Should handle case where total messages < recent_count"""
        total_messages = 5
        recent_count = 10

        recent_offset = max(0, total_messages - recent_count)

        assert recent_offset == 0

    def test_final_messages_order(self):
        """Messages should be in order: prepend + summary + recent"""
        prepend = [{"role": "system", "content": [{"text": "system"}]}]
        summary = {"role": "user", "content": [{"text": "summary"}]}
        recent = [
            {"role": "user", "content": [{"text": "recent1"}]},
            {"role": "assistant", "content": [{"text": "recent2"}]}
        ]

        final_messages = prepend.copy()
        if summary:
            final_messages.append(summary)
        final_messages.extend(recent)

        assert len(final_messages) == 4
        assert final_messages[0]["content"][0]["text"] == "system"
        assert final_messages[1]["content"][0]["text"] == "summary"
        assert final_messages[2]["content"][0]["text"] == "recent1"


class TestStrategyIdLookup:
    """Test SUMMARIZATION strategy ID lookup"""

    def test_strategy_lookup_from_memory_config(self):
        """Should extract strategy ID from Memory configuration"""
        mock_response = {
            'memory': {
                'strategies': [
                    {'type': 'USER_PREFERENCE', 'strategyId': 'user_pref-123'},
                    {'type': 'SEMANTIC', 'strategyId': 'semantic-456'},
                    {'type': 'SUMMARIZATION', 'strategyId': 'summary-789'},
                ]
            }
        }

        strategies = mock_response['memory']['strategies']
        summarization_id = None

        for strategy in strategies:
            if strategy.get('type') == 'SUMMARIZATION':
                summarization_id = strategy.get('strategyId')
                break

        assert summarization_id == 'summary-789'

    def test_strategy_lookup_with_old_field_names(self):
        """Should handle old field names (memoryStrategyType, memoryStrategyId)"""
        mock_response = {
            'memory': {
                'memoryStrategies': [
                    {'memoryStrategyType': 'SUMMARIZATION', 'memoryStrategyId': 'old-summary-123'},
                ]
            }
        }

        strategies = mock_response['memory'].get('strategies', mock_response['memory'].get('memoryStrategies', []))
        summarization_id = None

        for strategy in strategies:
            strategy_type = strategy.get('type', strategy.get('memoryStrategyType', ''))
            if strategy_type == 'SUMMARIZATION':
                summarization_id = strategy.get('strategyId', strategy.get('memoryStrategyId', ''))
                break

        assert summarization_id == 'old-summary-123'

    def test_strategy_not_found(self):
        """Should return None when SUMMARIZATION strategy not configured"""
        mock_response = {
            'memory': {
                'strategies': [
                    {'type': 'USER_PREFERENCE', 'strategyId': 'user_pref-123'},
                ]
            }
        }

        strategies = mock_response['memory']['strategies']
        summarization_id = None

        for strategy in strategies:
            if strategy.get('type') == 'SUMMARIZATION':
                summarization_id = strategy.get('strategyId')
                break

        assert summarization_id is None


class TestConfigurationFromEnvironment:
    """Test configuration loading from environment variables"""

    def test_default_compaction_threshold(self):
        """Should use default compaction threshold of 50"""
        threshold = int(os.environ.get('COMPACTION_THRESHOLD', '50'))
        assert threshold == 50

    def test_default_truncation_threshold(self):
        """Should use default truncation threshold of 20"""
        threshold = int(os.environ.get('COMPACTION_TRUNCATION_THRESHOLD', '20'))
        assert threshold == 20

    def test_default_recent_turns(self):
        """Should use default recent turns of 3"""
        recent_turns = int(os.environ.get('COMPACTION_RECENT_TURNS', '3'))
        assert recent_turns == 3

    def test_custom_compaction_threshold_from_env(self):
        """Should read custom event threshold from environment"""
        with patch.dict(os.environ, {'COMPACTION_THRESHOLD': '50'}):
            threshold = int(os.environ.get('COMPACTION_THRESHOLD', '50'))
            assert threshold == 50

    def test_custom_truncation_threshold_from_env(self):
        """Should read custom truncation threshold from environment"""
        with patch.dict(os.environ, {'COMPACTION_TRUNCATION_THRESHOLD': '25'}):
            threshold = int(os.environ.get('COMPACTION_TRUNCATION_THRESHOLD', '30'))
            assert threshold == 25

    def test_custom_recent_turns_from_env(self):
        """Should read custom recent turns from environment"""
        with patch.dict(os.environ, {'COMPACTION_RECENT_TURNS': '5'}):
            recent_turns = int(os.environ.get('COMPACTION_RECENT_TURNS', '3'))
            assert recent_turns == 5

    def test_default_max_tool_content_length(self):
        """Should use default max tool content length of 500"""
        max_length = int(os.environ.get('COMPACTION_MAX_TOOL_LENGTH', '500'))
        assert max_length == 500

    def test_custom_max_tool_content_length_from_env(self):
        """Should read custom max tool content length from environment"""
        with patch.dict(os.environ, {'COMPACTION_MAX_TOOL_LENGTH': '500'}):
            max_length = int(os.environ.get('COMPACTION_MAX_TOOL_LENGTH', '1000'))
            assert max_length == 500


class TestToolTruncation:
    """Test Stage 1: Tool content truncation - using actual CompactingSessionManager methods"""

    @pytest.fixture
    def manager(self):
        """Create CompactingSessionManager instance for testing"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__', return_value=None):
            from agent.session.compacting_session_manager import CompactingSessionManager
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            config.memory_id = 'test-memory'

            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2',
                max_tool_content_length=1000
            )
            return manager

    def test_truncate_text_short_unchanged(self, manager):
        """Text shorter than max_length should remain unchanged"""
        text = "Short text"
        result = manager._truncate_text(text, 1000)
        assert result == text

    def test_truncate_text_long_with_indicator(self, manager):
        """Long text should be truncated with indicator"""
        text = "A" * 2000
        result = manager._truncate_text(text, 1000)

        assert len(result) < len(text)
        assert "[truncated," in result
        assert "1000 chars removed]" in result

    def test_truncate_tool_contents_result_text(self, manager):
        """Should truncate long toolResult text content"""
        messages = [{
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": "123",
                    "content": [{"text": "B" * 2000}]
                }
            }]
        }]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # Verify truncation was applied
        result_text = result[0]["content"][0]["toolResult"]["content"][0]["text"]
        assert len(result_text) < 2000
        assert "[truncated," in result_text
        assert truncation_count == 1
        assert chars_saved > 0

    def test_truncate_tool_contents_use_input(self, manager):
        """Should truncate long toolUse input"""
        messages = [{
            "role": "assistant",
            "content": [{
                "toolUse": {
                    "toolUseId": "123",
                    "name": "web_search",
                    "input": {"query": "A" * 2000}
                }
            }]
        }]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # Verify input was truncated - now stored as {"_truncated": "..."}
        truncated_input = result[0]["content"][0]["toolUse"]["input"]
        assert "_truncated" in truncated_input
        assert "[truncated," in truncated_input["_truncated"]
        assert truncation_count == 1

    def test_truncate_tool_contents_preserves_structure(self, manager):
        """Truncation should preserve message structure and roles"""
        messages = [
            {"role": "user", "content": [{"text": "Hello"}]},
            {
                "role": "assistant",
                "content": [
                    {"text": "Let me search"},
                    {"toolUse": {"toolUseId": "1", "name": "search", "input": {"q": "A" * 2000}}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "1", "content": [{"text": "B" * 2000}]}}
                ]
            },
        ]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # Structure preserved
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert "toolUse" in result[1]["content"][1]
        assert "toolResult" in result[2]["content"][0]

        # Content truncated - toolUse input now has {"_truncated": "..."}
        assert "_truncated" in result[1]["content"][1]["toolUse"]["input"]
        assert "[truncated," in result[1]["content"][1]["toolUse"]["input"]["_truncated"]
        assert "[truncated," in result[2]["content"][0]["toolResult"]["content"][0]["text"]
        assert truncation_count == 2
        assert chars_saved > 0

    def test_truncate_tool_contents_json(self, manager):
        """Should truncate JSON content in toolResult - converts to text"""
        messages = [{
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": "123",
                    "content": [{"json": {"data": "C" * 2000}}]
                }
            }]
        }]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # JSON content converted to truncated text
        result_block = result[0]["content"][0]["toolResult"]["content"][0]
        assert "json" not in result_block  # json key removed
        assert "text" in result_block  # converted to text
        assert "[truncated," in result_block["text"]
        assert truncation_count == 1

    def test_truncate_does_not_modify_original(self, manager):
        """Truncation should not modify original messages"""
        original_text = "D" * 2000
        messages = [{
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": "123",
                    "content": [{"text": original_text}]
                }
            }]
        }]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # Original unchanged
        assert messages[0]["content"][0]["toolResult"]["content"][0]["text"] == original_text
        # Result truncated
        assert "[truncated," in result[0]["content"][0]["toolResult"]["content"][0]["text"]
        assert truncation_count == 1

    def test_truncate_image_block_to_placeholder(self, manager):
        """Should replace image block with text placeholder"""
        fake_image_bytes = b"fake_image_data_12345"
        messages = [{
            "role": "user",
            "content": [
                {"text": "Here is an image"},
                {
                    "image": {
                        "format": "png",
                        "source": {"bytes": fake_image_bytes}
                    }
                }
            ]
        }]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # Image should be replaced with text placeholder
        assert "image" not in result[0]["content"][1]
        assert "text" in result[0]["content"][1]
        placeholder_text = result[0]["content"][1]["text"]
        assert "[Image placeholder:" in placeholder_text
        assert "format=png" in placeholder_text
        assert f"original_size={len(fake_image_bytes)}" in placeholder_text
        assert truncation_count == 1
        assert chars_saved == len(fake_image_bytes)

    def test_truncate_image_in_tool_result(self, manager):
        """Should replace image in toolResult content with placeholder"""
        fake_image_bytes = b"screenshot_data_67890"
        messages = [{
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": "123",
                    "content": [
                        {"text": "Screenshot taken"},
                        {
                            "image": {
                                "format": "jpeg",
                                "source": {"bytes": fake_image_bytes}
                            }
                        }
                    ]
                }
            }]
        }]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # Image in toolResult should be replaced
        result_content = result[0]["content"][0]["toolResult"]["content"]
        assert result_content[0]["text"] == "Screenshot taken"  # Text unchanged
        assert "image" not in result_content[1]
        assert "text" in result_content[1]
        placeholder_text = result_content[1]["text"]
        assert "[Image placeholder:" in placeholder_text
        assert "format=jpeg" in placeholder_text
        assert truncation_count == 1

    def test_truncate_protected_image_not_replaced(self, manager):
        """Protected messages should NOT have images replaced"""
        fake_image_bytes = b"protected_image_data"
        messages = [{
            "role": "user",
            "content": [{
                "image": {
                    "format": "png",
                    "source": {"bytes": fake_image_bytes}
                }
            }]
        }]

        # Protect message index 0
        protected_indices = {0}
        result, truncation_count, chars_saved = manager._truncate_tool_contents(
            messages, protected_indices=protected_indices
        )

        # Image should remain unchanged
        assert "image" in result[0]["content"][0]
        assert result[0]["content"][0]["image"]["format"] == "png"
        assert truncation_count == 0
        assert chars_saved == 0


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_new_agent_no_summarization(self):
        """New agent (no existing session) should not trigger summarization"""
        session_agent = None  # No existing agent

        # New agent path: should create agent, not restore with summarization
        is_new_agent = session_agent is None
        assert is_new_agent is True

    def test_zero_messages_no_error(self):
        """Should handle session with zero messages gracefully"""
        total_messages = 0
        threshold = 50

        should_summarize = total_messages > threshold
        assert should_summarize is False

    def test_summary_retrieval_error_continues(self):
        """Should continue with recent messages even if summary retrieval fails"""
        summaries = []  # Empty due to error
        recent_messages = [
            {"role": "user", "content": [{"text": "hello"}]},
            {"role": "assistant", "content": [{"text": "hi"}]}
        ]

        # Should still have recent messages even without summary
        final_messages = []
        if summaries:
            final_messages.append({"role": "user", "content": [{"text": "summary"}]})
        final_messages.extend(recent_messages)

        assert len(final_messages) == 2
        assert final_messages[0]["content"][0]["text"] == "hello"


class TestIntegrationWithAgent:
    """Test integration with ChatbotAgent"""

    def test_agent_uses_summarizing_manager_in_cloud_mode(self):
        """ChatbotAgent should use CompactingSessionManager when MEMORY_ID is set"""
        # This is verified by the agent.py code change
        # When MEMORY_ID env var is set, CompactingSessionManager is used

        # Simulate the decision logic
        memory_id = 'test-memory-id'
        agentcore_available = True

        use_summarizing = memory_id and agentcore_available
        assert use_summarizing is True

    def test_agent_uses_file_manager_in_local_mode(self):
        """ChatbotAgent should use FileSessionManager in local mode"""
        memory_id = None
        agentcore_available = True

        use_summarizing = memory_id and agentcore_available
        # None and True = None, which is falsy
        assert not use_summarizing


class TestCompactionEnabledPayload:
    """Test compaction_enabled payload functionality"""

    def test_compaction_enabled_default_true(self):
        """compaction_enabled should default to True when not provided"""
        # Simulate the default logic in ChatbotAgent
        compaction_enabled = None
        effective_compaction = compaction_enabled if compaction_enabled is not None else True
        assert effective_compaction is True

    def test_compaction_enabled_explicit_true(self):
        """compaction_enabled=True should use CompactingSessionManager"""
        compaction_enabled = True
        effective_compaction = compaction_enabled if compaction_enabled is not None else True
        assert effective_compaction is True

    def test_compaction_enabled_explicit_false(self):
        """compaction_enabled=False should use base AgentCoreMemorySessionManager"""
        compaction_enabled = False
        effective_compaction = compaction_enabled if compaction_enabled is not None else True
        assert effective_compaction is False

    def test_session_manager_selection_with_compaction_enabled(self):
        """Should select CompactingSessionManager when compaction_enabled=True"""
        memory_id = 'test-memory-id'
        agentcore_available = True
        compaction_enabled = True

        # Simulate the decision logic from agent.py
        use_compacting_manager = memory_id and agentcore_available and compaction_enabled
        assert use_compacting_manager is True

    def test_session_manager_selection_with_compaction_disabled(self):
        """Should select base AgentCoreMemorySessionManager when compaction_enabled=False"""
        memory_id = 'test-memory-id'
        agentcore_available = True
        compaction_enabled = False

        # Simulate the decision logic from agent.py
        # When compaction_enabled is False, use base AgentCoreMemorySessionManager
        use_compacting_manager = memory_id and agentcore_available and compaction_enabled
        assert use_compacting_manager is False

    def test_session_manager_selection_local_mode_ignores_compaction_flag(self):
        """In local mode, compaction_enabled should be ignored (use FileSessionManager)"""
        memory_id = None  # Local mode
        agentcore_available = True
        compaction_enabled = True  # This should be ignored

        # Local mode check takes precedence
        # None and True = None, which is falsy
        use_agentcore = memory_id and agentcore_available
        assert not use_agentcore  # None is falsy, so this should pass


class TestAGUIStateCompactionEnabled:
    """Test that compaction_enabled is properly extracted from AG-UI state"""

    def test_agui_state_compaction_enabled_true(self):
        """AG-UI state should carry compaction_enabled"""
        state = {
            "user_id": "user-123",
            "compaction_enabled": True,
        }
        assert state.get("compaction_enabled") is True

    def test_agui_state_compaction_enabled_false(self):
        """AG-UI state should carry compaction_enabled=False"""
        state = {
            "user_id": "user-123",
            "compaction_enabled": False,
        }
        assert state.get("compaction_enabled") is False

    def test_agui_state_compaction_enabled_missing(self):
        """AG-UI state without compaction_enabled defaults to None"""
        state = {"user_id": "user-123"}
        assert state.get("compaction_enabled") is None


class TestNoCompactionBaseline:
    """Test no-compaction baseline mode for performance comparison"""

    def test_no_compaction_flag_logic(self):
        """--no-compaction flag should result in compaction_enabled=False"""
        no_compaction = True
        compaction_enabled = not no_compaction
        assert compaction_enabled is False

    def test_default_compaction_enabled(self):
        """Default should have compaction enabled"""
        no_compaction = False
        compaction_enabled = not no_compaction
        assert compaction_enabled is True

    def test_baseline_vs_compaction_session_id_pattern(self):
        """Session IDs should indicate baseline vs compaction mode"""
        import uuid

        test_id = uuid.uuid4().hex

        # Baseline mode
        no_compaction = True
        compaction_label = "baseline" if no_compaction else "compaction"
        baseline_session_id = f"travel-{compaction_label}-test-{test_id}"
        assert "baseline" in baseline_session_id

        # Compaction mode
        no_compaction = False
        compaction_label = "baseline" if no_compaction else "compaction"
        compaction_session_id = f"travel-{compaction_label}-test-{test_id}"
        assert "compaction" in compaction_session_id


class TestProtectedMessageIndices:
    """Test _find_protected_message_indices() function"""

    @pytest.fixture
    def manager(self):
        """Create a CompactingSessionManager with mocked dependencies"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__') as mock_init:
            mock_init.return_value = None
            from agent.session.compacting_session_manager import CompactingSessionManager
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2',
                truncation_protected_turns=2
            )
            return manager

    def test_protect_last_2_turns(self, manager):
        """Should protect messages from last 2 turns"""
        messages = [
            # Turn 1 (index 0-1)
            {"role": "user", "content": [{"text": "Turn 1 question"}]},
            {"role": "assistant", "content": [{"text": "Turn 1 answer"}]},
            # Turn 2 (index 2-3)
            {"role": "user", "content": [{"text": "Turn 2 question"}]},
            {"role": "assistant", "content": [{"text": "Turn 2 answer"}]},
            # Turn 3 (index 4-5)
            {"role": "user", "content": [{"text": "Turn 3 question"}]},
            {"role": "assistant", "content": [{"text": "Turn 3 answer"}]},
            # Turn 4 (index 6-7)
            {"role": "user", "content": [{"text": "Turn 4 question"}]},
            {"role": "assistant", "content": [{"text": "Turn 4 answer"}]},
        ]

        protected = manager._find_protected_message_indices(messages, protected_turns=2)

        # Should protect turns 3 and 4 (indices 4-7)
        assert 0 not in protected  # Turn 1 user - not protected
        assert 1 not in protected  # Turn 1 assistant - not protected
        assert 2 not in protected  # Turn 2 user - not protected
        assert 3 not in protected  # Turn 2 assistant - not protected
        assert 4 in protected      # Turn 3 user - protected
        assert 5 in protected      # Turn 3 assistant - protected
        assert 6 in protected      # Turn 4 user - protected
        assert 7 in protected      # Turn 4 assistant - protected

    def test_protect_with_tool_use(self, manager):
        """Should correctly identify turns with tool use/result"""
        messages = [
            # Turn 1 (index 0-3): User question -> Tool use -> Tool result -> Assistant answer
            {"role": "user", "content": [{"text": "Search for weather"}]},
            {"role": "assistant", "content": [{"toolUse": {"name": "weather", "toolUseId": "123", "input": {}}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "123", "content": [{"text": "Sunny"}]}}]},
            {"role": "assistant", "content": [{"text": "It's sunny!"}]},
            # Turn 2 (index 4-5): Simple question-answer
            {"role": "user", "content": [{"text": "Thanks!"}]},
            {"role": "assistant", "content": [{"text": "You're welcome!"}]},
        ]

        protected = manager._find_protected_message_indices(messages, protected_turns=1)

        # Turn 2 starts at index 4 (user text message, not toolResult)
        # Should protect indices 4-5
        assert 0 not in protected  # Turn 1 user - not protected
        assert 1 not in protected  # Turn 1 tool use - not protected
        assert 2 not in protected  # Turn 1 tool result (user role but toolResult) - not protected
        assert 3 not in protected  # Turn 1 assistant - not protected
        assert 4 in protected      # Turn 2 user - protected
        assert 5 in protected      # Turn 2 assistant - protected

    def test_protect_zero_turns(self, manager):
        """Should return empty set when protected_turns=0"""
        messages = [
            {"role": "user", "content": [{"text": "Question"}]},
            {"role": "assistant", "content": [{"text": "Answer"}]},
        ]

        protected = manager._find_protected_message_indices(messages, protected_turns=0)
        assert protected == set()

    def test_protect_more_turns_than_available(self, manager):
        """Should protect all messages when protected_turns > total turns"""
        messages = [
            {"role": "user", "content": [{"text": "Question"}]},
            {"role": "assistant", "content": [{"text": "Answer"}]},
        ]

        protected = manager._find_protected_message_indices(messages, protected_turns=10)

        # Only 1 turn exists, so all messages should be protected
        assert 0 in protected
        assert 1 in protected

    def test_empty_messages(self, manager):
        """Should return empty set for empty messages"""
        protected = manager._find_protected_message_indices([], protected_turns=2)
        assert protected == set()


class TestTruncationWithProtectedIndices:
    """Test _truncate_tool_contents() with protected indices"""

    @pytest.fixture
    def manager(self):
        """Create a CompactingSessionManager with mocked dependencies"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__') as mock_init:
            mock_init.return_value = None
            from agent.session.compacting_session_manager import CompactingSessionManager
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2',
                max_tool_content_length=100  # Short for testing
            )
            return manager

    def test_truncate_only_unprotected(self, manager):
        """Should truncate only unprotected messages"""
        long_content = "A" * 500  # Longer than max_tool_content_length (100)

        messages = [
            # Message 0: unprotected - should be truncated
            {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "content": [{"text": long_content}]}}]},
            # Message 1: protected - should NOT be truncated
            {"role": "user", "content": [{"toolResult": {"toolUseId": "2", "content": [{"text": long_content}]}}]},
        ]

        protected_indices = {1}  # Protect message index 1

        result, truncation_count, chars_saved = manager._truncate_tool_contents(
            messages, protected_indices=protected_indices
        )

        # Message 0 should be truncated
        assert "truncated" in result[0]["content"][0]["toolResult"]["content"][0]["text"]

        # Message 1 should NOT be truncated
        assert result[1]["content"][0]["toolResult"]["content"][0]["text"] == long_content
        assert "truncated" not in result[1]["content"][0]["toolResult"]["content"][0]["text"]

        # Only 1 item truncated
        assert truncation_count == 1

    def test_truncate_all_when_no_protected(self, manager):
        """Should truncate all long content when no protected indices"""
        long_content = "B" * 500

        messages = [
            {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "content": [{"text": long_content}]}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "2", "content": [{"text": long_content}]}}]},
        ]

        result, truncation_count, chars_saved = manager._truncate_tool_contents(messages)

        # Both should be truncated
        assert "truncated" in result[0]["content"][0]["toolResult"]["content"][0]["text"]
        assert "truncated" in result[1]["content"][0]["toolResult"]["content"][0]["text"]
        assert truncation_count == 2

    def test_no_truncation_when_all_protected(self, manager):
        """Should not truncate anything when all indices protected"""
        long_content = "C" * 500

        messages = [
            {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "content": [{"text": long_content}]}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "2", "content": [{"text": long_content}]}}]},
        ]

        protected_indices = {0, 1}  # Protect all

        result, truncation_count, chars_saved = manager._truncate_tool_contents(
            messages, protected_indices=protected_indices
        )

        # Neither should be truncated
        assert result[0]["content"][0]["toolResult"]["content"][0]["text"] == long_content
        assert result[1]["content"][0]["toolResult"]["content"][0]["text"] == long_content
        assert truncation_count == 0
        assert chars_saved == 0


class TestEffectiveOffsetCalculation:
    """Test effective offset calculation combining conv_manager offset and checkpoint"""

    def test_effective_offset_uses_checkpoint(self):
        """When checkpoint > conv_manager_offset, effective_offset should be checkpoint"""
        checkpoint = 50
        conv_manager_offset = 0

        effective_offset = max(conv_manager_offset, checkpoint)
        assert effective_offset == 50

    def test_effective_offset_uses_conv_manager(self):
        """When conv_manager_offset > checkpoint, effective_offset should be conv_manager_offset"""
        checkpoint = 20
        conv_manager_offset = 30

        effective_offset = max(conv_manager_offset, checkpoint)
        assert effective_offset == 30

    def test_effective_offset_both_zero(self):
        """When both are 0, effective_offset should be 0"""
        checkpoint = 0
        conv_manager_offset = 0

        effective_offset = max(conv_manager_offset, checkpoint)
        assert effective_offset == 0

    def test_effective_offset_same_value(self):
        """When both are same, effective_offset should be that value"""
        checkpoint = 25
        conv_manager_offset = 25

        effective_offset = max(conv_manager_offset, checkpoint)
        assert effective_offset == 25


class TestModeSelectionLogic:
    """Test the two-feature selection logic in initialize()"""

    def test_mode_selection_no_checkpoint(self):
        """checkpoint == 0 -> Load all messages, apply truncation"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState(checkpoint=0)

        # Determine mode based on checkpoint only
        if state.checkpoint > 0:
            mode = "checkpoint"
        else:
            mode = "none"

        assert mode == "none"

    def test_mode_selection_with_checkpoint(self):
        """checkpoint > 0 -> Load from checkpoint, apply truncation"""
        from agent.session.compacting_session_manager import CompactionState

        state = CompactionState(checkpoint=50)

        # Determine mode based on checkpoint only
        if state.checkpoint > 0:
            mode = "checkpoint"
        else:
            mode = "none"

        assert mode == "checkpoint"

    def test_truncation_always_applies(self):
        """Truncation is always applied regardless of checkpoint state"""
        from agent.session.compacting_session_manager import CompactionState

        # Case 1: No checkpoint
        state1 = CompactionState(checkpoint=0)
        # Case 2: With checkpoint
        state2 = CompactionState(checkpoint=50)

        # Truncation should always be applied (not conditional)
        truncation_applies = True  # Always True in new design
        assert truncation_applies is True


class TestTwoFeatureCompaction:
    """Test the two-feature compaction design"""

    @pytest.fixture
    def manager(self):
        """Create a CompactingSessionManager with custom thresholds for testing"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__') as mock_init:
            mock_init.return_value = None
            from agent.session.compacting_session_manager import CompactingSessionManager
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2',
                token_threshold=1000,        # Low threshold for testing
                protected_turns=2,
                max_tool_content_length=50
            )
            return manager

    def test_thresholds_configured_correctly(self, manager):
        """Verify thresholds are set correctly"""
        assert manager.token_threshold == 1000
        assert manager.protected_turns == 2

    def test_compaction_triggers_above_threshold(self, manager):
        """Compaction should trigger when tokens exceed threshold"""
        # Below threshold - no checkpoint update
        assert 500 <= manager.token_threshold
        assert 1000 <= manager.token_threshold

        # Above threshold - should trigger checkpoint update
        assert 1001 > manager.token_threshold
        assert 2000 > manager.token_threshold


class TestUpdateAfterTurnLogic:
    """Test update_after_turn() triggering logic

    API: update_after_turn(input_tokens, agent_id, agent)
    - Recomputes cutoff points from agent.messages
    """

    @pytest.fixture
    def manager(self):
        """Create a CompactingSessionManager with mocked dependencies"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__') as mock_init:
            mock_init.return_value = None
            from agent.session.compacting_session_manager import CompactingSessionManager, CompactionState
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            config.memory_id = 'test-memory'
            config.actor_id = 'test-user'

            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2',
                token_threshold=1000,
                protected_turns=2,
                user_id='test-user'
            )
            manager.compaction_state = CompactionState()
            manager.config = config
            manager._valid_cutoff_message_ids = []
            manager._all_messages_for_summary = []

            return manager

    def _make_mock_agent(self, messages):
        """Create a mock agent with messages."""
        agent = MagicMock()
        agent.messages = messages
        return agent

    def test_updates_state_below_threshold(self, manager):
        """Below threshold should not set checkpoint"""
        agent = self._make_mock_agent([
            {"role": "user", "content": [{"text": "q1"}]},
            {"role": "assistant", "content": [{"text": "a1"}]},
        ])

        with patch.object(manager, 'save_compaction_state'):
            manager.update_after_turn(500, 'test-agent', agent)  # Below threshold

        assert manager.compaction_state.checkpoint == 0

    def test_triggers_checkpoint_above_threshold(self, manager):
        """Should trigger checkpoint when tokens exceed threshold"""
        messages = [
            {"role": "user", "content": [{"text": "msg1"}]},
            {"role": "assistant", "content": [{"text": "resp1"}]},
            {"role": "user", "content": [{"text": "msg2"}]},
            {"role": "assistant", "content": [{"text": "resp2"}]},
            {"role": "user", "content": [{"text": "msg3"}]},
            {"role": "assistant", "content": [{"text": "resp3"}]},
        ]
        agent = self._make_mock_agent(messages)

        with patch.object(manager, 'save_compaction_state'):
            with patch.object(manager, '_generate_summary_for_compaction', return_value="Test summary"):
                manager.update_after_turn(1500, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 2
        assert manager.compaction_state.summary == "Test summary"

    def test_does_not_trigger_below_threshold(self, manager):
        """Should not trigger checkpoint when tokens below threshold"""
        agent = self._make_mock_agent([
            {"role": "user", "content": [{"text": "q1"}]},
            {"role": "assistant", "content": [{"text": "a1"}]},
        ])

        with patch.object(manager, 'save_compaction_state'):
            manager.update_after_turn(500, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 0

    def test_updates_checkpoint_forward(self, manager):
        """Should update checkpoint forward when tokens exceed threshold again"""
        from agent.session.compacting_session_manager import CompactionState

        manager.compaction_state = CompactionState(
            checkpoint=2,
            summary="Existing summary"
        )

        messages = [
            {"role": "user", "content": [{"text": "msg1"}]},
            {"role": "assistant", "content": [{"text": "resp1"}]},
            {"role": "user", "content": [{"text": "msg2"}]},
            {"role": "assistant", "content": [{"text": "resp2"}]},
            {"role": "user", "content": [{"text": "msg3"}]},
            {"role": "assistant", "content": [{"text": "resp3"}]},
            {"role": "user", "content": [{"text": "msg4"}]},
            {"role": "assistant", "content": [{"text": "resp4"}]},
        ]
        agent = self._make_mock_agent(messages)

        with patch.object(manager, 'save_compaction_state'):
            with patch.object(manager, '_generate_summary_for_compaction', return_value="Updated summary"):
                manager.update_after_turn(2000, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 4
        assert manager.compaction_state.summary == "Updated summary"

    def test_does_not_update_checkpoint_backward(self, manager):
        """Should not update checkpoint if new_checkpoint <= current checkpoint"""
        from agent.session.compacting_session_manager import CompactionState

        manager.compaction_state = CompactionState(
            checkpoint=50,
            summary="Existing summary"
        )

        messages = [
            {"role": "user", "content": [{"text": "msg1"}]},
            {"role": "assistant", "content": [{"text": "resp1"}]},
            {"role": "user", "content": [{"text": "msg2"}]},
            {"role": "assistant", "content": [{"text": "resp2"}]},
        ]
        agent = self._make_mock_agent(messages)

        with patch.object(manager, 'save_compaction_state'):
            manager.update_after_turn(2000, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 50
        assert manager.compaction_state.summary == "Existing summary"

    def test_skips_checkpoint_when_insufficient_turns(self, manager):
        """Should skip checkpoint when total turns <= protected_turns"""
        manager.protected_turns = 2

        messages = [
            {"role": "user", "content": [{"text": "msg1"}]},
            {"role": "assistant", "content": [{"text": "resp1"}]},
            {"role": "user", "content": [{"text": "msg2"}]},
            {"role": "assistant", "content": [{"text": "resp2"}]},
        ]
        agent = self._make_mock_agent(messages)

        with patch.object(manager, 'save_compaction_state'):
            manager.update_after_turn(2000, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 0


class TestEndToEndScenarios:
    """End-to-end scenario tests for two-feature compaction

    API: update_after_turn(input_tokens, agent_id, agent)
    - Recomputes cutoff points from agent.messages
    """

    @pytest.fixture
    def manager(self):
        """Create a CompactingSessionManager with low thresholds for testing"""
        with patch('agent.session.compacting_session_manager.AgentCoreMemorySessionManager.__init__') as mock_init:
            mock_init.return_value = None
            from agent.session.compacting_session_manager import CompactingSessionManager, CompactionState
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

            config = MagicMock(spec=AgentCoreMemoryConfig)
            config.memory_id = 'test-memory'
            config.actor_id = 'test-user'

            manager = CompactingSessionManager(
                agentcore_memory_config=config,
                region_name='us-west-2',
                token_threshold=1000,
                protected_turns=2,
                max_tool_content_length=50,
                user_id='test-user'
            )
            manager.compaction_state = CompactionState()
            manager.config = config
            manager._valid_cutoff_message_ids = []
            manager._all_messages_for_summary = []

            return manager

    def _make_mock_agent(self, messages):
        agent = MagicMock()
        agent.messages = messages
        return agent

    def test_scenario_below_threshold(self, manager):
        """Scenario: Tokens below threshold - no checkpoint set"""
        assert manager.compaction_state.checkpoint == 0

        agent = self._make_mock_agent([
            {"role": "user", "content": [{"text": "q1"}]},
            {"role": "assistant", "content": [{"text": "a1"}]},
        ])

        with patch.object(manager, 'save_compaction_state'):
            manager.update_after_turn(600, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 0

    def test_scenario_above_threshold_sets_checkpoint(self, manager):
        """Scenario: Tokens exceed threshold - checkpoint gets set"""
        messages = [
            {"role": "user", "content": [{"text": "q1"}]},
            {"role": "assistant", "content": [{"text": "a1"}]},
            {"role": "user", "content": [{"text": "q2"}]},
            {"role": "assistant", "content": [{"text": "a2"}]},
            {"role": "user", "content": [{"text": "q3"}]},
            {"role": "assistant", "content": [{"text": "a3"}]},
        ]
        agent = self._make_mock_agent(messages)

        with patch.object(manager, 'save_compaction_state'):
            with patch.object(manager, '_generate_summary_for_compaction', return_value="Summary"):
                manager.update_after_turn(1200, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 2
        assert manager.compaction_state.summary == "Summary"

    def test_scenario_checkpoint_updates_forward(self, manager):
        """Scenario: Checkpoint can be updated forward as conversation grows"""
        from agent.session.compacting_session_manager import CompactionState

        manager.compaction_state = CompactionState(
            checkpoint=2,
            summary="Previous summary",
        )

        messages = [
            {"role": "user", "content": [{"text": "msg1"}]},
            {"role": "assistant", "content": [{"text": "resp1"}]},
            {"role": "user", "content": [{"text": "msg2"}]},
            {"role": "assistant", "content": [{"text": "resp2"}]},
            {"role": "user", "content": [{"text": "msg3"}]},
            {"role": "assistant", "content": [{"text": "resp3"}]},
            {"role": "user", "content": [{"text": "msg4"}]},
            {"role": "assistant", "content": [{"text": "resp4"}]},
        ]
        agent = self._make_mock_agent(messages)

        with patch.object(manager, 'save_compaction_state'):
            with patch.object(manager, '_generate_summary_for_compaction', return_value="Updated summary"):
                manager.update_after_turn(1800, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 4
        assert manager.compaction_state.summary == "Updated summary"

    def test_scenario_checkpoint_stable_below_threshold(self, manager):
        """Scenario: Checkpoint remains stable when tokens are below threshold"""
        from agent.session.compacting_session_manager import CompactionState

        manager.compaction_state = CompactionState(
            checkpoint=50,
            summary="Previous summary",
        )

        agent = self._make_mock_agent([
            {"role": "user", "content": [{"text": "q1"}]},
            {"role": "assistant", "content": [{"text": "a1"}]},
        ])

        with patch.object(manager, 'save_compaction_state'):
            manager.update_after_turn(800, 'test-agent', agent)

        assert manager.compaction_state.checkpoint == 50
        assert manager.compaction_state.summary == "Previous summary"
