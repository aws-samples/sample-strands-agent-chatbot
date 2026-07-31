"""
WorkflowAgent - Manages workflow execution for multi-task agent orchestration

Wraps workflow orchestrators (like ComposerWorkflow) and provides a unified
agent interface for workflow-based operations.

This is an Agent that delegates to workflow orchestrators for multi-step
task execution with state management and human-in-the-loop interactions.
"""

import logging
from typing import AsyncGenerator, Optional

from agents.base import BaseAgent
from agent.factory import create_session_manager

logger = logging.getLogger(__name__)


class WorkflowAgent(BaseAgent):
    """
    Agent that manages workflow execution.

    Delegates to specialized workflow orchestrators (e.g., ComposerWorkflow)
    while providing a consistent agent interface.

    Key Features:
    - Manages workflow lifecycle (start, resume, state)
    - Uses "swarm" mode session manager (simple, no compaction)
    - Supports multiple workflow types via workflow_type parameter
    - Delegates streaming to workflow.run_workflow()
    """

    def __init__(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        workflow_type: str = "compose",
        model_id: Optional[str] = None,
        temperature: Optional[float] = None,
        enabled_tools: Optional[list] = None,
        **kwargs
    ):
        """
        Initialize WorkflowAgent.

        Args:
            session_id: Session identifier for workflow state persistence
            user_id: User identifier (defaults to session_id)
            workflow_type: Type of workflow to execute ("compose", etc.)
            model_id: Bedrock model ID to use
            temperature: Model temperature (0.0 - 1.0)
            enabled_tools: List of tool IDs (typically empty for workflows)
            **kwargs: Additional BaseAgent parameters
        """
        self.workflow_type = workflow_type

        # Initialize base agent
        super().__init__(
            session_id=session_id,
            user_id=user_id,
            enabled_tools=enabled_tools or [],  # Workflows typically don't use tools
            model_id=model_id,
            temperature=temperature,
            system_prompt="",  # Workflows manage their own prompts
            caching_enabled=kwargs.get('caching_enabled', True),
            compaction_enabled=False  # Workflows use swarm mode (no compaction)
        )

        # Create workflow instance based on type
        self.workflow = self._create_workflow()

        logger.info(
            f"WorkflowAgent initialized: workflow_type={workflow_type}, "
            f"session={session_id}, model={self.model_id}"
        )

    def _get_default_model_id(self) -> str:
        """Get default model ID for workflow agents"""
        from agent.config import DEFAULT_MODEL_ID

        return DEFAULT_MODEL_ID

    def _create_session_manager(self):
        """
        Create session manager for workflow agent.

        Uses "text" mode with buffer for both reading messages and writing artifacts.
        Buffer is needed for artifact metadata writes.
        """
        logger.info(f"[WorkflowAgent] Creating session_manager with user_id={self.user_id}, session_id={self.session_id}")
        return create_session_manager(
            session_id=self.session_id,
            user_id=self.user_id,
            mode="text",  # Use text mode to share messages with ChatAgent
            compaction_enabled=False,  # No compaction needed
            use_buffer=True  # Buffer enabled for writes (artifacts, metadata)
        )

    def _create_workflow(self):
        """
        Create workflow instance based on workflow_type.

        Returns:
            Workflow orchestrator instance

        Raises:
            ValueError: If workflow_type is unknown
        """
        if self.workflow_type == "compose":
            from workflows.composer_workflow import ComposerWorkflow
            return ComposerWorkflow(
                session_id=self.session_id,
                user_id=self.user_id,
                model_id=self.model_id,
                temperature=self.temperature,
                session_manager=self.session_manager
            )
        else:
            raise ValueError(f"Unknown workflow type: {self.workflow_type}")

    async def stream_async(
        self,
        message: str,
        confirmation_response=None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Stream workflow execution events.

        Delegates to the workflow's run_workflow() method, which handles
        task sequencing, state management, and LLM invocations.

        Args:
            message: User message (for starting new workflow)
            confirmation_response: Confirmation response (for resuming workflow)
            **kwargs: Additional workflow-specific parameters

        Yields:
            SSE-formatted event strings from workflow execution
        """
        logger.debug(
            f"WorkflowAgent.stream_async: message={bool(message)}, "
            f"confirmation={bool(confirmation_response)}"
        )

        # Save user message at the START of workflow (not at the end)
        # This ensures message history is correct even if workflow fails
        if message and not confirmation_response:
            try:
                from agents.chat_agent import ChatAgent
                from strands.types.content import Message
                from strands.types.session import SessionMessage

                chat_agent = ChatAgent(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    enabled_tools=[],
                )

                existing_messages = chat_agent.session_manager.list_messages(
                    session_id=self.session_id,
                    agent_id="default"
                )
                next_message_id = len(existing_messages)

                user_msg: Message = {
                    "role": "user",
                    "content": [{"text": message}]
                }
                user_session_message = SessionMessage.from_message(user_msg, next_message_id)
                chat_agent.session_manager.create_message(
                    session_id=self.session_id,
                    agent_id="default",
                    session_message=user_session_message
                )

                if hasattr(chat_agent.session_manager, 'flush'):
                    chat_agent.session_manager.flush()

                logger.info("[Workflow] Saved user message at workflow start")
            except Exception as e:
                logger.error(f"Failed to save user message: {e}", exc_info=True)

        # Delegate to workflow orchestrator
        async for event in self.workflow.run_workflow(
            user_request=message if not confirmation_response else None,
            confirmation_response=confirmation_response
        ):
            yield event

        # After workflow completes, create ChatAgent and save to its agent.state
        # Check if document was completed (can happen on initial request or after confirmation)
        if hasattr(self.workflow, 'completed_document') and self.workflow.completed_document:
            try:
                from datetime import datetime, timezone
                from agents.chat_agent import ChatAgent

                doc = self.workflow.completed_document
                artifact_id = f"doc-{self.session_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

                # Create ChatAgent to access agent.state
                # Note: ChatAgent.__init__ automatically calls create_agent()
                chat_agent = ChatAgent(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    enabled_tools=[],  # No tools needed, just for state access
                )

                # Get current artifacts from ChatAgent's state
                artifacts = chat_agent.agent.state.get("artifacts") or {}

                # Add new artifact
                artifacts[artifact_id] = {
                    "id": artifact_id,
                    "type": "document",
                    "title": doc["title"],
                    "content": doc["content"],
                    "tool_name": "composer",  # Track which tool created this artifact
                    "metadata": {
                        "word_count": doc["word_count"],
                        "sections_count": doc["sections_count"],
                        "document_type": doc.get("document_type", "document")
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

                # Save to ChatAgent's state
                chat_agent.agent.state.set("artifacts", artifacts)

                # Sync agent state to file system
                chat_agent.session_manager.sync_agent(
                    session_id=self.session_id,
                    agent=chat_agent.agent
                )

                logger.info(f"[Workflow] Saved document to ChatAgent.state: {artifact_id}")

                # Yield artifact_created event for frontend to update artifacts list
                import json
                artifact_event = {
                    "type": "artifact_created",
                    "artifact": artifacts[artifact_id]
                }
                yield f"data: {json.dumps(artifact_event, ensure_ascii=False)}\n\n"

                # Save assistant message (user message was saved at workflow start)
                from strands.types.content import Message
                from strands.types.session import SessionMessage

                existing_messages = chat_agent.session_manager.list_messages(
                    session_id=self.session_id,
                    agent_id="default"
                )
                next_message_id = len(existing_messages)

                assistant_content = f"Document **{doc['title']}** has been created. ({doc['word_count']} words)"
                assistant_msg: Message = {
                    "role": "assistant",
                    "content": [{"text": assistant_content}]
                }
                assistant_session_message = SessionMessage.from_message(assistant_msg, next_message_id)
                chat_agent.session_manager.create_message(
                    session_id=self.session_id,
                    agent_id="default",
                    session_message=assistant_session_message
                )

                if hasattr(chat_agent.session_manager, 'flush'):
                    chat_agent.session_manager.flush()

                logger.info("[Workflow] Saved assistant message to history")
            except Exception as e:
                logger.error(f"Failed to save workflow result: {e}", exc_info=True)
