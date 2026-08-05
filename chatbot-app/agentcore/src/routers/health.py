"""Health check endpoints"""

from fastapi import APIRouter

from agent import async_tasks

router = APIRouter(tags=["health"])

@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "agent-core", "version": "2.0.0"}

@router.get("/ping")
async def ping():
    """Report liveness to AgentCore Runtime.

    The status is the platform's only signal for whether this session is idle:
    "Healthy" makes the microVM eligible for termination after
    idleRuntimeSessionTimeout, "HealthyBusy" keeps it alive while background work
    is in flight. Must stay non-blocking — a slow /ping reads as an unhealthy
    session, and blocking here would let a busy event loop kill the container.
    """
    return {"status": async_tasks.ping_status()}
