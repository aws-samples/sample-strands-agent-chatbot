"""
Voice Chat WebSocket Router

Handles real-time bidirectional audio streaming for voice chat
using Nova Sonic speech-to-speech model via BidiAgent.

Architecture:
- Local mode: Direct WebSocket to /voice/stream
- Cloud mode: AgentCore Runtime routes WebSocket to /ws on container
  - URL format: wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/ws
  - BFF generates JWT-authenticated URL for browser WebSocket connection
"""

import asyncio
import logging
import uuid
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

logger = logging.getLogger(__name__)

# Lazy import to avoid pyaudio dependency at module load time
VoiceAgent = None

def _get_voice_agent_class():
    global VoiceAgent
    if VoiceAgent is None:
        from agent.voice_agent import VoiceAgent as _VoiceAgent
        VoiceAgent = _VoiceAgent
    return VoiceAgent

router = APIRouter()

# Active voice sessions (session_id -> VoiceAgent)
_active_sessions: dict = {}


def _get_param_from_request(websocket: WebSocket, header_suffix: str, query_param: Optional[str]) -> Optional[str]:
    """Extract param from custom header (cloud) or query param (local)."""
    # Cloud mode: AgentCore Runtime converts X-Amzn-Bedrock-AgentCore-Runtime-Custom-* to headers
    header_name = f"x-amzn-bedrock-agentcore-runtime-custom-{header_suffix}"
    custom_header = websocket.headers.get(header_name)
    if custom_header:
        return custom_header
    return query_param


def _get_bearer_token(websocket: WebSocket) -> Optional[str]:
    authorization = websocket.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _get_session_id_from_request(
    websocket: WebSocket,
    query_param: Optional[str],
) -> Optional[str]:
    return (
        websocket.headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
        or _get_param_from_request(websocket, "session-id", query_param)
    )


@router.websocket("/voice/stream")
async def voice_stream(
    websocket: WebSocket,
    session_id: Optional[str] = Query(None, description="Session ID (from BFF)"),
    user_id: Optional[str] = Query(None, description="User ID (from BFF)"),
    auth_token: Optional[str] = Query(None, description="Cognito JWT for MCP Runtime 3LO"),
):
    """WebSocket endpoint for real-time voice chat.

    Tool set is hardcoded (see factory.create_agent "voice" branch).
    """
    await websocket.accept()

    session_id = _get_session_id_from_request(websocket, session_id)
    user_id = _get_param_from_request(websocket, "user-id", user_id)
    auth_token = _get_bearer_token(websocket) or auth_token

    # The application config frame carries dynamic settings. In cloud mode,
    # authentication comes from the AgentCore WebSocket handshake.
    try:
        first_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        if first_msg.get("type") == "config":
            session_id = first_msg.get("session_id") or session_id
            user_id = first_msg.get("user_id") or user_id
            auth_token = first_msg.get("auth_token") or auth_token
            logger.info("[Voice] Config received from client message")
    except Exception as e:
        logger.warning(f"[Voice] Config message error: {e}")

    if not session_id:
        session_id = str(uuid.uuid4())
        logger.info(f"[Voice] Generated new session ID: {session_id}")

    logger.info(
        f"[Voice] WebSocket connected: session={session_id}, user={user_id}, "
        f"auth_token={'present' if auth_token else 'missing'}"
    )

    voice_agent = None

    try:
        VoiceAgentClass = _get_voice_agent_class()
        voice_agent = VoiceAgentClass(
            session_id=session_id,
            user_id=user_id,
            enabled_tools=[
                "gateway_ddg_web_search",
                "gateway_fetch_url_content",
            ],
            auth_token=auth_token,
        )

        # Store in active sessions
        _active_sessions[session_id] = voice_agent

        # Send connection established event FIRST (before starting agent)
        # Note: AgentCore Runtime WebSocket proxy may drop this message,
        # but client handles it via first-message detection workaround
        await websocket.send_json({
            "type": "bidi_connection_start",
            "connection_id": session_id,
            "status": "connected",
        })

        # Now start the voice agent (connects to Nova Sonic)
        await voice_agent.start()
        logger.info(f"[Voice] Voice agent started: session={session_id}")

        # Create tasks for bidirectional communication
        receive_task = asyncio.create_task(
            _receive_from_client(websocket, voice_agent, session_id)
        )
        send_task = asyncio.create_task(
            _send_to_client(websocket, voice_agent, session_id)
        )

        # Wait for either task to complete (one will complete when connection closes)
        done, pending = await asyncio.wait(
            [receive_task, send_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel pending tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Check for exceptions in completed tasks
        for task in done:
            if task.exception():
                logger.error(f"[Voice] Task error: {task.exception()}")

    except WebSocketDisconnect:
        logger.info(f"[Voice] WebSocket disconnected: session={session_id}")

    except Exception as e:
        logger.error(f"[Voice] Error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "bidi_error",
                "message": str(e),
            })
        except Exception as send_err:
            logger.debug(f"[Voice] Failed to send error to client: {send_err}")

    finally:
        # Cleanup
        if session_id in _active_sessions:
            del _active_sessions[session_id]

        if voice_agent:
            try:
                await voice_agent.stop()
            except Exception as e:
                logger.error(f"[Voice] Error stopping agent: {e}")

        try:
            await websocket.close()
        except Exception as close_err:
            logger.debug(f"[Voice] Failed to close websocket: {close_err}")

        logger.info(f"[Voice] Session cleaned up: {session_id}")


async def _receive_from_client(
    websocket: WebSocket,
    voice_agent,  # VoiceAgent (lazy imported, no type hint to avoid import at module level)
    session_id: str,
) -> None:
    """Receive messages from WebSocket client and forward to agent"""
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "bidi_audio_input":
                # Forward audio to agent
                audio = data.get("audio")
                sample_rate = data.get("sample_rate", 16000)
                if audio:
                    await voice_agent.send_audio(audio, sample_rate)

            elif msg_type == "bidi_text_input":
                # Forward text to agent
                text = data.get("text")
                if text:
                    await voice_agent.send_text(text)

            elif msg_type == "ping":
                # Respond to ping
                await websocket.send_json({"type": "pong"})

            elif msg_type == "stop":
                # Client requested stop
                logger.info(f"[Voice] Client requested stop: session={session_id}")
                break

            else:
                logger.warning(f"[Voice] Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        logger.info(f"[Voice] Client disconnected: session={session_id}")
        raise

    except asyncio.CancelledError:
        logger.debug(f"[Voice] Receive task cancelled: session={session_id}")
        raise

    except Exception as e:
        logger.error(f"[Voice] Receive error: {e}", exc_info=True)
        raise


async def _send_to_client(
    websocket: WebSocket,
    voice_agent,
    session_id: str,
) -> None:
    """Receive events from agent and forward to WebSocket client"""
    try:
        async for event in voice_agent.receive_events():
            await websocket.send_json(event)

    except asyncio.CancelledError:
        logger.debug(f"[Voice] Send task cancelled: session={session_id}")
        raise

    except Exception as e:
        logger.error(f"[Voice] Send error: {e}", exc_info=True)
        raise


@router.get("/voice/sessions")
async def list_voice_sessions():
    """List active voice sessions (for debugging)"""
    return {
        "active_sessions": list(_active_sessions.keys()),
        "count": len(_active_sessions),
    }


@router.delete("/voice/sessions/{session_id}")
async def stop_voice_session(session_id: str):
    """Force stop a voice session"""
    if session_id in _active_sessions:
        voice_agent = _active_sessions[session_id]
        await voice_agent.stop()
        del _active_sessions[session_id]
        return {"status": "stopped", "session_id": session_id}
    return {"status": "not_found", "session_id": session_id}


# =============================================================================
# /ws endpoint for AgentCore Runtime
# AgentCore Runtime routes WebSocket requests to /ws on the container
# This is an alias of /voice/stream for cloud deployment compatibility
# =============================================================================

@router.websocket("/ws")
async def ws_stream(
    websocket: WebSocket,
    session_id: Optional[str] = Query(None, description="Session ID"),
    user_id: Optional[str] = Query(None, description="User ID"),
    auth_token: Optional[str] = Query(None, description="Cognito JWT for MCP Runtime 3LO"),
):
    """WebSocket endpoint for AgentCore Runtime (cloud mode).

    AgentCore Runtime proxies WebSocket to /ws on the container. Mirrors
    /voice/stream.
    """
    await voice_stream(
        websocket=websocket,
        session_id=session_id,
        user_id=user_id,
        auth_token=auth_token,
    )
