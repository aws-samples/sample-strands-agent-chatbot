"""Tests for Research Agent provider-aware model construction."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import model_factory as mf  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_key_cache():
    mf._bedrock_api_key = None
    yield
    mf._bedrock_api_key = None


def test_native_model_uses_bedrock():
    with patch.object(mf, "BedrockModel") as bedrock_model:
        mf.build_model("us.anthropic.claude-sonnet-5", app_region="us-west-2")

    assert bedrock_model.call_args.kwargs["model_id"] == "us.anthropic.claude-sonnet-5"
    assert bedrock_model.call_args.kwargs["region_name"] == "us-west-2"


@patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test-key"})
def test_gpt_56_uses_mantle_responses_in_us_east_1():
    model = mf.build_model("openai.gpt-5.6-terra", app_region="us-west-2")

    assert model.config["model_id"] == "openai.gpt-5.6-terra"
    assert model.client_args["api_key"] == "test-key"
    assert "bedrock-mantle.us-east-1.api.aws/openai/v1" in model.client_args["base_url"]


def test_mantle_secret_is_loaded_from_runtime_region():
    secret_client = MagicMock()
    secret_client.get_secret_value.return_value = {"SecretString": "secret-key"}

    with patch.dict(
        os.environ,
        {
            "BEDROCK_API_KEY_SECRET_NAME": "project/bedrock/api-key",
            "AWS_REGION": "us-west-2",
        },
        clear=True,
    ), patch.object(mf.boto3, "client", return_value=secret_client) as boto_client:
        assert mf._get_bedrock_api_key() == "secret-key"

    boto_client.assert_called_once_with("secretsmanager", region_name="us-west-2")
    secret_client.get_secret_value.assert_called_once_with(
        SecretId="project/bedrock/api-key"
    )


def test_missing_mantle_credentials_fails_clearly():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="no Bedrock API key"):
            mf.build_model("openai.gpt-5.6-terra", app_region="us-west-2")
