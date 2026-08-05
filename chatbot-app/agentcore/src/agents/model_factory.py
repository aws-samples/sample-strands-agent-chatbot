"""Model factory - builds the right Strands model provider for a given model_id.

Two execution paths coexist:
- Native Bedrock (`BedrockModel`): supports prompt caching. Default for any
  model_id not registered as Mantle-only.
- Bedrock Mantle OpenAI-compatible (`OpenAIResponsesModel`): for frontier/extra
  models not available on native Bedrock Converse (gpt-5.x, grok, gemma-4).
  No prompt caching. Authenticated with a Bedrock API key from Secrets Manager.

Mantle models differ by region and by API path, so each is registered with its
own spec. A Mantle stream can intermittently end with a `response.failed` event
that the Strands SDK silently swallows (producing an empty turn);
BedrockMantleResponsesModel retries those before any output is streamed downstream.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import boto3
from strands.models import BedrockModel, CacheConfig

# Re-exported for callers that already reach for model_factory. The registry
# itself lives in its own module so the session manager can size compaction
# without importing the agent stack.
from agent.config.model_context_windows import (  # noqa: F401
    DEFAULT_MAX_INPUT_TOKENS,
    MODEL_MAX_INPUT_TOKENS,
    get_max_input_tokens,
)

logger = logging.getLogger(__name__)


# Reasoning models that reject the `temperature` inference param. Listed by
# exact ID rather than substring: a substring like "opus-5" also matches
# "opus-4-5", which does accept temperature. Add an entry when a model is added
# to the picker.
NO_TEMPERATURE_MODELS = frozenset({
    "us.anthropic.claude-opus-5",
    "us.anthropic.claude-sonnet-5",
    "openai.gpt-5.6-sol",
    "openai.gpt-5.6-terra",
    "openai.gpt-5.6-luna",
})


@dataclass(frozen=True)
class MantleSpec:
    """How to reach a Mantle-only model.

    api: "responses" -> /openai/v1 (OpenAIResponsesModel)
    region: Mantle region serving this model (independent of the app's region;
            e.g. grok-4.3 is us-west-2 only, gpt-5.6 is served in us-east-1).
    """
    api: str
    region: str


# Mantle-only models (not callable via native Bedrock Converse). Anything not
# listed here falls back to BedrockModel. The region is pinned per model: a
# Mantle model is only served in some regions (grok-4.3 in us-west-2, gpt-5.6
# in us-east-1), so the base_url region is forced independent of where the
# app is deployed.
MANTLE_MODELS: dict[str, MantleSpec] = {
    "openai.gpt-5.6-sol": MantleSpec(api="responses", region="us-east-1"),
    "openai.gpt-5.6-terra": MantleSpec(api="responses", region="us-east-1"),
    "openai.gpt-5.6-luna": MantleSpec(api="responses", region="us-east-1"),
    "xai.grok-4.3": MantleSpec(api="responses", region="us-west-2"),
    "google.gemma-4-31b": MantleSpec(api="responses", region="us-east-2"),
    "google.gemma-4-26b-a4b": MantleSpec(api="responses", region="us-east-2"),
    "google.gemma-4-e2b": MantleSpec(api="responses", region="us-east-2"),
}


# Native Bedrock models that are NOT available in every region. Selecting one of
# these forces the BedrockModel client to the listed region, overriding the
# app's deployment region (AWS_REGION). Models available everywhere are omitted
# and use the default region. Keep in sync with regional Converse availability.
NATIVE_MODEL_REGION_OVERRIDES: dict[str, str] = {
    # qwen3-235b is absent in us-east-1; pin to us-west-2 which serves it.
    "qwen.qwen3-235b-a22b-2507-v1:0": "us-west-2",
}


def model_rejects_temperature(model_id: str) -> bool:
    return model_id in NO_TEMPERATURE_MODELS


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
            "Mantle model requested but no Bedrock API key available "
            "(set BEDROCK_API_KEY_SECRET_NAME or AWS_BEARER_TOKEN_BEDROCK)"
        )
    region = os.environ.get("AWS_REGION", "us-west-2")
    client = boto3.client("secretsmanager", region_name=region)
    _bedrock_api_key = client.get_secret_value(SecretId=secret_name)["SecretString"]
    return _bedrock_api_key


def _make_bedrock_mantle_responses_model(model_id: str, spec: MantleSpec, max_tokens: int):
    """Build the OpenAI Responses model configured for Bedrock Mantle."""
    import asyncio
    import base64
    import mimetypes

    from strands.models.openai_responses import OpenAIResponsesModel

    class BedrockMantleResponsesModel(OpenAIResponsesModel):
        """Live streaming preserved. Buffers only the content-less leading events
        (messageStart). On the first content/tool block, flushes the buffer and
        streams live. If a turn produces no content block at all -- the Mantle
        `response.failed` event that the SDK swallows into an empty turn -- the
        buffer is discarded and the call retried. Safe because nothing was yielded
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
                if produced:
                    return
                if attempt < self.MAX_RETRIES:
                    attempt += 1
                    logger.warning(
                        "Mantle empty turn (response.failed) for %s; retry %d/%d",
                        model_id, attempt, self.MAX_RETRIES,
                    )
                    await asyncio.sleep(self.RETRY_BASE_S * attempt)
                    continue
                logger.error("Mantle model %s exhausted retries on empty turn", model_id)
                for held in lead_buffer:
                    yield held
                return

        @classmethod
        def _format_request_message_content(cls, content, *, role="user"):
            # The SDK emits documents as {"type": "input_file", "file_url": <data-url>},
            # which Mantle rejects with "Unsupported file type: 'unknown'". Mantle
            # requires the OpenAI-standard shape with an explicit filename + file_data.
            if "document" in content:
                doc = content["document"]
                fmt = doc["format"]
                mime = mimetypes.types_map.get(f".{fmt}", "application/octet-stream")
                data_url = f"data:{mime};base64,{base64.b64encode(doc['source']['bytes']).decode()}"
                name = doc.get("name", "document")
                return {
                    "type": "input_file",
                    "filename": f"{name}.{fmt}",
                    "file_data": data_url,
                }
            return super()._format_request_message_content(content, role=role)

    base_url = f"https://bedrock-mantle.{spec.region}.api.aws/openai/v1"
    return BedrockMantleResponsesModel(
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

    Mantle-only models -> BedrockMantleResponsesModel (no caching, no temperature).
    Everything else -> BedrockModel (caching + boto retry).
    """
    spec = MANTLE_MODELS.get(model_id)
    if spec is not None:
        logger.info("Building Mantle model %s (region=%s)", model_id, spec.region)
        return _make_bedrock_mantle_responses_model(model_id, spec, max_tokens)

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
