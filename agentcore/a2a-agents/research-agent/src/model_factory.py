"""Provider-aware model construction for the Research Agent."""

import os
from typing import Optional

import boto3
from botocore.config import Config
from strands.models import BedrockModel


MANTLE_MODEL_REGIONS: dict[str, str] = {
    "openai.gpt-5.6-sol": "us-east-1",
    "openai.gpt-5.6-terra": "us-east-1",
    "openai.gpt-5.6-luna": "us-east-1",
    "xai.grok-4.3": "us-west-2",
    "google.gemma-4-31b": "us-east-2",
    "google.gemma-4-26b-a4b": "us-east-2",
    "google.gemma-4-e2b": "us-east-2",
}

NATIVE_MODEL_REGION_OVERRIDES: dict[str, str] = {
    "qwen.qwen3-235b-a22b-2507-v1:0": "us-west-2",
}

_bedrock_api_key: Optional[str] = None


def _get_bedrock_api_key() -> str:
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
            "Mantle model requested but no Bedrock API key is configured"
        )

    region = os.environ.get("AWS_REGION", "us-west-2")
    client = boto3.client("secretsmanager", region_name=region)
    _bedrock_api_key = client.get_secret_value(SecretId=secret_name)["SecretString"]
    return _bedrock_api_key


def build_model(
    model_id: str,
    *,
    app_region: str,
    max_tokens: int = 32000,
):
    """Build a Mantle Responses model or a native Bedrock model."""
    mantle_region = MANTLE_MODEL_REGIONS.get(model_id)
    if mantle_region:
        from strands.models.openai_responses import OpenAIResponsesModel

        return OpenAIResponsesModel(
            model_id=model_id,
            params={"max_output_tokens": max_tokens},
            client_args={
                "api_key": _get_bedrock_api_key(),
                "base_url": (
                    f"https://bedrock-mantle.{mantle_region}.api.aws/openai/v1"
                ),
            },
        )

    retry_config = Config(
        retries={"max_attempts": 10, "mode": "adaptive"},
        connect_timeout=30,
        read_timeout=300,
    )
    return BedrockModel(
        model_id=model_id,
        region_name=NATIVE_MODEL_REGION_OVERRIDES.get(model_id, app_region),
        boto_client_config=retry_config,
        max_tokens=max_tokens,
    )
