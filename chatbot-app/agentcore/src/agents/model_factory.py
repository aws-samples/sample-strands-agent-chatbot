"""Model factory - builds the right Strands model provider for a given model_id.

Three execution paths coexist:
- Bedrock Runtime Converse (`BedrockModel`): default for native and
  cross-Region inference-profile IDs.
- Bedrock Runtime OpenAI-compatible Responses (`OpenAIResponsesModel`):
  GPT-5.6 models, including file inputs that Converse rejects.
- Bedrock Mantle OpenAI-compatible Responses (`OpenAIResponsesModel`):
  models not yet available through Bedrock Runtime, currently Gemma 4.

Responses models differ by endpoint and region, but share request formatting.
An empty Responses turn can otherwise be swallowed by the Strands SDK, so the
adapter retries before any output is streamed downstream.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import boto3
from strands.models import BedrockModel, CacheConfig
from strands.types.exceptions import ContextWindowOverflowException

from agent.config.model_catalog import get_model_catalog, normalize_model_id
# Re-exported for callers that already reach for model_factory. The registry
# itself lives in its own module so the session manager can size compaction
# without importing the agent stack.
from agent.config.model_context_windows import (  # noqa: F401
    DEFAULT_MAX_INPUT_TOKENS,
    MODEL_MAX_INPUT_TOKENS,
    get_max_input_tokens,
)

logger = logging.getLogger(__name__)
_MODEL_CATALOG = get_model_catalog()
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "prompt is too long",
    "maximum context length",
    "context length exceeded",
    "context window exceeded",
)
_DOCUMENT_MIME_TYPES = {
    "csv": "text/csv",
    "doc": "application/msword",
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "html": "text/html",
    "md": "text/markdown",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "xls": "application/vnd.ms-excel",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}


def _is_context_overflow_error(error: BaseException) -> bool:
    message = str(error).lower()
    if "prompt tokens" in message and "exceed model maximum" in message:
        return True
    return any(marker in message for marker in _CONTEXT_OVERFLOW_MARKERS)


# Reasoning models that reject the `temperature` inference param. Listed by
# exact ID rather than substring: a substring like "opus-5" also matches
# "opus-4-5", which does accept temperature. Values come from the model catalog.
NO_TEMPERATURE_MODELS = frozenset(
    spec.model_id
    for spec in _MODEL_CATALOG.models.values()
    if spec.rejects_temperature
)


@dataclass(frozen=True)
class MantleSpec:
    """How to reach a Mantle-only model.

    api: "responses" -> /openai/v1 (OpenAIResponsesModel)
    region: Mantle region serving this model, independent of the app's region.
    """
    api: str
    region: str


# Mantle-only models (not callable through Bedrock Runtime). Anything not
# registered here falls back to BedrockModel. The region is pinned per model.
MANTLE_MODELS: dict[str, MantleSpec] = {
    spec.model_id: MantleSpec(api="responses", region=str(spec.region))
    for spec in _MODEL_CATALOG.models.values()
    if spec.transport == "mantle_responses"
}

BEDROCK_RESPONSES_MODELS = frozenset(
    spec.model_id
    for spec in _MODEL_CATALOG.models.values()
    if spec.transport == "bedrock_responses"
)


# Native Bedrock models that are NOT available in every region. Selecting one of
# catalog entry with a native region forces BedrockModel to that region,
# overriding the app's deployment region. Models available everywhere omit it.
NATIVE_MODEL_REGION_OVERRIDES: dict[str, str] = {
    spec.model_id: str(spec.region)
    for spec in _MODEL_CATALOG.models.values()
    if spec.transport == "bedrock" and spec.region
}


def model_rejects_temperature(model_id: str) -> bool:
    return normalize_model_id(model_id) in NO_TEMPERATURE_MODELS


_bedrock_api_key: Optional[str] = None


def _get_bedrock_api_key() -> str:
    """Fetch the Bedrock API key from Secrets Manager (cached process-wide).

    Falls back to the AWS_BEARER_TOKEN_BEDROCK env var for local development.
    """
    global _bedrock_api_key
    if _bedrock_api_key:
        return _bedrock_api_key

    env_key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if env_key:
        _bedrock_api_key = env_key
        return _bedrock_api_key

    secret_name = os.environ.get("BEDROCK_API_KEY_SECRET_NAME")
    if not secret_name:
        raise RuntimeError(
            "Responses model requested but no Bedrock API key available "
            "(set BEDROCK_API_KEY_SECRET_NAME or AWS_BEARER_TOKEN_BEDROCK)"
        )
    region = os.environ.get("AWS_REGION", "us-west-2")
    client = boto3.client("secretsmanager", region_name=region)
    _bedrock_api_key = client.get_secret_value(SecretId=secret_name)["SecretString"]
    return _bedrock_api_key


def _make_bedrock_responses_model(
    model_id: str,
    *,
    base_url: str,
    max_tokens: int,
    endpoint_label: str,
):
    """Build an OpenAI Responses model for a Bedrock-compatible endpoint."""
    import asyncio
    import base64
    import mimetypes

    from strands.models.openai_responses import OpenAIResponsesModel

    class BedrockOpenAIResponsesModel(OpenAIResponsesModel):
        """Live streaming preserved. Buffers only the content-less leading events
        (messageStart). On the first content/tool block, flushes the buffer and
        streams live. If a turn produces no content block at all, the buffer is
        discarded and the call retried. This is safe because nothing was yielded
        downstream before the first content block.
        """

        MAX_RETRIES = 6
        RETRY_BASE_S = 1.0
        _CONTENT_KEYS = ("contentBlockStart", "contentBlockDelta")

        async def stream(self, *args, **kwargs):
            attempt = 0
            while True:
                lead_buffer = []
                produced = False
                try:
                    async for event in super().stream(*args, **kwargs):
                        if produced:
                            yield event
                            continue
                        key = next(iter(event.keys())) if isinstance(event, dict) else None
                        if key in self._CONTENT_KEYS:
                            produced = True
                            for held in lead_buffer:
                                yield held
                            lead_buffer = []
                            yield event
                        else:
                            lead_buffer.append(event)
                except ContextWindowOverflowException:
                    raise
                except Exception as error:
                    if _is_context_overflow_error(error):
                        raise ContextWindowOverflowException(str(error)) from error
                    raise
                if produced:
                    return
                if attempt < self.MAX_RETRIES:
                    attempt += 1
                    logger.warning(
                        "%s empty turn for %s; retry %d/%d",
                        endpoint_label,
                        model_id,
                        attempt,
                        self.MAX_RETRIES,
                    )
                    await asyncio.sleep(self.RETRY_BASE_S * attempt)
                    continue
                logger.error(
                    "%s model %s exhausted retries on empty turn",
                    endpoint_label,
                    model_id,
                )
                for held in lead_buffer:
                    yield held
                return

        @classmethod
        def _format_request_message_content(cls, content, *, role="user"):
            # Bedrock's OpenAI-compatible Responses endpoints accept the standard
            # inline file shape with an explicit filename and base64 file_data.
            # Keep this conversion common to Runtime and Mantle so Strands
            # document ContentBlocks do not reach Converse.
            if "document" in content:
                doc = content["document"]
                fmt = doc["format"]
                mime = _DOCUMENT_MIME_TYPES.get(
                    fmt,
                    mimetypes.types_map.get(f".{fmt}", "application/octet-stream"),
                )
                data_url = f"data:{mime};base64,{base64.b64encode(doc['source']['bytes']).decode()}"
                name = doc.get("name", "document")
                return {
                    "type": "input_file",
                    "filename": f"{name}.{fmt}",
                    "file_data": data_url,
                }
            return super()._format_request_message_content(content, role=role)

    return BedrockOpenAIResponsesModel(
        model_id=model_id,
        params={"max_output_tokens": max_tokens},
        client_args={
            "api_key": _get_bedrock_api_key(),
            "base_url": base_url,
        },
    )


def build_model(
    model_id: str,
    *,
    temperature: Optional[float] = None,
    max_tokens: int = 32000,
    caching_enabled: bool = False,
):
    """Build the appropriate Strands model for `model_id`.

    GPT-5.6 -> Bedrock Runtime OpenAI-compatible Responses API.
    Mantle-only models -> Bedrock Mantle Responses API.
    Everything else -> BedrockModel Converse with IAM authentication.
    """
    model_id = normalize_model_id(model_id)

    if model_id in BEDROCK_RESPONSES_MODELS:
        region = os.environ.get("AWS_REGION", "us-west-2")
        logger.info(
            "Building Bedrock Runtime Responses model %s (region=%s)",
            model_id,
            region,
        )
        return _make_bedrock_responses_model(
            model_id,
            base_url=f"https://bedrock-runtime.{region}.amazonaws.com/openai/v1",
            max_tokens=max_tokens,
            endpoint_label="Bedrock Runtime Responses",
        )

    spec = MANTLE_MODELS.get(model_id)
    if spec is not None:
        logger.info("Building Mantle model %s (region=%s)", model_id, spec.region)
        return _make_bedrock_responses_model(
            model_id,
            base_url=f"https://bedrock-mantle.{spec.region}.api.aws/openai/v1",
            max_tokens=max_tokens,
            endpoint_label="Bedrock Mantle Responses",
        )

    from botocore.config import Config

    retry_config = Config(
        retries={"max_attempts": 10, "mode": "adaptive"},
        connect_timeout=30,
        read_timeout=300,
    )
    model_config = {
        "model_id": model_id,
        "boto_client_config": retry_config,
        "max_tokens": max_tokens,
    }
    # Force the region for native models that aren't available everywhere;
    # otherwise BedrockModel defaults to the app's AWS_REGION.
    region_override = NATIVE_MODEL_REGION_OVERRIDES.get(model_id)
    if region_override:
        model_config["region_name"] = region_override
        logger.info("Region override for %s -> %s", model_id, region_override)
    if not model_rejects_temperature(model_id):
        model_config["temperature"] = temperature if temperature is not None else 0.7
    if caching_enabled:
        model_config["cache_config"] = CacheConfig(strategy="auto")
        logger.info("Prompt caching enabled via CacheConfig(strategy='auto')")

    return BedrockModel(**model_config)
