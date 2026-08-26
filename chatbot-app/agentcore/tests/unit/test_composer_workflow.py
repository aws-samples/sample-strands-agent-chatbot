"""Tests for Composer Workflow"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from models.composer_schemas import (
    WritingTaskStatus,
    WritingWorkflowStatus,
    WritingRequirements,
    OutlineSection,
    DocumentOutline,
    OutlineConfirmation,
    WritingWorkflowState,
)
from workflows.composer_workflow import ComposerWorkflow


# ============================================================
# Mock LLM Responses
# ============================================================

MOCK_REQUIREMENTS_RESPONSE = json.dumps({
    "document_type": "report",
    "topic": "Cloud Computing Benefits",
    "length_guidance": "medium",
    "extracted_points": ["Cost reduction", "Scalability", "Security"]
})

MOCK_OUTLINE_RESPONSE = json.dumps({
    "title": "The Business Case for Cloud Computing",
    "sections": [
        {
            "section_id": "s1",
            "title": "Executive Summary",
            "description": "Overview of cloud computing benefits",
            "subsections": [],
            "estimated_words": 200
        },
        {
            "section_id": "s2",
            "title": "Cost Analysis",
            "description": "Detailed cost breakdown",
            "subsections": [],
            "estimated_words": 400
        },
        {
            "section_id": "s3",
            "title": "Case Studies",
            "description": "Real-world examples",
            "subsections": [],
            "estimated_words": 400
        }
    ],
    "total_estimated_words": 1000
})

MOCK_SECTION_CONTENT = """Cloud computing has revolutionized the way businesses operate.
By leveraging cloud infrastructure, organizations can significantly reduce their IT costs
while improving scalability and reliability. This section explores the key benefits
and considerations for adopting cloud technologies."""

MOCK_INTRO_OUTRO_RESPONSE = json.dumps({
    "introduction": "In today's rapidly evolving digital landscape, cloud computing has emerged as a cornerstone of modern business strategy. This report examines the compelling business case for cloud adoption.",
    "conclusion": "As demonstrated throughout this report, cloud computing offers significant advantages for businesses of all sizes. The evidence clearly supports the strategic value of cloud adoption for forward-thinking organizations."
})

MOCK_REVIEW_RESPONSE = json.dumps({
    "edits": [
        {"old_text": "teh", "new_text": "the"},
        {"old_text": "recieve", "new_text": "receive"}
    ],
    "edit_count": 2
})


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def writing_agent():
    """Create a ComposerWorkflow instance for testing"""
    return ComposerWorkflow(
        session_id="test-session-123",
        user_id="test-user-456",
        model_id="us.anthropic.claude-sonnet-5",
        temperature=0.7
    )


@pytest.fixture
def mock_dynamodb():
    """Mock DynamoDB table operations"""
    with patch.object(ComposerWorkflow, '_get_dynamodb_table') as mock:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_table.update_item.return_value = {}
        mock.return_value = mock_table
        yield mock_table


# ============================================================
# Initialization Tests
# ============================================================

class TestComposerWorkflowInit:
    """Test ComposerWorkflow initialization"""

    def test_init_with_defaults(self):
        """Test agent initialization with default values"""
        agent = ComposerWorkflow(session_id="sess-123")
        assert agent.session_id == "sess-123"
        assert agent.user_id == "sess-123"  # defaults to session_id
        assert agent.model_id == "us.openai.gpt-5.6-terra"
        assert agent.temperature == 0.7

    def test_init_with_custom_values(self):
        """Test agent initialization with custom values"""
        agent = ComposerWorkflow(
            session_id="sess-123",
            user_id="user-456",
            model_id="custom-model",
            temperature=0.5
        )
        assert agent.session_id == "sess-123"
        assert agent.user_id == "user-456"
        assert agent.model_id == "custom-model"
        assert agent.temperature == 0.5


# ============================================================
# State Persistence Tests
# ============================================================

class TestStatePersistence:
    """Test DynamoDB state persistence"""

    def test_load_empty_state(self, writing_agent, mock_dynamodb):
        """Test loading when no state exists"""
        mock_dynamodb.get_item.return_value = {}
        state = writing_agent.load_workflow_state()
        assert state.status == WritingWorkflowStatus.NOT_STARTED

    def test_load_existing_state(self, writing_agent, mock_dynamodb):
        """Test loading existing state"""
        mock_dynamodb.get_item.return_value = {
            'Item': {
                'writingWorkflow': {
                    'workflow_id': 'wf-123',
                    'status': 'in_progress',
                    'current_task': 2,
                    'user_request': 'Write a report',
                    'outline_attempts': 1
                }
            }
        }
        state = writing_agent.load_workflow_state()
        assert state.status == WritingWorkflowStatus.IN_PROGRESS
        assert state.current_task == 2
        assert state.outline_attempts == 1

    def test_save_state(self, writing_agent, mock_dynamodb):
        """Test saving state to DynamoDB"""
        writing_agent.state = WritingWorkflowState(
            status=WritingWorkflowStatus.IN_PROGRESS,
            current_task=3
        )
        writing_agent.save_workflow_state()

        mock_dynamodb.update_item.assert_called_once()
        call_args = mock_dynamodb.update_item.call_args
        assert 'writingWorkflow' in call_args[1]['UpdateExpression']


# ============================================================
# Helper Method Tests
# ============================================================

class TestHelperMethods:
    """Test helper methods"""

    def test_format_sse(self, writing_agent):
        """Test SSE formatting"""
        data = {"type": "test", "message": "hello"}
        result = writing_agent._format_sse(data)
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        assert '"type": "test"' in result

    def test_extract_json_direct(self, writing_agent):
        """Test JSON extraction from direct JSON"""
        json_str = '{"key": "value"}'
        result = writing_agent._extract_json(json_str)
        assert result == {"key": "value"}

    def test_extract_json_markdown(self, writing_agent):
        """Test JSON extraction from markdown code block"""
        text = """Here's the result:
```json
{"key": "value"}
```
"""
        result = writing_agent._extract_json(text)
        assert result == {"key": "value"}

    def test_extract_json_embedded(self, writing_agent):
        """Test JSON extraction from embedded JSON"""
        text = 'Some text before {"key": "value"} some text after'
        result = writing_agent._extract_json(text)
        assert result == {"key": "value"}

    def test_extract_json_failure(self, writing_agent):
        """Test JSON extraction failure"""
        with pytest.raises(ValueError):
            writing_agent._extract_json("no json here")

    def test_get_word_target_short(self, writing_agent):
        """Test word target for short documents"""
        assert writing_agent._get_word_target("short") == 500
        assert writing_agent._get_word_target("Short document") == 500

    def test_get_word_target_medium(self, writing_agent):
        """Test word target for medium documents"""
        assert writing_agent._get_word_target("medium") == 1000
        assert writing_agent._get_word_target("Medium length") == 1000

    def test_get_word_target_long(self, writing_agent):
        """Test word target for long documents"""
        assert writing_agent._get_word_target("long") == 2000
        assert writing_agent._get_word_target("Long detailed") == 2000

    def test_get_word_target_specific(self, writing_agent):
        """Test word target with specific number"""
        assert writing_agent._get_word_target("about 1500 words") == 1500

    def test_get_word_target_default(self, writing_agent):
        """Test word target default"""
        assert writing_agent._get_word_target("any length") == 1000


class TestParsingMethods:
    """Test parsing helper methods"""

    def test_parse_requirements(self, writing_agent):
        """Test requirements parsing"""
        req = writing_agent._parse_requirements(MOCK_REQUIREMENTS_RESPONSE)
        assert req.document_type == "report"
        assert req.topic == "Cloud Computing Benefits"
        assert len(req.extracted_points) == 3

    def test_parse_outline(self, writing_agent):
        """Test outline parsing"""
        # Initialize state for version tracking
        writing_agent.state = WritingWorkflowState()
        outline = writing_agent._parse_outline(MOCK_OUTLINE_RESPONSE)
        assert outline.title == "The Business Case for Cloud Computing"
        assert len(outline.sections) == 3
        assert outline.sections[0].title == "Executive Summary"

    def test_parse_intro_outro(self, writing_agent):
        """Test intro/outro parsing"""
        result = writing_agent._parse_intro_outro(MOCK_INTRO_OUTRO_RESPONSE)
        assert "cloud computing" in result.introduction.lower()
        assert "conclusion" not in result.introduction.lower()
        assert len(result.conclusion) > 0

    def test_parse_review_result(self, writing_agent):
        """Test review result parsing"""
        result = writing_agent._parse_review_result(MOCK_REVIEW_RESPONSE)
        assert result.edit_count == 2
        assert len(result.edits) == 2


# ============================================================
# Document Assembly Tests
# ============================================================

class TestDocumentAssembly:
    """Test document assembly"""

    def test_assemble_empty_document(self, writing_agent):
        """Test assembly with no content"""
        writing_agent.state = WritingWorkflowState()
        result = writing_agent._assemble_document()
        assert result == ""

    def test_assemble_with_outline_only(self, writing_agent):
        """Test assembly with only outline"""
        writing_agent.state = WritingWorkflowState(
            outline=DocumentOutline(
                title="Test Document",
                sections=[]
            )
        )
        result = writing_agent._assemble_document()
        assert "# Test Document" in result

    def test_assemble_full_document(self, writing_agent):
        """Test assembly with all parts"""
        from models.composer_schemas import (
            IntroOutroContent,
            BodyWriteProgress,
            SectionContent
        )

        writing_agent.state = WritingWorkflowState(
            outline=DocumentOutline(
                title="Complete Document",
                sections=[]
            ),
            intro_outro=IntroOutroContent(
                introduction="This is the intro.",
                conclusion="This is the conclusion."
            ),
            body_progress=BodyWriteProgress(
                sections_content=[
                    SectionContent(
                        section_id="s1",
                        title="Section One",
                        content="Content of section one."
                    )
                ]
            )
        )

        result = writing_agent._assemble_document()
        assert "# Complete Document" in result
        assert "## Introduction" in result
        assert "This is the intro." in result
        assert "## Section One" in result
        assert "## Conclusion" in result


# ============================================================
# Workflow Tests with Mocked LLM
# ============================================================

class TestWorkflowExecution:
    """Test workflow execution with mocked LLM"""

    @pytest.mark.asyncio
    async def test_task_intake(self, writing_agent, mock_dynamodb):
        """Test requirements intake task"""
        with patch.object(writing_agent, '_invoke_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = MOCK_REQUIREMENTS_RESPONSE

            writing_agent.state = WritingWorkflowState(
                status=WritingWorkflowStatus.IN_PROGRESS
            )

            events = []
            async for event in writing_agent._task_intake("Write a report about cloud computing"):
                events.append(event)

            # Verify LLM was called
            mock_llm.assert_called_once()

            # Verify events were emitted
            assert len(events) >= 2  # progress start + completion
            assert any('"task": 1' in e for e in events)
            assert any('"completed"' in e for e in events)

            # Verify requirements were set
            assert writing_agent.state.requirements is not None
            assert writing_agent.state.requirements.document_type == "report"

    @pytest.mark.asyncio
    async def test_task_outline(self, writing_agent, mock_dynamodb):
        """Test outline generation task"""
        with patch.object(writing_agent, '_invoke_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = MOCK_OUTLINE_RESPONSE

            writing_agent.state = WritingWorkflowState(
                status=WritingWorkflowStatus.IN_PROGRESS,
                requirements=WritingRequirements(
                    document_type="report",
                    topic="Cloud Computing"
                )
            )

            events = []
            async for event in writing_agent._task_outline():
                events.append(event)

            # Verify outline was generated
            assert writing_agent.state.outline is not None
            assert writing_agent.state.outline.title == "The Business Case for Cloud Computing"

    @pytest.mark.asyncio
    async def test_task_confirm_emits_interrupt(self, writing_agent, mock_dynamodb):
        """Test confirmation task emits interrupt event"""
        writing_agent.state = WritingWorkflowState(
            status=WritingWorkflowStatus.IN_PROGRESS,
            outline=DocumentOutline(
                title="Test",
                sections=[OutlineSection(title="S1", description="D1")]
            )
        )

        events = []
        async for event in writing_agent._task_confirm():
            events.append(event)

        # Verify interrupt event was emitted
        assert any('"interrupt"' in e for e in events)
        assert any('"outline_confirmation"' in e for e in events)

        # Verify status changed
        assert writing_agent.state.status == WritingWorkflowStatus.AWAITING_OUTLINE_CONFIRMATION

    @pytest.mark.asyncio
    async def test_handle_outline_approval(self, writing_agent, mock_dynamodb):
        """Test handling outline approval"""
        with patch.object(writing_agent, '_invoke_llm', new_callable=AsyncMock) as mock_llm:
            # Set up mock responses for body, intro/outro, and review
            mock_llm.side_effect = [
                MOCK_SECTION_CONTENT,  # Section 1
                MOCK_INTRO_OUTRO_RESPONSE,  # Intro/outro
                MOCK_REVIEW_RESPONSE  # Review
            ]

            writing_agent.state = WritingWorkflowState(
                status=WritingWorkflowStatus.AWAITING_OUTLINE_CONFIRMATION,
                current_task=3,
                requirements=WritingRequirements(
                    document_type="report",
                    topic="Cloud"
                ),
                outline=DocumentOutline(
                    title="Test",
                    sections=[OutlineSection(
                        section_id="s1",
                        title="Section 1",
                        description="Desc",
                        estimated_words=100
                    )]
                )
            )

            confirmation = OutlineConfirmation(approved=True)

            events = []
            async for event in writing_agent._handle_outline_confirmation(confirmation):
                events.append(event)

            # Verify workflow completed
            assert writing_agent.state.status == WritingWorkflowStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_handle_outline_rejection(self, writing_agent, mock_dynamodb):
        """Test handling outline rejection"""
        with patch.object(writing_agent, '_invoke_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = MOCK_OUTLINE_RESPONSE

            writing_agent.state = WritingWorkflowState(
                status=WritingWorkflowStatus.AWAITING_OUTLINE_CONFIRMATION,
                current_task=3,
                outline_attempts=1,
                requirements=WritingRequirements(
                    document_type="report",
                    topic="Cloud"
                ),
                outline=DocumentOutline(
                    title="Test",
                    sections=[]
                )
            )

            confirmation = OutlineConfirmation(
                approved=False,
                feedback="Add more sections"
            )

            events = []
            async for event in writing_agent._handle_outline_confirmation(confirmation):
                events.append(event)

            # Verify feedback was stored
            assert "Add more sections" in writing_agent.state.outline_feedback

            # Verify another interrupt was emitted (for new outline)
            assert any('"interrupt"' in e for e in events)

    @pytest.mark.asyncio
    async def test_max_outline_attempts(self, writing_agent, mock_dynamodb):
        """Test workflow continues after max outline attempts"""
        with patch.object(writing_agent, '_invoke_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                MOCK_SECTION_CONTENT,  # Section content
                MOCK_INTRO_OUTRO_RESPONSE,  # Intro/outro
                MOCK_REVIEW_RESPONSE  # Review
            ]

            writing_agent.state = WritingWorkflowState(
                status=WritingWorkflowStatus.AWAITING_OUTLINE_CONFIRMATION,
                current_task=3,
                outline_attempts=3,  # Already at max
                max_outline_attempts=3,
                requirements=WritingRequirements(
                    document_type="report",
                    topic="Cloud"
                ),
                outline=DocumentOutline(
                    title="Test",
                    sections=[OutlineSection(
                        section_id="s1",
                        title="Section 1",
                        description="Desc",
                        estimated_words=100
                    )]
                )
            )

            confirmation = OutlineConfirmation(
                approved=False,
                feedback="Still not right"
            )

            events = []
            async for event in writing_agent._handle_outline_confirmation(confirmation):
                events.append(event)

            # Verify max attempts message
            assert any("Maximum revision attempts reached" in e for e in events)

            # Verify workflow continued and completed
            assert writing_agent.state.status == WritingWorkflowStatus.COMPLETED


# ============================================================
# Integration-style Tests
# ============================================================

class TestWorkflowIntegration:
    """Integration-style tests for complete workflow"""

    @pytest.mark.asyncio
    async def test_full_workflow_start(self, writing_agent, mock_dynamodb):
        """Test starting a full workflow"""
        with patch.object(writing_agent, '_invoke_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                MOCK_REQUIREMENTS_RESPONSE,  # Intake
                MOCK_OUTLINE_RESPONSE  # Outline
            ]

            events = []
            async for event in writing_agent.run_workflow(
                user_request="Write a report about cloud computing benefits"
            ):
                events.append(event)

            # Verify start and end events
            assert any('"start"' in e for e in events)
            assert any('"end"' in e for e in events)

            # Verify interrupt for confirmation
            assert any('"interrupt"' in e for e in events)

            # Verify state
            assert writing_agent.state.status == WritingWorkflowStatus.AWAITING_OUTLINE_CONFIRMATION
            assert writing_agent.state.requirements is not None
            assert writing_agent.state.outline is not None

    @pytest.mark.asyncio
    async def test_workflow_error_handling(self, writing_agent, mock_dynamodb):
        """Test workflow error handling"""
        with patch.object(writing_agent, '_invoke_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("LLM Error")

            events = []
            async for event in writing_agent.run_workflow(
                user_request="Write a report"
            ):
                events.append(event)

            # Verify error was handled
            assert any('"error"' in e for e in events)
            assert any('"end"' in e for e in events)

    @pytest.mark.asyncio
    async def test_workflow_no_request(self, writing_agent, mock_dynamodb):
        """Test workflow with no request"""
        events = []
        async for event in writing_agent.run_workflow():
            events.append(event)

        assert any("No user request provided" in e for e in events)
