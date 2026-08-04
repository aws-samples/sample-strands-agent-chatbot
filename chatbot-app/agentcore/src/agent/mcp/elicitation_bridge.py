"""
OAuth Elicitation Bridge

Bridge between MCP elicitation callback (MCPClient) and SSE stream (StreamEventProcessor).
When an MCP tool calls ctx.elicit_url(), the MCPClient's elicitation_callback fires.
This bridge:
1. Pushes an event to the outbound queue (SSE stream picks it up)
2. Waits for the frontend to signal completion via shared store (DynamoDB or in-memory)
3. Calls CompleteResourceTokenAuth from orchestrator's workload identity context
4. Emits a completion event on the same queue so the frontend can close its dialog
5. Returns the result to the MCPClient so the tool can resume

The elicitation ID is the sole correlation key. It is a server-generated UUID
that AgentCore Identity echoes back to the OAuth callback page as customState,
so the popup needs nothing from browser storage to signal completion.

Cloud mode uses DynamoDB for cross-container signaling (same pattern as StopSignalProvider).
Local mode uses in-memory state for simplicity.
"""

import asyncio
import logging
import os
import threading
import time
from typing import Optional, Any, Tuple

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1.0
_MAX_WAIT = 300


class OAuthElicitationBridge:
    """Bridge between MCP elicitation callback and SSE stream."""

    def __init__(self, session_id: str, user_id: str = "anonymous", auth_token: Optional[str] = None):
        self.session_id = session_id
        self.user_id = user_id
        self.auth_token = auth_token
        self._outbound_queue: asyncio.Queue = asyncio.Queue()

    async def elicitation_callback(self, context, params) -> Any:
        from mcp.types import ElicitResult

        if not hasattr(params, 'url'):
            return ElicitResult(action="decline")

        elicitation_id = getattr(params, 'elicitationId', str(id(params)))
        auth_url = params.url
        message = getattr(params, 'message', '')
        store = _get_elicitation_store()
        store.register_pending(self.session_id, elicitation_id, self.user_id)

        await self._outbound_queue.put({
            "type": "oauth_elicitation",
            "auth_url": auth_url,
            "message": message,
            "elicitation_id": elicitation_id,
            "session_id": self.session_id,
        })

        logger.info(f"[Elicitation] Waiting for OAuth completion: {elicitation_id}")

        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    _poll_for_completion,
                    store, self.session_id, elicitation_id,
                ),
                timeout=_MAX_WAIT + 5,
            )
            completed, oauth_session_uri = result
            if completed:
                logger.info(f"[Elicitation] OAuth completed: {elicitation_id}")

                if oauth_session_uri:
                    await self._complete_token_auth(oauth_session_uri)

                store.clear(self.session_id, elicitation_id)
                await self._emit_resolution(elicitation_id, "completed")
                return ElicitResult(action="accept")
            else:
                logger.warning(f"[Elicitation] Timeout waiting for OAuth: {elicitation_id}")
                store.clear(self.session_id, elicitation_id)
                await self._emit_resolution(elicitation_id, "timeout")
                return ElicitResult(action="cancel")
        except asyncio.TimeoutError:
            logger.warning(f"[Elicitation] Timeout waiting for OAuth: {elicitation_id}")
            store.clear(self.session_id, elicitation_id)
            await self._emit_resolution(elicitation_id, "timeout")
            return ElicitResult(action="cancel")

    async def _emit_resolution(self, elicitation_id: str, status: str):
        """Tell the frontend (over its own SSE stream) that the elicitation is
        settled, so it can dismiss the OAuth dialog without any popup->parent
        channel (postMessage and shared storage both break across the OAuth
        redirect chain)."""
        await self._outbound_queue.put({
            "type": "oauth_elicitation_resolved",
            "elicitation_id": elicitation_id,
            "status": status,
            "session_id": self.session_id,
        })

    async def _complete_token_auth(self, oauth_session_uri: str):
        """Call CompleteResourceTokenAuth from orchestrator's workload identity context."""
        if not self.auth_token:
            logger.warning("[Elicitation] No auth_token, skipping CompleteResourceTokenAuth")
            return

        try:
            import boto3
            region = os.environ.get("AWS_REGION", "us-west-2")
            client = boto3.client("bedrock-agentcore", region_name=region)

            token = self.auth_token
            if token.startswith("Bearer "):
                token = token[7:]

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: client.complete_resource_token_auth(
                sessionUri=oauth_session_uri,
                userIdentifier={"userToken": token},
            ))
            logger.info("[Elicitation] CompleteResourceTokenAuth succeeded")
        except Exception as e:
            logger.error(f"[Elicitation] CompleteResourceTokenAuth failed: {e}")

    def get_pending_event_nowait(self):
        try:
            return self._outbound_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


def _poll_for_completion(
    store: "ElicitationStore",
    session_id: str,
    elicitation_id: str,
) -> Tuple[bool, Optional[str]]:
    """Blocking poll. Returns (completed, oauth_session_uri)."""
    deadline = time.monotonic() + _MAX_WAIT
    while time.monotonic() < deadline:
        completed, oauth_session_uri = store.get_completion(session_id, elicitation_id)
        if completed:
            return True, oauth_session_uri
        time.sleep(_POLL_INTERVAL)
    return False, None


# ── Completion signal store ──────────────────────────────────────────────


class ElicitationStore:

    def register_pending(self, session_id: str, elicitation_id: str, user_id: str) -> None:
        raise NotImplementedError

    def signal_complete(self, session_id: str, elicitation_id: Optional[str], oauth_session_uri: Optional[str] = None) -> None:
        raise NotImplementedError

    def get_completion(self, session_id: str, elicitation_id: str) -> Tuple[bool, Optional[str]]:
        """Returns (is_completed, oauth_session_uri)."""
        raise NotImplementedError

    def clear(self, session_id: str, elicitation_id: str) -> None:
        raise NotImplementedError


class DynamoDBElicitationStore(ElicitationStore):

    def __init__(self, table_name: str):
        import boto3
        region = os.environ.get("AWS_REGION", "us-west-2")
        self._client = boto3.client("dynamodb", region_name=region)
        self._table_name = table_name

    def _get_key(self, session_id: str, elicitation_id: str) -> dict:
        # Keyed by elicitation ID alone: it is a server-generated UUID, and the
        # OAuth callback page can only present the ID it received as
        # customState — it has no session context. session_id is kept as an
        # attribute for observability.
        return {
            "userId": {"S": f"ELICIT#{elicitation_id}"},
            "sk": {"S": "META"},
        }

    def register_pending(self, session_id: str, elicitation_id: str, user_id: str) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item={
                **self._get_key(session_id, elicitation_id),
                "status": {"S": "pending"},
                "ownerUserId": {"S": user_id},
                "sessionId": {"S": session_id},
                "ttl": {"N": str(int(time.time()) + 600)},
            },
        )

    def signal_complete(self, session_id: str, elicitation_id: Optional[str], oauth_session_uri: Optional[str] = None) -> None:
        eid = elicitation_id or "__all__"
        try:
            item = {
                **self._get_key(session_id, eid),
                "status": {"S": "completed"},
                "sessionId": {"S": session_id},
                "ttl": {"N": str(int(time.time()) + 600)},
            }
            if oauth_session_uri:
                item["oauthSessionUri"] = {"S": oauth_session_uri}
            self._client.put_item(TableName=self._table_name, Item=item)
            logger.info(f"[Elicitation] Completion signalled in DynamoDB: {session_id}/{eid}")
        except Exception as e:
            logger.error(f"[Elicitation] DynamoDB put failed: {e}")

    def get_completion(self, session_id: str, elicitation_id: str) -> Tuple[bool, Optional[str]]:
        try:
            resp = self._client.get_item(
                TableName=self._table_name,
                Key=self._get_key(session_id, elicitation_id),
            )
            item = resp.get("Item")
            if item and item.get("status", {}).get("S") == "completed":
                uri = item.get("oauthSessionUri", {}).get("S")
                return True, uri
            return False, None
        except Exception as e:
            logger.warning(f"[Elicitation] DynamoDB check failed: {e}")
            return False, None

    def clear(self, session_id: str, elicitation_id: str) -> None:
        try:
            self._client.delete_item(TableName=self._table_name, Key=self._get_key(session_id, elicitation_id))
        except Exception:
            pass


class LocalElicitationStore(ElicitationStore):

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._completed: dict[str, Optional[str]] = {}
                    cls._instance._data_lock = threading.Lock()
        return cls._instance

    def _key(self, session_id: str, elicitation_id: str) -> str:
        return f"{session_id}:{elicitation_id}"

    def register_pending(self, session_id: str, elicitation_id: str, user_id: str) -> None:
        with self._data_lock:
            self._completed.pop(self._key(session_id, elicitation_id), None)

    def signal_complete(self, session_id: str, elicitation_id: Optional[str], oauth_session_uri: Optional[str] = None) -> None:
        eid = elicitation_id or "__all__"
        with self._data_lock:
            self._completed[self._key(session_id, eid)] = oauth_session_uri
        logger.info(f"[Elicitation] Completion signalled (local): {session_id}/{eid}")

    def get_completion(self, session_id: str, elicitation_id: str) -> Tuple[bool, Optional[str]]:
        with self._data_lock:
            key = self._key(session_id, elicitation_id)
            if key in self._completed:
                return True, self._completed[key]
            return False, None

    def clear(self, session_id: str, elicitation_id: str) -> None:
        with self._data_lock:
            self._completed.pop(self._key(session_id, elicitation_id), None)


# ── Singleton store ──────────────────────────────────────────────────────

_store_instance: Optional[ElicitationStore] = None
_store_lock = threading.Lock()


def _get_elicitation_store() -> ElicitationStore:
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                table_name = os.environ.get("DYNAMODB_USERS_TABLE")
                if table_name:
                    logger.info(f"[Elicitation] Using DynamoDB store (table={table_name})")
                    _store_instance = DynamoDBElicitationStore(table_name)
                else:
                    logger.info("[Elicitation] Using local in-memory store")
                    _store_instance = LocalElicitationStore()
    return _store_instance


# ── Public API ─────────────────────────────────────────────────────────

def signal_elicitation_complete(session_id: str, elicitation_id: Optional[str] = None) -> None:
    store = _get_elicitation_store()
    store.signal_complete(session_id, elicitation_id)


# ── Bridge registry ───────────────────────────────────────────────────

_elicitation_bridges: dict[str, OAuthElicitationBridge] = {}


def register_bridge(session_id: str, bridge: OAuthElicitationBridge):
    _elicitation_bridges[session_id] = bridge


def get_bridge(session_id: str) -> Optional[OAuthElicitationBridge]:
    return _elicitation_bridges.get(session_id)


def cleanup_bridge(session_id: str):
    _elicitation_bridges.pop(session_id, None)
