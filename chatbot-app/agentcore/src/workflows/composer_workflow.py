"""
Composer Workflow - 6-Task Document Composition Workflow

Orchestrates multi-step document creation with human-in-the-loop confirmation.

This is NOT an Agent - it's a workflow orchestrator that uses Agent internally
for LLM invocations but manages the overall process, state, and task sequencing.

Tasks:
1. Intake - Extract requirements from user request
2. Outline - Generate document outline
3. Confirm - Get user confirmation on outline (with interrupt)
4. Body Write - Write each section (loop)
5. Intro/Outro - Write introduction and conclusion
6. Review - Final review and polish

State is persisted in DynamoDB under 'writingWorkflow' key.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional, Dict, Any

from strands import Agent
from agents.model_factory import build_model

from models.composer_schemas import (
    WritingTaskStatus,
    WritingWorkflowStatus,
    WritingRequirements,
    OutlineSection,
    OutlineSubsection,
    DocumentOutline,
    OutlineConfirmation,
    SectionContent,
    BodyWriteProgress,
    IntroOutroContent,
    ReviewResult,
    WritingWorkflowState,
    WritingProgressEvent,
    WritingOutlineEvent,
    WritingCompleteEvent,
)

logger = logging.getLogger(__name__)


# ============================================================
# Task Prompts (English only)
# ============================================================

INTAKE_PROMPT = """Analyze the user request and conversation context to extract document requirements.

{conversation_context}

User request:
{user_request}

Extract and return a JSON object with this exact structure:

{{
  "document_type": "Type of document (report, article, essay, proposal, blog post, etc.)",
  "topic": "Main topic or subject",
  "length_guidance": "Length guidance (short, medium, long, or specific word count)",
  "extracted_points": [
    "Array of specific, concrete information from the conversation to include",
    "Technical facts, statistics, arguments, insights from the conversation",
    "Each point should be self-contained and directly usable in writing"
  ]
}}

Guidelines for extracted_points:
- Extract 5-15 concrete points from the conversation context
- Include: technical facts, API details, statistics, benchmarks, code examples, logical arguments
- Each point should be self-contained (e.g., "Claude API tool_choice parameter supports 'auto', 'any', or specific tool name")
- If no conversation context, use empty array []

Respond with ONLY valid JSON matching the structure above, no other text."""

OUTLINE_PROMPT = """Create a clear and logical document outline.

<document_info>
Type: {document_type}
{topic_section}
Length: {length_guidance} (~{word_target} words)
</document_info>

{conversation_context_section}

{extracted_points_section}

{feedback_section}

<outline_guidelines>
- Use descriptive section titles that convey value to the reader
- Create a logical flow that builds understanding naturally
- Balance section lengths based on importance (not all equal)
- DO NOT include "Introduction" or "Conclusion" - written separately
- Assign relevant context points (by number) to each section
</outline_guidelines>

Return JSON with:
- title: Document title
- sections: Array with:
  - section_id: Unique ID (e.g., "s1", "s2")
  - title: Section title
  - description: What this section covers
  - subsections: Optional nested sections
  - estimated_words: Word count estimate
  - assigned_points: Point numbers to use (e.g., [0, 2, 5])
- total_estimated_words: Total word count

Respond with ONLY valid JSON, no other text."""

BODY_SECTION_PROMPT = """You are writing a {document_type} about {topic}.

<content_requirements>
- Section: {section_title}
- Purpose: {section_description}
- Target length: ~{estimated_words} words
</content_requirements>

{context_points_section}

<previous_context>
{previous_context}
</previous_context>

<writing_style>
- Write in natural, flowing prose - avoid bullet points and numbered lists
- Write lists in natural language: "key aspects include X, Y, and Z" rather than bulleted lists
- Be direct and specific - use concrete examples rather than abstract statements
- Prefer active voice over passive voice
- Vary sentence rhythm - mix shorter punchy sentences with longer explanatory ones
- Use simple, clear words when they work just as well as complex ones
</writing_style>

<what_to_avoid>
- Overused phrases: "delve into", "navigate", "leverage", "robust", "seamless"
- Throat-clearing openings: "In this section, we will explore..."
- Meta-commentary about what you're doing
- The section heading itself - write content only
</what_to_avoid>

Your goal is to be helpful, accurate, and write in a natural voice that engages the reader.

IMPORTANT: Write the section content directly. Do NOT include the section heading (## {section_title}).

Respond with ONLY the section content body."""

INTRO_OUTRO_PROMPT = """Write the introduction and conclusion for this {document_type}.

<document_info>
Title: {document_title}
Structure: {outline_summary}
Content: {section_summaries}
</document_info>

<introduction_guidelines>
- Start with something engaging - a question, surprising fact, or relatable scenario
- Avoid cliché openings: "In today's world...", "In recent years..."
- Establish why this matters to the reader
- Naturally preview content - don't list sections
- Write in natural, flowing prose
</introduction_guidelines>

<conclusion_guidelines>
- Offer a final insight or perspective beyond summary
- Skip formulaic starts: "In conclusion...", "To summarize..."
- End with purpose - a takeaway or thought to consider
- Match the document's scope and style
</conclusion_guidelines>

<style>
- Be direct and confident
- Use active voice where natural
- Keep flow smooth between ideas
- Write in natural language, not lists
</style>

Return JSON with:
- introduction: The introduction text
- conclusion: The conclusion text

Respond with ONLY valid JSON, no other text."""

REVIEW_PROMPT = """Review this {document_type} and suggest edits for clarity and natural flow.

<document>
{document_title}

{full_document}
</document>

<review_focus>
- Clarity: Every sentence should be easy to understand on first read
- Flow: Smooth transitions between paragraphs and sections
- Conciseness: Remove unnecessary words or redundant phrases
- Voice: Prefer active voice, vary sentence structure
</review_focus>

<watch_for>
- Overused words: "delve", "leverage", "robust", "seamless", "navigate"
- Repetitive sentence patterns or starters
- Excessive hedging: "It might be said that...", "One could argue..."
</watch_for>

Return edits as find-and-replace pairs. Each edit should have:
- old_text: Exact text to find (must match exactly)
- new_text: Replacement text

Return JSON with:
- edits: Array of {{"old_text": "...", "new_text": "..."}}
- edit_count: Number of edits

Only include meaningful improvements. If document is already good, return empty edits array.
Respond with ONLY valid JSON, no other text."""


class ComposerWorkflow:
    """
    Orchestrates the 6-task document composition workflow.

    This is a workflow orchestrator, not an Agent. It manages state,
    task sequencing, and human-in-the-loop interactions while using
    Agent internally for LLM invocations.
    """

    # Task names for progress reporting
    TASK_NAMES = {
        1: "Requirements Analysis",
        2: "Outline Generation",
        3: "Outline Confirmation",
        4: "Section Writing",
        5: "Introduction & Conclusion",
        6: "Final Review"
    }

    def __init__(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        model_id: Optional[str] = None,
        temperature: Optional[float] = None,
        session_manager: Optional[Any] = None
    ):
        """
        Initialize ComposerWorkflow.

        Args:
            session_id: Session identifier for state persistence
            user_id: User identifier for DynamoDB operations
            model_id: Bedrock model ID to use
            temperature: Model temperature
            session_manager: Session manager for loading conversation history (optional)
        """
        self.session_id = session_id
        self.user_id = user_id or session_id
        from agent.config import DEFAULT_MODEL_ID

        self.model_id = model_id or DEFAULT_MODEL_ID
        self.temperature = temperature if temperature is not None else 0.7
        self.region_name = os.environ.get('AWS_REGION', 'us-west-2')
        self.session_manager = session_manager

        # Workflow state (loaded from DynamoDB)
        self.state: Optional[WritingWorkflowState] = None

        # DynamoDB table (lazy initialized)
        self._dynamodb_table = None

        # Completed document (set after workflow completes)
        # WorkflowAgent will read this and save to agent_state + messages
        self.completed_document: Optional[Dict[str, Any]] = None

        logger.debug(
            f"ComposerWorkflow initialized: session={session_id}, user={self.user_id}, "
            f"model={self.model_id}, temp={self.temperature}"
        )

    # ============================================================
    # DynamoDB State Persistence
    # ============================================================

    def _get_dynamodb_table(self):
        """Lazy initialization of DynamoDB table"""
        if self._dynamodb_table is None:
            import boto3
            project_name = os.environ.get('PROJECT_NAME', 'strands-agent-chatbot')
            table_name = f"{project_name}-users-v2"
            dynamodb = boto3.resource('dynamodb', region_name=self.region_name)
            self._dynamodb_table = dynamodb.Table(table_name)
            logger.debug(f"DynamoDB table initialized: {table_name}")
        return self._dynamodb_table

    def _get_session_key(self) -> dict:
        """Get DynamoDB key for session operations"""
        return {
            'userId': self.user_id,
            'sk': f'SESSION#{self.session_id}'
        }

    def load_workflow_state(self) -> WritingWorkflowState:
        """Load workflow state from DynamoDB session metadata"""
        if not self.user_id:
            logger.debug("No user_id set, returning new workflow state")
            return WritingWorkflowState()

        try:
            table = self._get_dynamodb_table()
            response = table.get_item(
                Key=self._get_session_key(),
                ProjectionExpression='writingWorkflow'
            )

            if 'Item' in response and 'writingWorkflow' in response['Item']:
                state = WritingWorkflowState.from_dict(response['Item']['writingWorkflow'])
                logger.debug(
                    f"Workflow state loaded: status={state.status}, task={state.current_task}"
                )
                return state

            return WritingWorkflowState()

        except Exception as e:
            logger.warning(f"Error loading workflow state: {e}")
            return WritingWorkflowState()

    def save_workflow_state(self) -> None:
        """Save workflow state to DynamoDB session metadata"""
        if not self.user_id or not self.state:
            logger.debug("No user_id or state, skipping save")
            return

        try:
            self.state.updated_at = datetime.now(timezone.utc).isoformat()
            table = self._get_dynamodb_table()
            table.update_item(
                Key=self._get_session_key(),
                UpdateExpression='SET writingWorkflow = :state',
                ExpressionAttributeValues={
                    ':state': self.state.to_dict()
                }
            )
            logger.debug(
                f"Workflow state saved: status={self.state.status}, task={self.state.current_task}"
            )

        except Exception as e:
            logger.error(f"Error saving workflow state: {e}")

    # ============================================================
    # Main Orchestration
    # ============================================================

    async def run_workflow(
        self,
        user_request: Optional[str] = None,
        confirmation_response: Optional[OutlineConfirmation] = None
    ) -> AsyncGenerator[str, None]:
        """
        Run the writing workflow.

        This method handles both starting a new workflow and resuming after confirmation.

        Args:
            user_request: Initial user request (for starting new workflow)
            confirmation_response: Outline confirmation (for resuming after interrupt)

        Yields:
            SSE-formatted event strings
        """
        # Load existing state or create new
        self.state = self.load_workflow_state()

        # Determine mode: new workflow vs resume after confirmation
        if confirmation_response is not None:
            # Resume after outline confirmation
            async for event in self._handle_outline_confirmation(confirmation_response):
                yield event
            return

        if not user_request:
            yield self._format_sse({"type": "error", "message": "No user request provided"})
            return

        # Start new workflow
        self.state = WritingWorkflowState(
            user_request=user_request,
            status=WritingWorkflowStatus.IN_PROGRESS,
            current_task=1,
            created_at=datetime.now(timezone.utc).isoformat()
        )
        self.save_workflow_state()

        # Yield start event
        yield self._format_sse({"type": "start"})

        try:
            # Task 1: Intake
            async for event in self._task_intake(user_request):
                yield event

            if self.state.status == WritingWorkflowStatus.FAILED:
                return

            # Task 2: Outline
            async for event in self._task_outline():
                yield event

            if self.state.status == WritingWorkflowStatus.FAILED:
                return

            # Task 3: Confirm (yields interrupt and returns)
            async for event in self._task_confirm():
                yield event

            # Note: Workflow pauses here. Resume via confirmation_response.

        except Exception as e:
            logger.error(f"Workflow error: {e}")
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = str(e)
            self.save_workflow_state()
            yield self._format_sse({"type": "error", "message": str(e)})

        finally:
            yield self._format_sse({"type": "end"})

    async def _handle_outline_confirmation(
        self,
        confirmation: OutlineConfirmation
    ) -> AsyncGenerator[str, None]:
        """
        Handle outline confirmation and continue workflow.

        Args:
            confirmation: User's confirmation response

        Yields:
            SSE-formatted event strings
        """
        yield self._format_sse({"type": "start"})

        try:
            if confirmation.approved:
                # Outline approved - continue to body writing
                logger.info("Outline approved, continuing to body writing")

                # Task 4: Body Write
                async for event in self._task_body_write():
                    yield event

                if self.state.status == WritingWorkflowStatus.FAILED:
                    return

                # Task 5: Intro/Outro
                async for event in self._task_intro_outro():
                    yield event

                if self.state.status == WritingWorkflowStatus.FAILED:
                    return

                # Task 6: Review
                async for event in self._task_review():
                    yield event

            else:
                # Outline rejected - regenerate with feedback
                if confirmation.feedback:
                    self.state.outline_feedback.append(confirmation.feedback)
                if confirmation.specific_changes:
                    self.state.outline_feedback.extend(confirmation.specific_changes)

                if self.state.outline_attempts >= self.state.max_outline_attempts:
                    # Max attempts reached - force continue
                    logger.warning("Max outline attempts reached, forcing continuation")
                    yield self._format_sse({
                        "type": "text",
                        "content": "\n\n*Maximum revision attempts reached. Proceeding with current outline.*\n\n"
                    })

                    # Continue with current outline
                    async for event in self._task_body_write():
                        yield event

                    if self.state.status == WritingWorkflowStatus.FAILED:
                        return

                    async for event in self._task_intro_outro():
                        yield event

                    if self.state.status == WritingWorkflowStatus.FAILED:
                        return

                    async for event in self._task_review():
                        yield event
                else:
                    # Regenerate outline with feedback
                    logger.info(f"Regenerating outline (attempt {self.state.outline_attempts + 1})")

                    async for event in self._task_outline(
                        feedback="\n".join(self.state.outline_feedback)
                    ):
                        yield event

                    if self.state.status == WritingWorkflowStatus.FAILED:
                        return

                    # Request confirmation again
                    async for event in self._task_confirm():
                        yield event

                    return  # Pause again for confirmation

        except Exception as e:
            logger.error(f"Error handling confirmation: {e}")
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = str(e)
            self.save_workflow_state()
            yield self._format_sse({"type": "error", "message": str(e)})

        finally:
            yield self._format_sse({"type": "end"})

    # ============================================================
    # Task Implementations
    # ============================================================

    async def _task_intake(
        self,
        user_request: str
    ) -> AsyncGenerator[str, None]:
        """Task 1: Extract requirements from user request"""
        self.state.current_task = 1
        self.save_workflow_state()

        # Yield progress event
        progress = WritingProgressEvent(
            task=1,
            task_name=self.TASK_NAMES[1],
            status=WritingTaskStatus.IN_PROGRESS,
            details="Analyzing your writing request..."
        )
        yield self._format_sse(progress.model_dump())

        # Load conversation history from session
        conversation_context = ""
        try:
            # Flush buffer before reading (critical for cross-agent communication)
            # ChatAgent uses LocalSessionBuffer which buffers writes
            if self.session_manager:
                # Use provided session_manager and flush if it has a buffer
                if hasattr(self.session_manager, 'flush'):
                    self.session_manager.flush()
                    logger.info(f"[Compose] Flushed buffer for session {self.session_id}")
                session_manager = self.session_manager
                logger.info(f"[Compose] Using provided session_manager: {type(session_manager).__name__}")
                # Debug: log actor_id being used
                if hasattr(session_manager, 'config') and hasattr(session_manager.config, 'actor_id'):
                    logger.info(f"[Compose] Session manager actor_id: {session_manager.config.actor_id}")
                logger.info(f"[Compose] WorkflowAgent user_id: {self.user_id}")
            else:
                # Create new session manager if not provided
                from agent.factory import create_session_manager
                session_manager = create_session_manager(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    mode="text",
                    compaction_enabled=False,
                    use_buffer=True
                )
                logger.info(f"[Compose] Created new session_manager: {type(session_manager).__name__}")

            # Get recent messages (last 10 for context)
            logger.info(f"[Compose] Loading messages for session_id={self.session_id}, agent_id=default")
            messages = session_manager.list_messages(
                session_id=self.session_id,
                agent_id="default",
                limit=20  # Get more to filter
            )
            logger.info(f"[Compose] Loaded {len(messages) if messages else 0} messages from session")

            if messages:
                context_lines = ["Previous conversation context:"]
                # Take last 10 messages for context
                for i, msg in enumerate(messages[-10:]):
                    if hasattr(msg, 'message'):
                        role = msg.message.get('role', '')
                        content = msg.message.get('content', [])
                        # Extract text from content blocks
                        text_content = ""
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and 'text' in block:
                                    text_content += block['text']
                        elif isinstance(content, str):
                            text_content = content

                        if role and text_content:
                            context_lines.append(f"{role.capitalize()}: {text_content[:200]}")  # Truncate long messages
                            logger.debug(f"[Compose] Message {i}: {role} - {text_content[:50]}...")

                if len(context_lines) > 1:  # More than just the header
                    conversation_context = "\n".join(context_lines) + "\n"
                    # Store in self for use in other tasks
                    self.conversation_context = conversation_context
                    logger.info(f"[Compose] Built context with {len(context_lines)-1} messages ({len(conversation_context)} chars)")
                else:
                    self.conversation_context = ""
                    logger.warning(f"[Compose] No valid messages found in {len(messages)} loaded messages")
            else:
                self.conversation_context = ""
                logger.warning(f"[Compose] No messages found for session {self.session_id}")
        except Exception as e:
            logger.error(f"[Compose] Failed to load conversation history: {e}", exc_info=True)
            self.conversation_context = ""
            # Continue without context

        # Check if user_request is already structured JSON (from frontend wizard)
        requirements = None
        try:
            # Try to parse as JSON first
            import json
            data = json.loads(user_request)
            if isinstance(data, dict) and 'document_type' in data and 'topic' in data:
                # Direct structured input from frontend - no LLM parsing needed
                # Ensure extracted_points is a list (not None)
                if 'extracted_points' not in data or data['extracted_points'] is None:
                    data['extracted_points'] = []
                requirements = WritingRequirements(**data)
                logger.info(f"[Compose] Using structured input from frontend: {data['document_type']}")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            # Not JSON or invalid structure - fall back to LLM parsing
            logger.debug(f"[Compose] Failed to parse as JSON: {e}")
            pass

        # If not structured JSON, use LLM to parse natural language request
        if not requirements:
            logger.info("[Compose] Parsing natural language request with LLM")
            prompt = INTAKE_PROMPT.format(
                user_request=user_request,
                conversation_context=conversation_context
            )
            response = await self._invoke_llm(prompt)
            requirements = self._parse_requirements(response)

        # Save requirements
        try:
            self.state.requirements = requirements
            self.save_workflow_state()

            # Yield completion
            progress = WritingProgressEvent(
                task=1,
                task_name=self.TASK_NAMES[1],
                status=WritingTaskStatus.COMPLETED,
                details=f"Identified: {requirements.document_type} about '{requirements.topic}'"
            )
            yield self._format_sse(progress.model_dump())

        except Exception as e:
            logger.error(f"Failed to parse requirements: {e}")
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = f"Failed to analyze request: {e}"
            self.save_workflow_state()

            progress = WritingProgressEvent(
                task=1,
                task_name=self.TASK_NAMES[1],
                status=WritingTaskStatus.FAILED,
                details=str(e)
            )
            yield self._format_sse(progress.model_dump())

    async def _task_outline(
        self,
        feedback: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Task 2: Generate document outline"""
        self.state.current_task = 2
        self.save_workflow_state()

        # Yield progress event
        progress = WritingProgressEvent(
            task=2,
            task_name=self.TASK_NAMES[2],
            status=WritingTaskStatus.IN_PROGRESS,
            details="Creating document structure..."
        )
        yield self._format_sse(progress.model_dump())

        req = self.state.requirements
        if not req:
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = "No requirements found"
            self.save_workflow_state()
            return

        # Determine word target based on length guidance
        word_target = self._get_word_target(req.length_guidance)

        # Build topic section
        if req.topic and req.topic.strip():
            topic_section = f"Topic: {req.topic}"
        else:
            topic_section = "Topic: Based on the conversation context below, determine an appropriate and valuable topic"

        # Build conversation context section
        conversation_context_section = ""
        if hasattr(self, 'conversation_context') and self.conversation_context:
            conversation_context_section = f"""
<conversation_context>
{self.conversation_context}
</conversation_context>

Use this conversation history to understand the topic and write a relevant, contextual document.
"""

        # Build extracted points section
        extracted_points_section = ""
        if req.extracted_points and len(req.extracted_points) > 0:
            points_list = "\n".join(f"{i}: {point}" for i, point in enumerate(req.extracted_points))
            extracted_points_section = f"""
Available context points (from conversation):
{points_list}

Use the point numbers in 'assigned_points' field for each section.
"""

        # Build feedback section if any
        feedback_section = ""
        if feedback:
            feedback_section = f"""
Previous feedback to incorporate:
{feedback}

Please address this feedback in the revised outline.
"""

        # Build prompt
        prompt = OUTLINE_PROMPT.format(
            document_type=req.document_type,
            topic_section=topic_section,
            length_guidance=req.length_guidance,
            conversation_context_section=conversation_context_section,
            extracted_points_section=extracted_points_section,
            feedback_section=feedback_section,
            word_target=word_target
        )

        response = await self._invoke_llm(prompt)

        # Parse outline
        try:
            outline = self._parse_outline(response)
            self.state.outline = outline
            self.state.outline_attempts += 1
            self.save_workflow_state()

            # Yield completion
            progress = WritingProgressEvent(
                task=2,
                task_name=self.TASK_NAMES[2],
                status=WritingTaskStatus.COMPLETED,
                details=f"Created outline with {len(outline.sections)} sections"
            )
            yield self._format_sse(progress.model_dump())

        except Exception as e:
            logger.error(f"Failed to parse outline: {e}")
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = f"Failed to create outline: {e}"
            self.save_workflow_state()

            progress = WritingProgressEvent(
                task=2,
                task_name=self.TASK_NAMES[2],
                status=WritingTaskStatus.FAILED,
                details=str(e)
            )
            yield self._format_sse(progress.model_dump())

    async def _task_confirm(self) -> AsyncGenerator[str, None]:
        """Task 3: Request user confirmation on outline"""
        self.state.current_task = 3
        self.state.status = WritingWorkflowStatus.AWAITING_OUTLINE_CONFIRMATION
        self.save_workflow_state()

        # Yield progress event
        progress = WritingProgressEvent(
            task=3,
            task_name=self.TASK_NAMES[3],
            status=WritingTaskStatus.AWAITING_CONFIRMATION,
            details="Waiting for your approval..."
        )
        yield self._format_sse(progress.model_dump())

        # Yield outline event
        outline_event = WritingOutlineEvent(
            outline=self.state.outline,
            attempt=self.state.outline_attempts
        )
        yield self._format_sse(outline_event.model_dump())

        # Yield interrupt event (pauses workflow)
        interrupt_data = {
            "type": "interrupt",
            "interrupts": [{
                "id": f"outline-confirm-{self.state.workflow_id}",
                "name": "outline_confirmation",
                "reason": "Please review the document outline and confirm or request changes.",
                "data": {
                    "outline": self.state.outline.model_dump() if self.state.outline else None,
                    "attempt": self.state.outline_attempts,
                    "max_attempts": self.state.max_outline_attempts
                }
            }]
        }
        yield self._format_sse(interrupt_data)

    async def _task_body_write(self) -> AsyncGenerator[str, None]:
        """Task 4: Write each section content"""
        self.state.current_task = 4
        self.state.status = WritingWorkflowStatus.IN_PROGRESS
        self.save_workflow_state()

        outline = self.state.outline
        if not outline or not outline.sections:
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = "No outline sections found"
            self.save_workflow_state()
            return

        # Initialize body progress
        self.state.body_progress = BodyWriteProgress(
            total_sections=len(outline.sections),
            completed_sections=0,
            sections_content=[]
        )
        self.save_workflow_state()

        req = self.state.requirements
        previous_content = []

        # Iterate through sections
        for idx, section in enumerate(outline.sections):
            # Update progress
            self.state.body_progress.current_section_id = section.section_id
            self.save_workflow_state()

            # Yield progress event
            progress = WritingProgressEvent(
                task=4,
                task_name=self.TASK_NAMES[4],
                status=WritingTaskStatus.IN_PROGRESS,
                details=f"Writing section {idx + 1}/{len(outline.sections)}: {section.title}"
            )
            yield self._format_sse(progress.model_dump())

            # Build previous context (summaries of previous sections)
            previous_context = "None (this is the first section)"
            if previous_content:
                context_parts = []
                for prev in previous_content[-3:]:  # Last 3 sections for context
                    context_parts.append(f"- {prev['title']}: {prev['summary']}")
                previous_context = "\n".join(context_parts)

            # Build context points section for this section
            context_points_section = ""
            if hasattr(section, 'assigned_points') and section.assigned_points and req.extracted_points:
                points_for_section = []
                for point_idx in section.assigned_points:
                    if 0 <= point_idx < len(req.extracted_points):
                        points_for_section.append(req.extracted_points[point_idx])

                if points_for_section:
                    context_points_section = "Specific information to include in this section:\n" + "\n".join(f"- {point}" for point in points_for_section) + "\n"

            # Build prompt
            prompt = BODY_SECTION_PROMPT.format(
                document_type=req.document_type,
                topic=req.topic,
                document_title=outline.title,
                section_title=section.title,
                section_description=section.description,
                estimated_words=section.estimated_words or 200,
                context_points_section=context_points_section,
                previous_context=previous_context
            )

            # Invoke LLM
            response = await self._invoke_llm(prompt)

            # Store section content
            word_count = len(response.split())
            section_content = SectionContent(
                section_id=section.section_id,
                title=section.title,
                content=response,
                word_count=word_count,
                status=WritingTaskStatus.COMPLETED
            )
            self.state.body_progress.sections_content.append(section_content)
            self.state.body_progress.completed_sections = idx + 1

            # Add to previous content for context
            summary = response[:200] + "..." if len(response) > 200 else response
            previous_content.append({
                "title": section.title,
                "summary": summary
            })

            self.save_workflow_state()

        # Yield completion
        progress = WritingProgressEvent(
            task=4,
            task_name=self.TASK_NAMES[4],
            status=WritingTaskStatus.COMPLETED,
            details=f"Completed all {len(outline.sections)} sections"
        )
        yield self._format_sse(progress.model_dump())

    async def _task_intro_outro(self) -> AsyncGenerator[str, None]:
        """Task 5: Write introduction and conclusion"""
        self.state.current_task = 5
        self.save_workflow_state()

        # Yield progress event
        progress = WritingProgressEvent(
            task=5,
            task_name=self.TASK_NAMES[5],
            status=WritingTaskStatus.IN_PROGRESS,
            details="Writing introduction and conclusion..."
        )
        yield self._format_sse(progress.model_dump())

        req = self.state.requirements
        outline = self.state.outline
        body = self.state.body_progress

        if not outline or not body:
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = "Missing outline or body content"
            self.save_workflow_state()
            return

        # Build outline summary
        outline_summary = f"Title: {outline.title}\nSections: "
        outline_summary += ", ".join([s.title for s in outline.sections])

        # Build section summaries
        section_summaries = []
        for sc in body.sections_content:
            summary = sc.content[:300] + "..." if len(sc.content) > 300 else sc.content
            section_summaries.append(f"**{sc.title}**: {summary}")

        # Build prompt
        prompt = INTRO_OUTRO_PROMPT.format(
            document_title=outline.title,
            document_type=req.document_type,
            outline_summary=outline_summary,
            section_summaries="\n\n".join(section_summaries)
        )

        response = await self._invoke_llm(prompt)

        # Parse intro/outro
        try:
            intro_outro = self._parse_intro_outro(response)
            self.state.intro_outro = intro_outro
            self.save_workflow_state()

            # Yield completion
            progress = WritingProgressEvent(
                task=5,
                task_name=self.TASK_NAMES[5],
                status=WritingTaskStatus.COMPLETED,
                details="Introduction and conclusion ready"
            )
            yield self._format_sse(progress.model_dump())

        except Exception as e:
            logger.error(f"Failed to parse intro/outro: {e}")
            self.state.status = WritingWorkflowStatus.FAILED
            self.state.error_message = f"Failed to write intro/outro: {e}"
            self.save_workflow_state()

            progress = WritingProgressEvent(
                task=5,
                task_name=self.TASK_NAMES[5],
                status=WritingTaskStatus.FAILED,
                details=str(e)
            )
            yield self._format_sse(progress.model_dump())

    async def _task_review(self) -> AsyncGenerator[str, None]:
        """Task 6: Final review and polish"""
        self.state.current_task = 6
        self.save_workflow_state()

        # Yield progress event
        progress = WritingProgressEvent(
            task=6,
            task_name=self.TASK_NAMES[6],
            status=WritingTaskStatus.IN_PROGRESS,
            details="Reviewing and polishing document..."
        )
        yield self._format_sse(progress.model_dump())

        req = self.state.requirements
        outline = self.state.outline

        # Assemble full document
        full_document = self._assemble_document()

        # Build prompt
        prompt = REVIEW_PROMPT.format(
            document_title=outline.title,
            document_type=req.document_type,
            full_document=full_document
        )

        response = await self._invoke_llm(prompt)

        # Parse review result and apply edits
        try:
            review = self._parse_review_result(response)
            self.state.review_result = review

            # Apply edits to the assembled document
            final_document = self._apply_edits(full_document, review.edits)
            final_word_count = len(final_document.split())

            self.state.status = WritingWorkflowStatus.COMPLETED
            self.save_workflow_state()

            logger.info(f"[Review] Applied {review.edit_count} edits, final word count: {final_word_count}")

            # Store completed document for WorkflowAgent to save
            self.completed_document = {
                "title": outline.title,
                "content": final_document,
                "word_count": final_word_count,
                "sections_count": len(outline.sections),
                "document_type": req.document_type if req else "document"
            }
            logger.info(f"Document completed: {outline.title} ({final_word_count} words)")

            # Yield completion event
            complete_event = WritingCompleteEvent(
                document_title=outline.title,
                word_count=final_word_count,
                sections_count=len(outline.sections)
            )
            yield self._format_sse(complete_event.model_dump())

            # Yield final progress
            progress = WritingProgressEvent(
                task=6,
                task_name=self.TASK_NAMES[6],
                status=WritingTaskStatus.COMPLETED,
                details=f"Document complete: {final_word_count} words ({review.edit_count} edits applied)"
            )
            yield self._format_sse(progress.model_dump())

            # Yield document content as text event (for message history)
            yield self._format_sse({
                "type": "text",
                "content": f"\n\n**Document: {outline.title}**\n\n{final_document}"
            })

        except Exception as e:
            logger.error(f"Failed to parse review: {e}")
            # Don't fail the workflow - document is still usable
            self.state.status = WritingWorkflowStatus.COMPLETED
            self.save_workflow_state()

            # Yield complete document anyway in correct order
            intro_outro = self.state.intro_outro
            body = self.state.body_progress

            # Title
            yield self._format_sse({
                "type": "text",
                "content": f"\n\n# {outline.title}\n\n"
            })

            # Introduction
            if intro_outro and intro_outro.introduction:
                yield self._format_sse({
                    "type": "text",
                    "content": f"## Introduction\n\n{intro_outro.introduction}\n\n"
                })

            # Body sections
            if body and body.sections_content:
                for section in body.sections_content:
                    yield self._format_sse({
                        "type": "text",
                        "content": f"## {section.title}\n\n{section.content}\n\n"
                    })

            # Conclusion
            if intro_outro and intro_outro.conclusion:
                yield self._format_sse({
                    "type": "text",
                    "content": f"## Conclusion\n\n{intro_outro.conclusion}\n\n"
                })

            # Calculate word count for completion event
            full_doc = self._assemble_document()
            word_count = len(full_doc.split())

            # Store completed document for WorkflowAgent to save (fallback path)
            self.completed_document = {
                "title": outline.title,
                "content": full_doc,
                "word_count": word_count,
                "sections_count": len(outline.sections) if outline else 0,
                "document_type": req.document_type if req else "document"
            }
            logger.info(f"Document completed (fallback): {outline.title} ({word_count} words)")

            # Yield writing_complete event (for frontend artifact handling)
            complete_event = WritingCompleteEvent(
                document_title=outline.title,
                word_count=word_count,
                sections_count=len(outline.sections) if outline else 0
            )
            yield self._format_sse(complete_event.model_dump())

            # Yield completion progress
            progress = WritingProgressEvent(
                task=6,
                task_name=self.TASK_NAMES[6],
                status=WritingTaskStatus.COMPLETED,
                details=f"Document complete: {word_count} words (review parsing skipped)"
            )
            yield self._format_sse(progress.model_dump())

            # Yield document content as text event
            yield self._format_sse({
                "type": "text",
                "content": f"\n\n**Document: {outline.title}**\n\n{full_doc}"
            })

    # ============================================================
    # Helper Methods
    # ============================================================

    async def _invoke_llm(self, prompt: str) -> str:
        """Invoke LLM with prompt and return response text"""
        import sys
        import io

        # Log the full prompt for debugging
        logger.info("=" * 80)
        logger.info("LLM PROMPT (Full):")
        logger.info("=" * 80)
        logger.info(prompt)
        logger.info("=" * 80)

        # Routes to BedrockModel or a Mantle OpenAI provider based on model_id.
        model = build_model(
            self.model_id,
            temperature=self.temperature,
            max_tokens=8096,
        )

        # Create a simple agent for LLM invocation (stateless)
        agent = Agent(
            model=model,
            system_prompt="You are a professional document writer. Follow instructions precisely and return only the requested format.",
            tools=[]
        )

        # Suppress stdout during LLM invocation (Strands Agent prints by default)
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            result = agent(prompt)
        finally:
            sys.stdout = old_stdout

        # Extract text from result
        if hasattr(result, 'message') and hasattr(result.message, 'content'):
            content = result.message.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and 'text' in block:
                        return block['text']
            return str(content)

        return str(result)

    def _assemble_document(self) -> str:
        """Assemble complete document from parts"""
        parts = []

        outline = self.state.outline
        intro_outro = self.state.intro_outro
        body = self.state.body_progress

        if outline:
            parts.append(f"# {outline.title}\n")

        if intro_outro and intro_outro.introduction:
            parts.append(f"## Introduction\n\n{intro_outro.introduction}\n")

        if body and body.sections_content:
            for section in body.sections_content:
                parts.append(f"## {section.title}\n\n{section.content}\n")

        if intro_outro and intro_outro.conclusion:
            parts.append(f"## Conclusion\n\n{intro_outro.conclusion}\n")

        return "\n".join(parts)

    def _parse_requirements(self, response: str) -> WritingRequirements:
        """Parse requirements from LLM response"""
        data = self._extract_json(response)
        return WritingRequirements(**data)

    def _parse_outline(self, response: str) -> DocumentOutline:
        """Parse outline from LLM response"""
        data = self._extract_json(response)

        # Parse sections
        sections = []
        for s in data.get('sections', []):
            subsections = []
            # Handle None subsections
            subsection_data = s.get('subsections') or []
            for sub in subsection_data:
                subsections.append(OutlineSubsection(**sub))

            # Handle None assigned_points
            assigned_points = s.get('assigned_points') or []

            section = OutlineSection(
                section_id=s.get('section_id', ''),
                title=s.get('title', ''),
                description=s.get('description', ''),
                subsections=subsections,
                estimated_words=s.get('estimated_words', 0),
                assigned_points=assigned_points
            )
            sections.append(section)

        return DocumentOutline(
            title=data.get('title', 'Untitled Document'),
            sections=sections,
            total_estimated_words=data.get('total_estimated_words', 0),
            version=self.state.outline_attempts + 1
        )

    def _parse_intro_outro(self, response: str) -> IntroOutroContent:
        """Parse introduction and conclusion from LLM response"""
        data = self._extract_json(response)
        return IntroOutroContent(
            introduction=data.get('introduction', ''),
            conclusion=data.get('conclusion', '')
        )

    def _parse_review_result(self, response: str) -> ReviewResult:
        """Parse review result from LLM response"""
        from models.composer_schemas import ReviewEdit
        data = self._extract_json(response)
        edits = []
        for edit_data in data.get('edits', []):
            if isinstance(edit_data, dict) and 'old_text' in edit_data and 'new_text' in edit_data:
                edits.append(ReviewEdit(
                    old_text=edit_data['old_text'],
                    new_text=edit_data['new_text']
                ))
        return ReviewResult(
            edits=edits,
            edit_count=data.get('edit_count', len(edits))
        )

    def _apply_edits(self, document: str, edits: list) -> str:
        """Apply find-and-replace edits to document"""
        result = document
        applied_count = 0
        for edit in edits:
            if edit.old_text in result:
                result = result.replace(edit.old_text, edit.new_text, 1)
                applied_count += 1
                logger.debug(f"[Review] Applied edit: '{edit.old_text[:30]}...' -> '{edit.new_text[:30]}...'")
            else:
                logger.warning(f"[Review] Edit not found: '{edit.old_text[:50]}...'")
        logger.info(f"[Review] Applied {applied_count}/{len(edits)} edits")
        return result

    def _extract_json(self, text: str) -> dict:
        """Extract JSON from text response"""
        # Try direct parsing first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith('```'):
            # Remove opening fence (```json or ```)
            cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
            # Remove closing fence
            cleaned = re.sub(r'\n?```\s*$', '', cleaned)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

        # Find JSON object using bracket counting for nested structures
        start_idx = text.find('{')
        if start_idx != -1:
            depth = 0
            end_idx = start_idx
            in_string = False
            escape = False

            for i, char in enumerate(text[start_idx:], start_idx):
                if escape:
                    escape = False
                    continue
                if char == '\\' and in_string:
                    escape = True
                    continue
                if char == '"' and not escape:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        end_idx = i
                        break

            if depth == 0:
                try:
                    return json.loads(text[start_idx:end_idx + 1])
                except json.JSONDecodeError:
                    pass

        raise ValueError(f"Could not extract JSON from response: {text[:200]}...")

    def _format_sse(self, data: dict) -> str:
        """Format data as SSE event"""
        return f"data: {json.dumps(data)}\n\n"

    def _get_word_target(self, length_guidance: str) -> int:
        """Convert length guidance to word target"""
        guidance_lower = length_guidance.lower()

        if 'short' in guidance_lower:
            return 500
        elif 'long' in guidance_lower:
            return 2000
        elif 'medium' in guidance_lower:
            return 1000

        # Try to extract number
        numbers = re.findall(r'\d+', guidance_lower)
        if numbers:
            return int(numbers[0])

        return 1000  # Default to medium
