"""
Agent Core Service

Handles:
1. Strands Agent execution
2. Session management (agent pool)
3. Tool execution (MCP clients)
4. SSE streaming
"""

import os
import sys
from pathlib import Path

# Add src directory to Python path
src_path = Path(__file__).parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
import logging  # noqa: E402
import asyncio  # noqa: E402

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress known OpenTelemetry context detach error (Python 3.13 + async generators)
# Token created in one contextvars.Context cannot be reset in another — harmless noise
logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)

# Filter out /ping and /health from access logs
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return '/ping' not in msg and '/health' not in msg

logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

# Suppress verbose logging from various modules
logging.getLogger("strands.experimental.bidi").setLevel(logging.WARNING)

# Suppress MCP client detailed logs (tool listing, filtering)
logging.getLogger("agent.mcp.client").setLevel(logging.WARNING)

# Suppress Strands SDK internal logs (model calls, tool execution details)
logging.getLogger("strands.agent.agent").setLevel(logging.WARNING)
logging.getLogger("strands.tools.mcp").setLevel(logging.WARNING)

# Suppress noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("botocore.credentials").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)
logging.getLogger("bedrock_agentcore.memory").setLevel(logging.WARNING)
logging.getLogger("opentelemetry.instrumentation").setLevel(logging.WARNING)

# Lifespan event handler (replaces on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=== Agent Core Service Starting ===")
    logger.info("Agent execution engine initialized")

    # Create sessions directory for local development (FileSessionManager)
    # Workspace files are stored in S3, not local directories
    base_dir = Path(__file__).parent.parent
    sessions_dir = os.path.join(base_dir, "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    logger.info("Sessions directory ready")

    from agent.mailbox_runtime import clear_mailbox_runtime, register_mailbox_runtime
    from agent.research_jobs import register_delivery_handler, clear_delivery_handler
    from routers.chat import deliver_mailbox_event, deliver_research_job

    register_delivery_handler(asyncio.get_running_loop(), deliver_research_job)
    register_mailbox_runtime(
        asyncio.get_running_loop(),
        {"async_result.ready": deliver_mailbox_event},
    )

    yield  # Application is running

    # Shutdown
    clear_delivery_handler()
    clear_mailbox_runtime()
    logger.info("=== Agent Core Service Shutting Down ===")
    # TODO: Cleanup agent pool, MCP clients, etc.

# Create FastAPI app with lifespan
app = FastAPI(
    title="Strands Agent Chatbot - Agent Core",
    version="2.0.0",
    description="Agent execution and tool orchestration service",
    lifespan=lifespan
)

# Add CORS middleware for local development
# In production (AWS), CloudFront handles routing so CORS is not needed
if os.getenv('ENVIRONMENT', 'development') == 'development':
    logger.info("Adding CORS middleware for local development")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",  # Frontend dev server
            "http://localhost:3001",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Import routers
from routers import health, chat, gateway_tools, tools, skills, browser_live_view, voice  # noqa: E402

# Include routers
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(gateway_tools.router)
app.include_router(tools.router)
app.include_router(skills.router)
app.include_router(browser_live_view.router)
app.include_router(voice.router)  # Voice chat WebSocket

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )
