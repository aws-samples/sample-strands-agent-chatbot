"""
Research Agent A2A Server

Receives research topics and:
1. Performs web research using DuckDuckGo, Wikipedia, and URL fetching
2. Gathers comprehensive information
3. Generates structured markdown research report with citations
4. Returns markdown document with all sources cited

For local testing:
    python -m uvicorn main:app --port 9000 --reload
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI
from strands import Agent
from strands.multiagent.a2a import A2AServer
from strands.multiagent.a2a.executor import StrandsA2AExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.tasks import TaskUpdater, InMemoryTaskStore
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import Part, TextPart

import uvicorn

# Add src to path
src_path = Path(__file__).parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from tools import (  # noqa: E402
    ddg_web_search,
    fetch_url_content,
    wikipedia_search,
    wikipedia_get_article,
    write_markdown_section,
    read_markdown_file
)
from tools.generate_chart import generate_chart_tool  # noqa: E402
from model_factory import build_model  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class MetadataAwareExecutor(StrandsA2AExecutor):
    """
    Custom A2A Executor that extracts metadata (model_id, session_id, user_id)
    from RequestContext and passes to agent's invocation_state.
    """

    # Tool name to user-friendly status mapping
    TOOL_STATUS_MAP = {
        "ddg_web_search": "Searching web sources",
        "fetch_url_content": "Fetching article content",
        "wikipedia_search": "Searching Wikipedia",
        "wikipedia_get_article": "Reading Wikipedia article",
        "write_markdown_section": "Writing report section",
        "read_markdown_file": "Reading report",
        "generate_chart_tool": "Generating chart",
    }

    def __init__(self, agent_cache: dict):
        """
        Initialize with agent cache instead of single agent.

        Args:
            agent_cache: Dict mapping model_id to Agent instances
        """
        # Don't call super().__init__() since we'll override agent selection
        self.agent_cache = agent_cache
        self._current_tool_use_id = None  # Track current tool to avoid duplicate updates
        self._step_counter = 0  # Counter for step artifacts

    async def _handle_streaming_event(self, event: dict, updater: TaskUpdater) -> None:
        """
        Override to stream tool execution status in real-time.

        Handles these event types:
        - current_tool_use: When a tool starts executing (type: tool_use_stream)
        - tool_result: When a tool completes
        - data: Text content being generated (thinking)
        - result: Final agent result
        """
        event_type = event.get("type", "unknown")
        event_keys = list(event.keys())
        logger.debug(f"[MetadataAwareExecutor] Event - type: {event_type}, keys: {event_keys}")

        # Handle tool use start - stream status when tool begins
        # Check both direct key and type-based detection
        if event_type == "tool_use_stream":
            tool_use = event.get("current_tool_use", {})
            tool_use_id = tool_use.get("toolUseId")
            tool_name = tool_use.get("name", "")

            # Only send update if this is a new tool (avoid duplicates from streaming chunks)
            if tool_use_id and tool_use_id != self._current_tool_use_id:
                self._current_tool_use_id = tool_use_id
                self._step_counter += 1

                # Get user-friendly status message
                status_message = self.TOOL_STATUS_MAP.get(tool_name, f"Running {tool_name}")

                # Extract query/input for context
                tool_input = tool_use.get("input", {})
                context_info = ""
                if isinstance(tool_input, dict):
                    if "query" in tool_input:
                        context_info = f": {tool_input['query'][:80]}..."
                    elif "heading" in tool_input:
                        context_info = f": {tool_input['heading']}"
                    elif "url" in tool_input:
                        context_info = f": {tool_input['url'][:60]}..."

                step_text = f"🔍 {status_message}{context_info}"

                await updater.add_artifact(
                    parts=[Part(root=TextPart(text=step_text))],
                    name=f"research_step_{self._step_counter}"
                )
                logger.info(f"[MetadataAwareExecutor] Streamed step {self._step_counter}: {status_message}")

        # Handle tool result - could add completion status here if needed
        elif event.get("type") == "tool_result":
            # Tool completed - reset current tool tracking
            self._current_tool_use_id = None

        # Handle text data (thinking/response generation)
        elif "data" in event:
            # For now, we don't stream thinking text to avoid too much noise
            # But we could add a "Thinking..." status here if no tool is active
            pass

        # Handle final result
        elif "result" in event:
            await self._handle_agent_result(event["result"], updater)

    async def _execute_streaming(self, context: RequestContext, updater: TaskUpdater) -> None:
        """
        Override to inject metadata into invocation_state and use appropriate model.
        """
        # Reset step tracking for new request
        self._current_tool_use_id = None
        self._step_counter = 0

        # Extract metadata from RequestContext (need session_id early for file cleanup)
        # Try both params.metadata (MessageSendParams) and message.metadata (Message)
        # Streaming client may put metadata in Message.metadata
        metadata = context.metadata  # MessageSendParams.metadata
        if not metadata and context.message and hasattr(context.message, 'metadata'):
            metadata = context.message.metadata or {}  # Message.metadata

        model_id = metadata.get("model_id") if metadata else None
        session_id = metadata.get("session_id") if metadata else None
        user_id = metadata.get("user_id", "default_user") if metadata else "default_user"

        logger.info(f"[MetadataAwareExecutor] Extracted metadata - model_id: {model_id}, session_id: {session_id}, user_id: {user_id}")

        # Clear previous research file for this session (prevent cumulative results)
        if session_id:
            try:
                from report_manager import get_report_manager
                manager = get_report_manager(session_id, user_id)
                markdown_file = os.path.join(manager.workspace, "research_report.md")
                if os.path.exists(markdown_file):
                    os.remove(markdown_file)
                    logger.info(f"[MetadataAwareExecutor] Cleared previous research file: {markdown_file}")
            except Exception as e:
                logger.warning(f"[MetadataAwareExecutor] Failed to clear previous research file: {e}")

        # Get or create agent with specified model_id
        if model_id and model_id in self.agent_cache:
            agent = self.agent_cache[model_id]
            logger.info(f"[MetadataAwareExecutor] Using cached agent with model: {model_id}")
        elif model_id:
            # Create new agent with this model_id
            logger.info(f"[MetadataAwareExecutor] Creating new agent with model: {model_id}")
            agent = create_agent(model_id)
            self.agent_cache[model_id] = agent
        else:
            # Fallback to default agent
            default_model = MODEL_ID
            if default_model not in self.agent_cache:
                self.agent_cache[default_model] = create_agent(default_model)
            agent = self.agent_cache[default_model]
            logger.info(f"[MetadataAwareExecutor] Using default agent with model: {default_model}")

        # Temporarily set self.agent for parent class methods
        self.agent = agent

        # Convert A2A message parts to Strands ContentBlocks
        if context.message and hasattr(context.message, "parts"):
            content_blocks = self._convert_a2a_parts_to_content_blocks(context.message.parts)
            if not content_blocks:
                raise ValueError("No content blocks available")
        else:
            raise ValueError("No content blocks available")

        # Prepare invocation_state with metadata
        invocation_state = {
            "request_state": {
                "session_id": session_id,
                "user_id": user_id,
                "metadata": metadata
            }
        }

        logger.info(f"[MetadataAwareExecutor] Invoking agent with invocation_state: {invocation_state}")

        # Store session info for _handle_agent_result
        self._current_session_id = session_id
        self._current_user_id = user_id

        try:
            # Use agent.stream_async with invocation_state
            async for event in agent.stream_async(content_blocks, invocation_state=invocation_state):
                await self._handle_streaming_event(event, updater)
        except Exception as e:
            error_msg = str(e)
            # Check for Bedrock service errors
            if "serviceUnavailableException" in error_msg or "ServiceUnavailable" in error_msg:
                logger.error(f"Bedrock service unavailable: {error_msg}")
                # Add error artifact before failing
                await updater.add_artifact(
                    [Part(root=TextPart(text="Error: Bedrock service is temporarily unavailable. Please try again in a few moments."))],
                    name="error"
                )
            elif "ThrottlingException" in error_msg:
                logger.error(f"Bedrock throttling: {error_msg}")
                await updater.add_artifact(
                    [Part(root=TextPart(text="Error: Request was throttled. Please try again later."))],
                    name="error"
                )
            else:
                logger.exception("Error in streaming execution")
            raise

    async def _handle_agent_result(self, result, updater: TaskUpdater) -> None:
        """
        Override to add markdown content along with agent result before completing.
        """
        # Add agent's summary response first (if any)
        if final_content := str(result):
            await updater.add_artifact(
                [Part(root=TextPart(text=final_content))],
                name="agent_response",
            )

        # Read markdown file and add as main artifact
        session_id = getattr(self, '_current_session_id', None)
        user_id = getattr(self, '_current_user_id', 'default_user')

        if session_id:
            try:
                from report_manager import get_report_manager
                import os

                manager = get_report_manager(session_id, user_id)
                markdown_file = os.path.join(manager.workspace, "research_report.md")

                if os.path.exists(markdown_file):
                    with open(markdown_file, 'r', encoding='utf-8') as f:
                        markdown_content = f.read()
                    logger.info(f"[MetadataAwareExecutor] Read markdown file: {markdown_file} ({len(markdown_content)} chars)")

                    # Add markdown content wrapped in <research> tags
                    research_output = f"<research>\n{markdown_content}\n</research>"
                    await updater.add_artifact(
                        [Part(root=TextPart(text=research_output))],
                        name="research_markdown"
                    )
                    logger.info(f"[MetadataAwareExecutor] Added research_markdown artifact ({len(research_output)} chars)")
                else:
                    logger.warning(f"[MetadataAwareExecutor] Markdown file not found: {markdown_file}")
            except Exception as e:
                logger.error(f"[MetadataAwareExecutor] Error reading markdown file: {e}")
                import traceback
                logger.error(traceback.format_exc())

        # Complete task after adding all artifacts
        await updater.complete()


def get_current_date_pacific() -> str:
    """Get current date and hour in US Pacific timezone (America/Los_Angeles)"""
    try:
        # Check if timezone libraries are available
        try:
            # Try zoneinfo first (Python 3.9+)
            from zoneinfo import ZoneInfo
            pacific_tz = ZoneInfo("America/Los_Angeles")
            now = datetime.now(pacific_tz)
            tz_abbr = now.strftime("%Z")
        except (ImportError, NameError):
            # Fallback to pytz
            try:
                import pytz
                pacific_tz = pytz.timezone("America/Los_Angeles")
                now = datetime.now(pacific_tz)
                tz_abbr = now.strftime("%Z")
            except ImportError:
                # No timezone library available, use UTC
                now = datetime.utcnow()
                return now.strftime("%Y-%m-%d (%A) %H:00 UTC")

        return now.strftime(f"%Y-%m-%d (%A) %H:00 {tz_abbr}")
    except Exception as e:
        logger.warning(f"Failed to get Pacific time: {e}, using UTC")
        now = datetime.utcnow()
        return now.strftime("%Y-%m-%d (%A) %H:00 UTC")


# Configuration
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
PORT = int(os.getenv("PORT", "9000"))  # A2A protocol requires port 9000
PROJECT_NAME = os.getenv("PROJECT_NAME", "strands-agent-chatbot")
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")

logger.info("Configuration:")
logger.info(f"  Model ID: {MODEL_ID}")
logger.info(f"  AWS Region: {AWS_REGION}")
logger.info(f"  Port: {PORT}")
logger.info(f"  Project: {PROJECT_NAME}")
logger.info(f"  Environment: {ENVIRONMENT}")

# System prompt for Research Agent (A2A Server)
SYSTEM_PROMPT = """You are a Research Agent - conduct comprehensive web research and create structured research reports.

**Your Task:**
When given a research plan, execute it by gathering information and generating a structured report.

**Input Format:**
You will receive a research plan that includes:
- Research objectives and questions to answer
- Specific topics and subtopics to investigate
- Expected report structure
- Types of sources to prioritize

Follow the plan's guidance while applying your research expertise to gather comprehensive information.

1. **Research Phase**: Gather information from multiple sources
   - Use ddg_web_search() to find relevant web articles and information
   - Use wikipedia_search() and wikipedia_get_article() for encyclopedic knowledge
   - Use fetch_url_content() to read full articles when needed
   - Collect at least 3-5 diverse sources for comprehensive coverage

2. **Document Creation Phase**: Write sections directly to markdown file
   - Use write_markdown_section(heading, content, citations) to write each section
   - Sections are automatically appended to the markdown file
   - Include citations parameter to add section-level references
   - Build a well-organized report with Introduction, Key Findings, Details, and Conclusion

**Available Tools:**

**Research Tools:**
- ddg_web_search(query, max_results=5): Search the web with DuckDuckGo
- wikipedia_search(query): Find Wikipedia articles
- wikipedia_get_article(title, summary_only=False): Get full Wikipedia article content
- fetch_url_content(url): Extract full text content from any URL

**Markdown Writing Tools:**
- write_markdown_section(heading, content, citations=[]): Write a section to research_report.md with optional citations
- read_markdown_file(): Read current markdown content from research_report.md

**Chart Visualization Tool:**
- generate_chart_tool(chart_id, python_code, insert_at_line): Generate charts for quantitative data
  → Use when you find numerical data that would enhance comprehension:
    • Market statistics (growth rates, market share, revenue)
    • Time series data (historical trends, projections)
    • Comparative data (company performance, regional differences)
    • Statistical distributions (demographics, survey results)
  → Creates professional charts with matplotlib and uploads to S3

**Citation Format:**
When citing sources in your content, use inline markdown links:
"According to recent studies ([MIT Technology Review](https://technologyreview.com/article)), AI adoption..."

**Research Process:**

Step 1: Initial Research
→ Use ddg_web_search() with 2-3 different search queries to find diverse sources
→ Use wikipedia_search() to find authoritative background information
→ Note promising URLs and source names

Step 2: Deep Dive
→ Use fetch_url_content() on 2-3 most relevant URLs to read full articles
→ Use wikipedia_get_article() to get detailed encyclopedia content
→ Extract key facts, statistics, quotes, and insights

Step 3: Write Report Sections (No initialization needed)
→ write_markdown_section("# Research Report: [Your Topic]", "Brief summary...", "research_report.md")
→ write_markdown_section("## Introduction", "Overview paragraph with context...", "research_report.md")
→ write_markdown_section("## Background", "Historical context and foundational information...", "research_report.md")
→ write_markdown_section("## Key Findings", "Main discoveries from your research with citations...", "research_report.md")
→ write_markdown_section("## Analysis", "Your synthesis of the information...", "research_report.md")
→ write_markdown_section("## Conclusion", "Summary of insights...", "research_report.md")

Step 3.5: Generate Charts for Quantitative Data (if applicable)
→ If you found numerical/statistical data, create charts to visualize it:
→ Use read_markdown_file() to find where to insert the chart
→ generate_chart_tool("market_growth_2020_2024", python_code, insert_at_line)
→ Chart types: bar charts (comparisons), line charts (trends), pie charts (distributions)
→ Only create charts when they genuinely enhance understanding

Step 4: Include Citations with Each Section
→ Use the citations parameter in write_markdown_section() for EVERY section
→ Citations format: [{"title": "Source Name", "url": "https://full-url.com"}]
→ Citations will appear immediately after each section

**Example Flow:**
1. ddg_web_search("artificial intelligence market growth 2024")
2. fetch_url_content("https://relevant-article-url.com")
3. wikipedia_search("Artificial intelligence")
4. write_markdown_section("# AI Market Growth in 2024", "This report examines...")
5. write_markdown_section("## Introduction", "The AI market has grown ([Source](url))...", [{"title": "MIT Tech Review", "url": "https://..."}])
6. write_markdown_section("## Market Analysis", "Data shows market size...", [{"title": "Statista", "url": "https://..."}])
7. generate_chart_tool("ai_market_size_2020_2024", "import matplotlib.pyplot as plt\\nyears=[2020,2021,2022,2023,2024]\\nsize=[150,200,280,380,500]\\nplt.plot(years,size)...", 85)

**Rules:**
- Conduct thorough research with multiple sources before writing
- Always cite sources inline using ([Source Name](url)) format
- Include citations parameter with write_markdown_section() to add section-level citations
- Create well-structured documents with clear sections (NO separate References section needed)
- **When you find quantitative/statistical data, consider creating a chart to visualize it**
- Only generate charts for numerical data that genuinely enhances comprehension
- Be comprehensive but concise
- Execute research and writing automatically without asking permission
- Use consistent filename throughout the session (e.g., "research_report.md")
- **FINAL RESPONSE: After completing all sections, charts, and references:**
  1. Provide a brief summary (2-3 sentences max) of what was researched and created
  2. Example: "Research completed on [topic]. Generated comprehensive report with 5 sections covering [key areas]. Included 2 charts visualizing market trends and growth statistics."
  3. DO NOT call read_markdown_file() at the end - the system will automatically include the markdown file
  4. Keep your final response concise and professional"""


# A2A Skills Definition
AGENT_SKILLS = [
    {
        "id": "research_topic",
        "name": "Research Topic",
        "description": "Conduct comprehensive web research on any topic using DuckDuckGo, Wikipedia, and web scraping. Returns structured research findings with citations.",
        "inputModes": ["text/plain"],
        "outputModes": ["text/markdown", "application/json"],
        "tags": ["research", "web-search", "information-gathering"],
        "examples": [
            "Research the latest developments in quantum computing",
            "Find information about sustainable energy solutions",
            "Research AI safety regulations in the EU"
        ]
    },
    {
        "id": "generate_report",
        "name": "Generate Research Report",
        "description": "Generate a comprehensive markdown research report with Introduction, Analysis, Key Findings, and References. All sources are properly cited.",
        "inputModes": ["text/plain"],
        "outputModes": ["text/markdown"],
        "tags": ["report", "documentation", "writing"],
        "examples": [
            "Generate a research report on climate change impacts",
            "Create a market analysis report for electric vehicles",
            "Write a technical report on blockchain technology"
        ]
    }
]


def create_agent(model_id: Optional[str] = None) -> Agent:
    """
    Create the Research Agent with research and document tools.

    Args:
        model_id: Model ID to use (defaults to MODEL_ID from env)
    """
    # Use provided model_id or fall back to environment variable
    effective_model_id = model_id or MODEL_ID
    model = build_model(effective_model_id, app_region=AWS_REGION)

    logger.info(f"Creating agent with model: {effective_model_id}")

    # Add current date to system prompt
    current_date = get_current_date_pacific()
    system_prompt_with_date = f"{SYSTEM_PROMPT}\n\nCurrent date: {current_date}"
    logger.info(f"Using system prompt with current date: {current_date}")

    agent = Agent(
        name="Research Agent",
        description=(
            "Research Agent (A2A Server) - An autonomous research specialist that "
            "conducts comprehensive web research using DuckDuckGo, Wikipedia, and "
            "URL fetching. Generates well-structured markdown reports with proper "
            "citations and references. Can generate professional charts using Python/matplotlib."
        ),
        system_prompt=system_prompt_with_date,
        model=model,
        tools=[
            # Research tools
            ddg_web_search,
            fetch_url_content,
            wikipedia_search,
            wikipedia_get_article,
            # Markdown writing tools
            write_markdown_section,
            read_markdown_file,
            # Chart generation
            generate_chart_tool
        ]
    )

    logger.info(f"Agent created: {agent.name} with model {effective_model_id}")
    return agent


def create_app() -> FastAPI:
    """Create FastAPI application with A2A server."""

    # Runtime URL
    runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL', f'http://127.0.0.1:{PORT}/')

    # Create FastAPI app first
    app = FastAPI(
        title="Research Agent A2A Server",
        description=(
            "Research Agent (A2A Server) - Autonomous research specialist that conducts "
            "web research and generates comprehensive markdown reports with citations. "
            "Skills: research_topic, generate_report."
        ),
        version="1.0.0"
    )

    # Agent cache for reusing agents with the same model_id
    agent_cache = {}

    def get_or_create_agent(model_id: Optional[str] = None) -> Agent:
        """Get cached agent or create new one with specified model_id"""
        effective_model_id = model_id or MODEL_ID

        if effective_model_id not in agent_cache:
            logger.info(f"Creating new agent instance with model: {effective_model_id}")
            agent_cache[effective_model_id] = create_agent(effective_model_id)
        else:
            logger.info(f"Reusing cached agent with model: {effective_model_id}")

        return agent_cache[effective_model_id]

    # Create default agent for A2A Server initialization (required but will be overridden by MetadataAwareExecutor)
    default_agent = get_or_create_agent()

    # Create Custom Executor with agent cache
    custom_executor = MetadataAwareExecutor(agent_cache=agent_cache)

    # Create Custom Request Handler with our executor
    task_store = InMemoryTaskStore()
    custom_request_handler = DefaultRequestHandler(
        agent_executor=custom_executor,
        task_store=task_store
    )

    # Create A2A server with custom request handler
    # Note: We still need to pass an agent to A2AServer for AgentCard generation
    a2a_server = A2AServer(
        agent=default_agent,  # Used only for AgentCard metadata
        http_url=runtime_url,
        serve_at_root=True,
        host="0.0.0.0",
        port=PORT,
        version="1.0.0",
        skills=AGENT_SKILLS,
        task_store=task_store  # Share the same task store
    )

    # Override the request_handler with our custom one
    a2a_server.request_handler = custom_request_handler

    logger.info(f"A2A Server configured with MetadataAwareExecutor at {runtime_url}")

    @app.get("/ping")
    def ping():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "agent": "Research Agent",
            "version": "1.0.0",
            "skills": ["research_topic", "generate_report"]
        }

    @app.post("/research")
    async def research_topic(request: dict):
        """
        Direct endpoint for local testing (non-A2A).

        Request body:
        {
            "topic": "Research topic or question",
            "session_id": "optional-session-id"
        }
        """
        topic = request.get("topic", "")
        session_id = request.get("session_id", str(uuid.uuid4()))

        if not topic:
            return {"error": "topic is required"}

        try:
            logger.info(f"Starting research on topic: {topic} (session: {session_id})")

            # Get default agent
            agent = get_or_create_agent()

            # Run agent with invocation_state to pass session_id to tools
            result = await agent.invoke_async(
                f"Research this topic and create a comprehensive report: {topic}",
                invocation_state={"request_state": {"session_id": session_id}}
            )

            # Read the generated markdown document
            from report_manager import get_report_manager
            import os
            manager = get_report_manager(session_id)

            # Read markdown file from workspace
            markdown_file = os.path.join(manager.workspace, "research_report.md")
            markdown_content = ""
            if os.path.exists(markdown_file):
                with open(markdown_file, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
            else:
                markdown_content = "No markdown file generated"

            return {
                "status": "success",
                "session_id": session_id,
                "topic": topic,
                "markdown": markdown_content,
                "markdown_file": markdown_file,
                "agent_response": result.output if hasattr(result, 'output') else str(result)
            }

        except Exception as e:
            logger.error(f"Error in research_topic: {e}")
            return {"error": str(e)}

    # Mount A2A server with MetadataAwareExecutor
    # This handles ALL A2A protocol endpoints including /, /ping, /.well-known/agent-card.json
    app.mount("/", a2a_server.to_fastapi_app())

    logger.info("A2A server with MetadataAwareExecutor mounted successfully")

    return app


# Create app instance
app = create_app()

if __name__ == "__main__":
    logger.info(f"Starting Research Agent on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
