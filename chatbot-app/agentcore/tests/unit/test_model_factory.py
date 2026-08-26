"""
Tests for agents.model_factory

Covers:
- build_model routing: Bedrock Converse vs Runtime Responses vs Mantle
- temperature guard (extended-thinking / reasoning models)
- Bedrock Runtime profile and legacy alias routing
- Mantle-only Gemma base_url / region selection
- Bedrock API key resolution (env var and Secrets Manager)
"""
import os
import pytest
from unittest.mock import patch, MagicMock

import agents.model_factory as mf
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.exceptions import ContextWindowOverflowException
from agents.model_factory import (
    build_model,
    model_rejects_temperature,
    BEDROCK_RESPONSES_MODELS,
    MANTLE_MODELS,
)


@pytest.fixture(autouse=True)
def _reset_key_cache():
    mf._bedrock_api_key = None
    yield
    mf._bedrock_api_key = None


class TestTemperatureGuard:
    @pytest.mark.parametrize("model_id", [
        "us.anthropic.claude-opus-5",
        "us.anthropic.claude-sonnet-5",
        "us.openai.gpt-5.6-sol",
        "us.openai.gpt-5.6-terra",
        "us.openai.gpt-5.6-luna",
        "us.xai.grok-4.6",
    ])
    def test_rejects(self, model_id):
        assert model_rejects_temperature(model_id) is True

    @pytest.mark.parametrize("model_id", [
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "google.gemma-4-31b",
    ])
    def test_allows(self, model_id):
        assert model_rejects_temperature(model_id) is False

    def test_guard_matches_whole_ids_not_substrings(self):
        """Every entry must be matched as a whole ID.

        A substring check would let a longer, unrelated ID that merely contains
        an entry be treated as temperature-rejecting — e.g. a future
        "…claude-opus-5-lite" or a "…claude-sonnet-5-preview" snapshot.
        """
        for entry in mf.NO_TEMPERATURE_MODELS:
            assert model_rejects_temperature(entry) is True
            assert model_rejects_temperature(f"{entry}-preview") is False
            assert model_rejects_temperature(f"prefixed.{entry}") is False


class TestBedrockRouting:
    def test_bedrock_model_for_native_id(self):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model("us.anthropic.claude-haiku-4-5-20251001-v1:0", temperature=0.5, caching_enabled=True)
            kwargs = MockBedrock.call_args.kwargs
            assert kwargs["model_id"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
            assert kwargs["temperature"] == 0.5
            assert "cache_config" in kwargs

    def test_no_temperature_for_sonnet_5(self):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model("us.anthropic.claude-sonnet-5", temperature=0.7)
            assert "temperature" not in MockBedrock.call_args.kwargs

    def test_no_temperature_for_opus(self):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model("us.anthropic.claude-opus-5", temperature=0.7)
            assert "temperature" not in MockBedrock.call_args.kwargs

    def test_no_temperature_for_grok_46(self):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model("us.xai.grok-4.6", temperature=0.7)
            assert "temperature" not in MockBedrock.call_args.kwargs

    def test_no_cache_when_disabled(self):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model("us.anthropic.claude-sonnet-5", caching_enabled=False)
            assert "cache_config" not in MockBedrock.call_args.kwargs

    def test_region_override_for_restricted_native_model(self):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model("qwen.qwen3-235b-a22b-2507-v1:0")
            assert MockBedrock.call_args.kwargs["region_name"] == "us-west-2"

    def test_no_region_override_for_global_model(self):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model("us.anthropic.claude-sonnet-5")
            assert "region_name" not in MockBedrock.call_args.kwargs

    @pytest.mark.parametrize("model_id", ["us.xai.grok-4.6"])
    def test_runtime_profile_models_use_bedrock(self, model_id):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model(model_id)
            assert MockBedrock.call_args.kwargs["model_id"] == model_id

    @pytest.mark.parametrize(("legacy_id", "canonical_id"), [
        ("xai.grok-4.3", "us.xai.grok-4.6"),
        ("xai.grok-4.6", "us.xai.grok-4.6"),
    ])
    def test_legacy_ids_are_normalized(self, legacy_id, canonical_id):
        with patch.object(mf, "BedrockModel") as MockBedrock:
            build_model(legacy_id)
            assert MockBedrock.call_args.kwargs["model_id"] == canonical_id


class TestBedrockRuntimeResponsesRouting:
    GPT_MODELS = (
        "us.openai.gpt-5.6-sol",
        "us.openai.gpt-5.6-terra",
        "us.openai.gpt-5.6-luna",
    )

    def test_only_gpt_56_uses_runtime_responses(self):
        assert BEDROCK_RESPONSES_MODELS == frozenset(self.GPT_MODELS)

    @pytest.mark.parametrize("model_id", GPT_MODELS)
    @patch.dict(os.environ, {
        "AWS_BEARER_TOKEN_BEDROCK": "test-key",
        "AWS_REGION": "us-west-2",
    })
    def test_gpt_56_uses_runtime_responses_endpoint(self, model_id):
        model = build_model(model_id)
        assert isinstance(model, OpenAIResponsesModel)
        assert (
            model.client_args["base_url"]
            == "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1"
        )
        assert model.client_args["api_key"] == "test-key"
        assert model.config["model_id"] == model_id

    @pytest.mark.parametrize(("legacy_id", "canonical_id"), [
        ("openai.gpt-5.6-sol", "us.openai.gpt-5.6-sol"),
        ("openai.gpt-5.6-terra", "us.openai.gpt-5.6-terra"),
        ("openai.gpt-5.6-luna", "us.openai.gpt-5.6-luna"),
    ])
    @patch.dict(os.environ, {
        "AWS_BEARER_TOKEN_BEDROCK": "test-key",
        "AWS_REGION": "us-west-2",
    })
    def test_legacy_gpt_ids_use_runtime_responses(self, legacy_id, canonical_id):
        model = build_model(legacy_id)
        assert model.config["model_id"] == canonical_id
        assert (
            model.client_args["base_url"]
            == "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1"
        )

    @pytest.mark.parametrize(("file_format", "expected_mime"), [
        ("txt", "text/plain"),
        ("pdf", "application/pdf"),
        (
            "docx",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
        ),
        (
            "xlsx",
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet",
        ),
    ])
    @patch.dict(os.environ, {
        "AWS_BEARER_TOKEN_BEDROCK": "test-key",
        "AWS_REGION": "us-west-2",
    })
    def test_document_uses_filename_and_file_data(
        self,
        file_format,
        expected_mime,
    ):
        model = build_model("us.openai.gpt-5.6-sol")
        block = {
            "document": {
                "format": file_format,
                "name": "probe",
                "source": {"bytes": b"BEDROCK_FILE_OK"},
            }
        }
        out = type(model)._format_request_message_content(block)
        assert out["type"] == "input_file"
        assert out["filename"] == f"probe.{file_format}"
        assert out["file_data"].startswith(f"data:{expected_mime};base64,")
        assert "file_url" not in out


class TestMantleRouting:
    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test-key"})
    def test_mantle_model_built_with_correct_base_url(self):
        model = build_model("google.gemma-4-31b")
        assert "bedrock-mantle.us-east-2.api.aws/openai/v1" in model.client_args["base_url"]
        assert model.client_args["api_key"] == "test-key"

    def test_only_gemma_models_remain_on_mantle(self):
        assert MANTLE_MODELS
        assert all(model_id.startswith("google.gemma-4") for model_id in MANTLE_MODELS)
        assert all(spec.region == "us-east-2" for spec in MANTLE_MODELS.values())

    def test_all_mantle_models_have_responses_api(self):
        for spec in MANTLE_MODELS.values():
            assert spec.api == "responses"
            assert spec.region

    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test-key"})
    def test_pdf_document_uses_filename_and_file_data(self):
        # Bedrock Responses endpoints require explicit filename + file_data.
        model = build_model("google.gemma-4-31b")
        block = {"document": {"format": "pdf", "name": "report", "source": {"bytes": b"%PDF-1.4 x"}}}
        out = type(model)._format_request_message_content(block)
        assert out["type"] == "input_file"
        assert out["filename"] == "report.pdf"
        assert out["file_data"].startswith("data:application/pdf;base64,")
        assert "file_url" not in out

    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test-key"})
    def test_non_document_content_delegates_to_parent(self):
        model = build_model("google.gemma-4-31b")
        out = type(model)._format_request_message_content({"text": "hi"})
        assert out == {"type": "input_text", "text": "hi"}

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test-key"})
    async def test_normalizes_mantle_prompt_token_overflow(self):
        async def failing_stream(_self, *args, **kwargs):
            if False:
                yield None
            raise RuntimeError(
                "prompt tokens (1209034) exceed model maximum (1050000)"
            )

        model = build_model("google.gemma-4-31b")
        with patch.object(OpenAIResponsesModel, "stream", failing_stream):
            with pytest.raises(ContextWindowOverflowException, match="1209034"):
                async for _ in model.stream([]):
                    pass

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test-key"})
    async def test_preserves_unrelated_mantle_errors(self):
        async def failing_stream(_self, *args, **kwargs):
            if False:
                yield None
            raise RuntimeError("upstream connection reset")

        model = build_model("google.gemma-4-31b")
        with patch.object(OpenAIResponsesModel, "stream", failing_stream):
            with pytest.raises(RuntimeError, match="connection reset"):
                async for _ in model.stream([]):
                    pass


class TestApiKeyResolution:
    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "env-key"})
    def test_env_var_takes_precedence(self):
        assert mf._get_bedrock_api_key() == "env-key"

    @patch.dict(os.environ, {"BEDROCK_API_KEY_SECRET_NAME": "my/secret", "AWS_REGION": "us-east-2"}, clear=True)
    def test_secrets_manager_fallback(self):
        fake_client = MagicMock()
        fake_client.get_secret_value.return_value = {"SecretString": "sm-key"}
        with patch.object(mf.boto3, "client", return_value=fake_client) as mock_client:
            assert mf._get_bedrock_api_key() == "sm-key"
            mock_client.assert_called_once_with("secretsmanager", region_name="us-east-2")
            fake_client.get_secret_value.assert_called_once_with(SecretId="my/secret")

    @patch.dict(os.environ, {}, clear=True)
    def test_raises_when_no_key(self):
        with pytest.raises(RuntimeError, match="no Bedrock API key"):
            mf._get_bedrock_api_key()
