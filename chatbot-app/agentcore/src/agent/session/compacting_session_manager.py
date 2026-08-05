"""Compacting Session Manager for Long Context Optimization."""

import copy
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Dict, List

from strands.types.session import SessionAgent, SessionMessage
from typing_extensions import override

from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig


if TYPE_CHECKING:
    from strands.agent.agent import Agent

logger = logging.getLogger(__name__)


@dataclass
class CompactionState:
    """Compaction state stored in agent.state["compaction"].

    - checkpoint: Message index to load from (0 = load all)
    - summary: Summary of messages before checkpoint
    """
    checkpoint: int = 0
    summary: Optional[str] = None
    updatedAt: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "summary": self.summary,
            "updatedAt": self.updatedAt
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "CompactionState":
        if not data:
            return cls()
        return cls(
            checkpoint=int(data.get("checkpoint", 0)),
            summary=data.get("summary"),
            updatedAt=data.get("updatedAt")
        )


class CompactingSessionManager(AgentCoreMemorySessionManager):
    """Session manager with token-based context compaction.

    Compaction state is persisted in agent.state["compaction"], enabling:
    - Stateless operation across server restarts
    - Accurate token-based threshold triggering
    - Efficient checkpoint-based message loading
    - No DynamoDB dependency for compaction

    Flow:
    1. initialize(): Load compaction state from agent.state, apply if enabled
    2. After turn: Check context size, trigger compaction if threshold exceeded
    """

    def __init__(
        self,
        agentcore_memory_config: AgentCoreMemoryConfig,
        region_name: str = "us-west-2",
        token_threshold: int = 100_000,
        protected_turns: int = 2,
        max_tool_content_length: int = 500,
        user_id: Optional[str] = None,
        summarization_strategy_id: Optional[str] = None,
        metrics_only: bool = False,
        **kwargs: Any,
    ):
        """
        Initialize CompactingSessionManager.

        Args:
            agentcore_memory_config: AgentCore Memory configuration
            region_name: AWS region
            token_threshold: Trigger checkpoint when input tokens exceed this (default: 100,000)
            protected_turns: Number of recent turns to protect from truncation and keep after checkpoint (default: 2)
            max_tool_content_length: Max chars for tool content before truncation (default: 500)
            user_id: User ID for DynamoDB operations
            summarization_strategy_id: Strategy ID for LTM summarization (optional)
            metrics_only: If True, only track metrics without applying compaction (for baseline testing)
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(
            agentcore_memory_config=agentcore_memory_config,
            region_name=region_name,
            **kwargs
        )

        self.token_threshold = token_threshold
        # Load-time truncation shares the checkpoint threshold: both answer
        # "is this conversation big enough to need reducing?". Kept as its own
        # attribute so it can be tuned independently if the two diverge.
        self.truncation_threshold = token_threshold
        self.protected_turns = protected_turns
        self.max_tool_content_length = max_tool_content_length
        self.user_id = user_id
        self.region_name = region_name
        self.summarization_strategy_id = summarization_strategy_id
        self.metrics_only = metrics_only

        # Current compaction state (loaded from DynamoDB in initialize)
        self.compaction_state: Optional[CompactionState] = None

        # Last initialization info (for external metrics collection)
        self.last_init_info: Optional[Dict[str, Any]] = None

        # Cached valid cutoff points from initialize() for checkpoint calculation
        # List of message_ids where checkpoint can be set (user text messages, not toolResult)
        self._valid_cutoff_message_ids: List[int] = []
        # Total message count from Session Memory at initialize time
        self._total_message_count_at_init: int = 0
        # All messages loaded at initialize (for summary generation)
        self._all_messages_for_summary: List[Dict] = []

        # API call metrics for performance measurement
        self._api_call_count = 0
        self._api_call_total_ms = 0.0

        mode_str = "metrics_only" if metrics_only else "full_compaction"
        logger.debug(f"CompactingSessionManager: mode={mode_str}")

    def reset_api_metrics(self):
        """Reset API call metrics."""
        self._api_call_count = 0
        self._api_call_total_ms = 0.0

    def get_api_metrics(self) -> Dict[str, Any]:
        """Get API call metrics."""
        return {
            "api_call_count": self._api_call_count,
            "api_call_total_ms": self._api_call_total_ms,
        }

    def _track_api_call(self, func, *args, **kwargs):
        """Execute function and track API call metrics."""
        import time
        start = time.time()
        result = func(*args, **kwargs)
        elapsed_ms = (time.time() - start) * 1000
        self._api_call_count += 1
        self._api_call_total_ms += elapsed_ms
        return result

    @override
    def append_message(self, message: Dict, agent: "Agent", **kwargs: Any) -> None:
        """Append message with empty content filtering, image offloading, and API call tracking."""
        # Filter out empty content blocks before saving
        filtered_message = self._filter_empty_text(message)

        # Skip if content is completely empty after filtering
        content = filtered_message.get("content", [])
        if not content or (isinstance(content, list) and len(content) == 0):
            logger.debug("[Save] Skipping message with empty content (likely from stop signal)")
            return

        start = time.time()
        super().append_message(filtered_message, agent, **kwargs)
        elapsed_ms = (time.time() - start) * 1000
        self._api_call_count += 1
        self._api_call_total_ms += elapsed_ms

    @override
    def sync_agent(self, agent: "Agent", **kwargs: Any) -> None:
        """Sync agent with API call tracking."""
        start = time.time()
        super().sync_agent(agent, **kwargs)
        elapsed_ms = (time.time() - start) * 1000
        self._api_call_count += 1
        self._api_call_total_ms += elapsed_ms

    @staticmethod
    def _filter_empty_text(message: dict) -> dict:
        """Filter out empty or invalid content blocks from message.

        Removes:
        - Blocks with empty text ("")
        - Blocks with only whitespace text
        - Blocks that don't have any valid content (text, toolUse, toolResult, etc.)
        """
        if "content" not in message:
            return message
        content = message.get("content", [])
        if not isinstance(content, list):
            return message

        def is_valid_block(block):
            if not isinstance(block, dict):
                return False
            # Check if text block has non-empty content
            if "text" in block:
                text = block.get("text", "")
                return isinstance(text, str) and text.strip() != ""
            # Check if block has other valid content (toolUse, toolResult, image, document)
            return any(key in block for key in ["toolUse", "toolResult", "image", "document"])

        filtered = [block for block in content if is_valid_block(block)]
        return {**message, "content": filtered}

    def load_compaction_state(self, agent: "Agent") -> CompactionState:
        """Load compaction state from agent.state."""
        data = agent.state.get("compaction")
        if data:
            state = CompactionState.from_dict(data)
            if state.checkpoint > 0:
                logger.debug(f"Compaction state loaded from agent.state: checkpoint={state.checkpoint}")
            return state
        return CompactionState()

    def save_compaction_state(self, state: CompactionState, agent: "Agent") -> None:
        """Save compaction state to agent.state and sync to storage."""
        state.updatedAt = datetime.now(timezone.utc).isoformat()
        agent.state.set("compaction", state.to_dict())
        self.sync_agent(agent)
        logger.debug(f"Compaction state saved to agent.state: checkpoint={state.checkpoint}")

    def _get_summarization_strategy_id(self) -> Optional[str]:
        """
        Get the SUMMARIZATION strategy ID from AgentCore Memory configuration.

        Returns:
            Strategy ID for SUMMARIZATION, or None if not found
        """
        if self.summarization_strategy_id:
            return self.summarization_strategy_id

        try:
            # Try to get from Memory configuration via control plane
            response = self.memory_client.gmcp_client.get_memory(
                memoryId=self.config.memory_id
            )
            memory = response.get('memory', {})
            strategies = memory.get('strategies', memory.get('memoryStrategies', []))

            for strategy in strategies:
                strategy_payload_type = strategy.get('type', strategy.get('memoryStrategyType', ''))
                if strategy_payload_type == 'SUMMARIZATION':
                    strategy_id = strategy.get('strategyId', strategy.get('memoryStrategyId', ''))
                    logger.debug(f"Found SUMMARIZATION strategy: {strategy_id}")
                    self.summarization_strategy_id = strategy_id
                    return strategy_id

            logger.warning("SUMMARIZATION strategy not found in Memory configuration")
            return None

        except Exception as e:
            logger.error(f"Failed to get SUMMARIZATION strategy ID: {e}")
            return None

    def _retrieve_session_summaries(self) -> List[str]:
        """
        Retrieve session summaries from AgentCore LTM using list_memory_records.

        Uses SUMMARIZATION strategy namespace (session-level):
        /strategies/{summarization_strategy_id}/actors/{actor_id}/sessions/{session_id}

        Returns:
            List of summary texts for this session
        """
        strategy_id = self._get_summarization_strategy_id()
        if not strategy_id:
            logger.warning("Cannot retrieve summaries: SUMMARIZATION strategy not configured")
            return []

        try:
            import boto3

            # Build namespace path for session-level summaries
            # Pattern: /strategies/{strategyId}/actors/{actorId}/sessions/{sessionId}
            namespace = f"/strategies/{strategy_id}/actors/{self.config.actor_id}/sessions/{self.session_id}"

            logger.debug(f"Listing summaries from namespace: {namespace}")

            # Use boto3 directly for list_memory_records (not available in MemoryClient)
            gmdp = boto3.client('bedrock-agentcore', region_name=self.region_name)
            response = gmdp.list_memory_records(
                memoryId=self.config.memory_id,
                namespace=namespace,
                maxResults=100  # Get all summary chunks for this session
            )

            records = response.get('memoryRecordSummaries', [])
            logger.debug(f"Found {len(records)} summary records in session namespace")

            # Extract texts
            summaries = []
            for record in records:
                content = record.get("content", {})
                if isinstance(content, dict):
                    text = content.get("text", "").strip()
                    if text:
                        summaries.append(text)

            logger.debug(f"Retrieved {len(summaries)} summaries from LTM")
            return summaries

        except Exception as e:
            logger.error(f"Failed to retrieve summaries: {e}")
            return []

    def _prepend_summary_to_first_message(self, messages: List[Dict], summary_prefix: str) -> List[Dict]:
        """
        Prepend summary to the first user message's text content.

        Args:
            messages: List of message dicts (first should be user role)
            summary_prefix: Summary text to prepend

        Returns:
            Modified messages list with summary prepended to first message
        """
        if not messages or not summary_prefix:
            return messages

        # Deep copy to avoid modifying original
        modified_messages = copy.deepcopy(messages)

        first_msg = modified_messages[0]
        if first_msg.get('role') != 'user':
            logger.warning("First message is not user role, cannot prepend summary")
            return messages

        content = first_msg.get('content', [])
        if isinstance(content, list) and len(content) > 0:
            # Find first text block and prepend summary
            for block in content:
                if isinstance(block, dict) and 'text' in block:
                    block['text'] = summary_prefix + block['text']
                    logger.debug(" Summary prepended to first user message")
                    return modified_messages

        # No text block found - add one at the beginning
        content.insert(0, {'text': summary_prefix.rstrip()})
        first_msg['content'] = content
        logger.debug(" Summary added as new text block in first user message")

        return modified_messages

    def _truncate_text(self, text: str, max_length: int) -> str:
        """Truncate text to max_length with indicator."""
        if len(text) <= max_length:
            return text
        return text[:max_length] + f"\n... [truncated, {len(text) - max_length} chars removed]"

    def _find_protected_message_indices(self, messages: List[Dict], protected_turns: int) -> set:
        """
        Find message indices that should be protected from truncation.

        Protected messages are the most recent N complete turns (user text + assistant response).

        Args:
            messages: List of message dicts
            protected_turns: Number of recent turns to protect

        Returns:
            Set of message indices that should NOT be truncated
        """
        if protected_turns <= 0:
            return set()

        # Find all user text message indices (turn boundaries)
        turn_start_indices = []
        for i, msg in enumerate(messages):
            if msg.get('role') == 'user' and not self._has_tool_result(msg):
                turn_start_indices.append(i)

        if not turn_start_indices:
            return set()

        # Get the start index of the Nth turn from the end
        turns_to_protect = min(protected_turns, len(turn_start_indices))
        protected_start_idx = turn_start_indices[-turns_to_protect]

        # All messages from protected_start_idx onwards are protected
        protected_indices = set(range(protected_start_idx, len(messages)))

        logger.debug(
            f"Protected messages: indices {protected_start_idx}~{len(messages)-1} "
            f"({len(protected_indices)} messages, {turns_to_protect} turns)"
        )

        return protected_indices

    def _truncate_tool_contents(self, messages: List[Dict], protected_indices: Optional[set] = None) -> tuple:
        """
        Stage 1 Compaction: Truncate long tool inputs/results and replace images with placeholders.

        Reduces token usage by:
        - Truncating toolUse.input (large tool parameters)
        - Truncating toolResult.content (large tool outputs)
        - Replacing image blocks with text placeholders

        Protected messages (recent turns) are NOT truncated to preserve latest context.

        Args:
            messages: List of message dicts
            protected_indices: Set of message indices to skip truncation (optional)

        Returns:
            Tuple of (modified_messages, truncation_count, chars_saved)
        """
        modified_messages = copy.deepcopy(messages)
        truncation_count = 0
        total_chars_saved = 0

        if protected_indices is None:
            protected_indices = set()

        for msg_idx, msg in enumerate(modified_messages):
            # Skip protected messages (recent turns)
            if msg_idx in protected_indices:
                continue
            content = msg.get('content', [])
            if not isinstance(content, list):
                continue

            # Process content blocks - need index for replacement
            for block_idx, block in enumerate(content):
                if not isinstance(block, dict):
                    continue

                # Replace image blocks with placeholder
                if 'image' in block:
                    image_data = block['image']
                    image_format = image_data.get('format', 'unknown')
                    source = image_data.get('source', {})
                    original_bytes = source.get('bytes', b'')
                    original_size = len(original_bytes) if isinstance(original_bytes, bytes) else 0

                    content[block_idx] = {
                        'text': f'[Image placeholder: format={image_format}, original_size={original_size} bytes]'
                    }
                    truncation_count += 1
                    total_chars_saved += original_size

                # Replace document blocks with placeholder
                elif 'document' in block:
                    doc_data = block['document']
                    doc_format = doc_data.get('format', 'unknown')
                    doc_name = doc_data.get('name', 'unknown')
                    source = doc_data.get('source', {})
                    original_bytes = source.get('bytes', b'')
                    original_size = len(original_bytes) if isinstance(original_bytes, bytes) else 0

                    content[block_idx] = {
                        'text': f'[Document placeholder: name={doc_name}, format={doc_format}, original_size={original_size} bytes]'
                    }
                    truncation_count += 1
                    total_chars_saved += original_size

                # Truncate toolUse input
                elif 'toolUse' in block:
                    tool_use = block['toolUse']
                    tool_input = tool_use.get('input', {})

                    if isinstance(tool_input, dict):
                        # Serialize to check length
                        input_str = json.dumps(tool_input, ensure_ascii=False)
                        if len(input_str) > self.max_tool_content_length:
                            original_len = len(input_str)
                            # Replace with truncated string representation
                            tool_use['input'] = {"_truncated": self._truncate_text(input_str, self.max_tool_content_length)}
                            truncation_count += 1
                            total_chars_saved += original_len - self.max_tool_content_length

                # Truncate toolResult content
                elif 'toolResult' in block:
                    tool_result = block['toolResult']
                    result_content = tool_result.get('content', [])

                    if isinstance(result_content, list):
                        for result_idx, result_block in enumerate(result_content):
                            if not isinstance(result_block, dict):
                                continue

                            # Replace image in toolResult with placeholder
                            if 'image' in result_block:
                                image_data = result_block['image']
                                image_format = image_data.get('format', 'unknown')
                                source = image_data.get('source', {})
                                original_bytes = source.get('bytes', b'')
                                if isinstance(original_bytes, bytes):
                                    original_size = len(original_bytes)
                                else:
                                    original_size = 0

                                result_content[result_idx] = {
                                    'text': f'[Image placeholder: format={image_format}, original_size={original_size} bytes]'
                                }
                                truncation_count += 1
                                total_chars_saved += original_size

                            elif 'text' in result_block:
                                text = result_block['text']
                                if len(text) > self.max_tool_content_length:
                                    original_len = len(text)
                                    result_block['text'] = self._truncate_text(text, self.max_tool_content_length)
                                    truncation_count += 1
                                    total_chars_saved += original_len - self.max_tool_content_length

                            elif 'json' in result_block:
                                json_content = result_block['json']
                                json_str = json.dumps(json_content, ensure_ascii=False)
                                if len(json_str) > self.max_tool_content_length:
                                    original_len = len(json_str)
                                    # Simply convert to truncated text instead of recursive dict processing
                                    result_block.pop('json')
                                    result_block['text'] = self._truncate_text(json_str, self.max_tool_content_length)
                                    truncation_count += 1
                                    total_chars_saved += original_len - self.max_tool_content_length

        if truncation_count > 0:
            logger.debug(
                f" Stage 1 Truncation: {truncation_count} items truncated (text/json/images), "
                f"~{total_chars_saved} chars/bytes saved"
            )

        return modified_messages, truncation_count, total_chars_saved

    @staticmethod
    def _estimate_tokens(messages: List[Dict]) -> int:
        """Rough token estimate for a message list, used only to decide whether
        truncation is worth doing at load time.

        The real count is only known after the model call reports usage, so this
        is a serialized-length heuristic at ~4 chars/token. It is intentionally
        crude: the decision it feeds is "are we anywhere near the limit", and the
        accurate number arrives later via update_after_turn().

        Base64 image and document payloads are counted at their encoded length,
        which overestimates their token cost — that errs toward compacting, which
        is the safe direction for the media that dominates context size.
        """
        try:
            return len(json.dumps(messages, ensure_ascii=False, default=str)) // 4
        except (TypeError, ValueError):
            # Unserializable content: assume it is large rather than skip compaction.
            return sys.maxsize

    def _should_truncate(self, messages: List[Dict]) -> tuple[bool, int]:
        """Whether load-time truncation should run, plus the estimate behind it.

        Truncation used to be unconditional, which meant every session load
        permanently* discarded old tool output and replaced images and documents
        with placeholders — even for a conversation using a fraction of the
        window. On the 1M-token models in the picker that threw away detail the
        model had ample room for.

        (*permanently for that turn only: the stored session is never rewritten,
        so relaxing this restores full history on the next load.)
        """
        estimated = self._estimate_tokens(messages)
        return estimated >= self.truncation_threshold, estimated

    def _has_tool_result(self, message: Dict) -> bool:
        """Check if message contains toolResult block."""
        content = message.get('content', [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and 'toolResult' in block:
                    return True
        return False


    def initialize(self, agent: "Agent", **kwargs: Any) -> None:
        """
        Initialize agent with simplified two-feature compaction.

        Flow:
        1. Load compaction state from DynamoDB
        2. Feature 1 - Message Loading:
           - If checkpoint > 0: Load messages[checkpoint:] + prepend summary
           - Else: Load all messages
        3. Feature 2 - Truncation (always applied):
           - Truncate old tool contents (protect recent 2 turns)

        Args:
            agent: Agent to initialize
            **kwargs: Additional arguments
        """
        from strands.agent.state import AgentState

        if agent.agent_id in self._latest_agent_message:
            from strands.types.exceptions import SessionException
            raise SessionException("The `agent_id` of an agent must be unique in a session.")

        self._latest_agent_message[agent.agent_id] = None

        # Check if agent exists in session
        session_agent = self.session_repository.read_agent(self.session_id, agent.agent_id)

        if session_agent is None:
            # New agent - create normally
            logger.debug(f"agent_id=<{agent.agent_id}> | session_id=<{self.session_id}> | creating agent")

            session_agent = SessionAgent.from_agent(agent)
            self.session_repository.create_agent(self.session_id, session_agent)

            # Initialize messages with sequential indices
            session_message = None
            for i, message in enumerate(agent.messages):
                session_message = SessionMessage.from_message(message, i)
                self.session_repository.create_message(self.session_id, agent.agent_id, session_message)
            self._latest_agent_message[agent.agent_id] = session_message

            # No compaction for new agent
            self.compaction_state = CompactionState()
            self.last_init_info = {
                "stage": "none",
                "original_messages": 0,
                "final_messages": 0,
                "truncation_count": 0,
                "compaction_overhead_ms": 0,
            }

            # Initialize cached cutoff points for new agent
            self._valid_cutoff_message_ids = []
            self._total_message_count_at_init = 0
            self._all_messages_for_summary = []

        else:
            # Existing agent - restore with compaction (or metrics_only mode)
            mode_label = "metrics_only" if self.metrics_only else "with compaction"
            logger.debug(f"agent_id=<{agent.agent_id}> | session_id=<{self.session_id}> | restoring agent ({mode_label})")

            # Start timing compaction overhead
            compaction_start_time = time.time()

            agent.state = AgentState(session_agent.state)
            session_agent.initialize_internal_state(agent)

            # Restore conversation manager state
            prepend_messages = agent.conversation_manager.restore_from_session(
                session_agent.conversation_manager_state
            )
            if prepend_messages is None:
                prepend_messages = []

            # Load ALL messages from Session Memory (limit=None fetches all)
            all_session_messages = self.session_repository.list_messages(
                session_id=self.session_id,
                agent_id=agent.agent_id,
            )

            # Update latest message tracking
            if len(all_session_messages) > 0:
                self._latest_agent_message[agent.agent_id] = all_session_messages[-1]

            # Cache total message count
            self._total_message_count_at_init = len(all_session_messages)

            if self.metrics_only:
                # Metrics-only mode: Load all messages without compaction
                self.compaction_state = CompactionState()
                self._valid_cutoff_message_ids = []
                self._all_messages_for_summary = []
                messages_to_process = [sm.to_message() for sm in all_session_messages]
                original_message_count = len(messages_to_process)
                agent.messages = prepend_messages + messages_to_process

                compaction_overhead_ms = (time.time() - compaction_start_time) * 1000
                self.last_init_info = {
                    "stage": "metrics_only",
                    "original_messages": original_message_count,
                    "final_messages": len(agent.messages),
                    "truncation_count": 0,
                    "compaction_overhead_ms": compaction_overhead_ms,
                }
            else:
                # Full compaction mode
                self.compaction_state = self.load_compaction_state(agent)
                conv_manager_offset = agent.conversation_manager.removed_message_count
                checkpoint = self.compaction_state.checkpoint
                effective_offset = max(conv_manager_offset, checkpoint)

                stage = "none"
                self._valid_cutoff_message_ids = []
                self._all_messages_for_summary = [sm.to_message() for sm in all_session_messages]

                for idx, sm in enumerate(all_session_messages):
                    msg = sm.to_message()
                    if msg.get('role') == 'user' and not self._has_tool_result(msg):
                        self._valid_cutoff_message_ids.append(idx)

                session_messages = all_session_messages[effective_offset:] if effective_offset > 0 else all_session_messages
                messages_to_process = [sm.to_message() for sm in session_messages]
                original_message_count = len(messages_to_process)

                if checkpoint > 0 and effective_offset >= checkpoint:
                    if self.compaction_state.summary and messages_to_process:
                        summary_prefix = f"""<conversation_summary>
The following is a summary of our previous conversation:

{self.compaction_state.summary}

Please continue the conversation with this context in mind.
</conversation_summary>

"""
                        messages_to_process = self._prepend_summary_to_first_message(messages_to_process, summary_prefix)
                    stage = "checkpoint"

                # Truncate tool output only when the conversation is actually
                # near the model's limit. Doing it on every load discarded
                # detail that a 1M-token model had room for.
                should_truncate, estimated_tokens = self._should_truncate(messages_to_process)
                truncation_count = 0
                if should_truncate:
                    protected_indices = self._find_protected_message_indices(messages_to_process, self.protected_turns)
                    truncated_messages, truncation_count, chars_saved = self._truncate_tool_contents(
                        messages_to_process, protected_indices=protected_indices
                    )
                    logger.info(
                        f"[Compaction] Truncated {truncation_count} block(s): "
                        f"~{estimated_tokens:,} estimated tokens >= {self.truncation_threshold:,} threshold"
                    )
                else:
                    truncated_messages = messages_to_process
                    # info, not debug: this is the decision the model-sized
                    # threshold exists to make, and the only record of which
                    # threshold was in effect for this model. At debug level it
                    # is absent from deployed logs, leaving no way to tell an
                    # intentional skip from compaction silently not running.
                    logger.info(
                        f"[Compaction] Skipped truncation: ~{estimated_tokens:,} estimated tokens "
                        f"< {self.truncation_threshold:,} threshold"
                    )

                if truncation_count > 0:
                    stage = "checkpoint+truncation" if stage == "checkpoint" else "truncation"

                agent.messages = prepend_messages + truncated_messages
                compaction_overhead_ms = (time.time() - compaction_start_time) * 1000

                self.last_init_info = {
                    "stage": stage,
                    "original_messages": original_message_count,
                    "final_messages": len(agent.messages),
                    "truncation_count": truncation_count,
                    "compaction_overhead_ms": compaction_overhead_ms,
                }

        # Mark that we have an existing agent
        self.has_existing_agent = True

    def update_after_turn(self, input_tokens: int, agent_id: str, agent: "Agent") -> None:
        """Update compaction state after turn completion.

        Called after agent response with the last LLM call's context size.
        Uses cached valid cutoff points from initialize().

        Args:
            input_tokens: Context size from the last LLM call (not accumulated)
            agent_id: Agent ID (for logging)
            agent: Agent instance (for persisting state)
        """
        if self.compaction_state is None:
            self.compaction_state = CompactionState()

        if self.metrics_only:
            logger.debug(f"Metrics-only: context_tokens={input_tokens:,} (no compaction)")
            return

        if input_tokens > self.token_threshold:
            logger.info(f"Threshold exceeded: {input_tokens:,} > {self.token_threshold:,}")

            # Always recompute cutoff points from current agent.messages
            # Cache from initialize() becomes stale as new messages are appended
            self._valid_cutoff_message_ids = []
            self._all_messages_for_summary = []
            for idx, msg in enumerate(agent.messages):
                self._all_messages_for_summary.append(msg)
                if msg.get('role') == 'user' and not self._has_tool_result(msg):
                    self._valid_cutoff_message_ids.append(idx)

            total_turns = len(self._valid_cutoff_message_ids)
            if total_turns <= self.protected_turns:
                logger.debug(
                    f"Only {total_turns} turns available (need > {self.protected_turns}), "
                    f"keeping all messages"
                )
                self.save_compaction_state(self.compaction_state, agent)
                return

            new_checkpoint = self._valid_cutoff_message_ids[-(self.protected_turns)]
            current_checkpoint = self.compaction_state.checkpoint

            if new_checkpoint > current_checkpoint:
                logger.info(
                    f"Checkpoint update: {input_tokens:,} tokens > {self.token_threshold:,} threshold, "
                    f"checkpoint {current_checkpoint} -> {new_checkpoint}"
                )

                messages_to_summarize = self._all_messages_for_summary[:new_checkpoint] if self._all_messages_for_summary else []
                summary = self._generate_summary_for_compaction(messages_to_summarize)

                self.compaction_state.checkpoint = new_checkpoint
                self.compaction_state.summary = summary

                logger.debug(
                    f"Checkpoint updated: {new_checkpoint}, "
                    f"summary_length={len(summary) if summary else 0}"
                )

        self.save_compaction_state(self.compaction_state, agent)

    def _generate_summary_for_compaction(self, messages: List[Dict]) -> Optional[str]:
        """
        Generate a summary of messages for compaction.

        First tries to retrieve from LTM (if SUMMARIZATION strategy is configured),
        falls back to simple extraction of key points.

        Note: New summary completely REPLACES existing summary (no accumulation).
        Each checkpoint has its own summary covering all messages before it.

        Args:
            messages: Messages to summarize (those before the checkpoint)

        Returns:
            Summary text or None if generation fails
        """
        if not messages:
            return None

        # Try to retrieve existing summaries from LTM first
        # Note: New summary REPLACES existing summary (no accumulation)
        summaries = self._retrieve_session_summaries()
        if summaries:
            combined = "\n\n".join(summaries)
            logger.debug(f" Retrieved {len(summaries)} summaries from LTM for compaction")
            return combined

        # Fallback: extract key points from messages (simple approach)
        # This is a basic fallback - LTM summaries are preferred
        try:
            key_points = []
            tools_used = set()
            for msg in messages:
                role = msg.get('role', '')
                content = msg.get('content', [])

                if not isinstance(content, list):
                    continue

                if role == 'user':
                    for block in content:
                        if isinstance(block, dict) and 'text' in block:
                            text = block['text']
                            first_line = text.split('\n')[0][:150]
                            if first_line and not first_line.startswith('<'):
                                key_points.append(f"- User: {first_line}")
                                break
                elif role == 'assistant':
                    for block in content:
                        if isinstance(block, dict):
                            if 'toolUse' in block:
                                tool_name = block['toolUse'].get('name', '')
                                if tool_name:
                                    tools_used.add(tool_name)
                            elif 'text' in block:
                                text = block['text']
                                first_line = text.split('\n')[0][:150]
                                if first_line and not first_line.startswith('<'):
                                    key_points.append(f"- Assistant: {first_line}")
                                    break

            if key_points:
                parts = ["Previous conversation:"]
                parts.append("\n".join(key_points[-15:]))
                if tools_used:
                    parts.append(f"\nTools used: {', '.join(sorted(tools_used))}")
                new_summary = "\n".join(parts)
                logger.debug(f" Generated fallback summary with {len(key_points)} key points, {len(tools_used)} tools")
                return new_summary

        except Exception as e:
            logger.warning(f"Failed to generate fallback summary: {e}")

        return None
