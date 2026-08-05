"""Measured context-window sizes per model.

Kept separate from model_factory so that consumers which only need the numbers
(context compaction, in particular) do not have to import the agent stack.
model_factory pulls in the full tool set via agents/__init__, including
optional native dependencies; the session manager must not depend on those.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Maximum input tokens per model, used to size context compaction relative to the
# model actually in use rather than a single hard-coded number.
#
# Every value below was measured against the deployed endpoints by sending an
# oversized prompt and reading the limit back out of the rejection, e.g.
#   prompt tokens (1600007) exceed model maximum (1050000) for openai.gpt-5.6-luna
#   prompt is too long: 1600056 tokens > 1000000 maximum   (claude-opus-5)
# Published docs agree where they exist, but the probe is what these are from:
# the limit that matters is the one our account and region actually enforce.
#
# Note the spread — 131k to 1.05M, an 8x range. Sizing compaction off one
# constant either wastes most of a 1M window or overflows a 131k one.
#
# Re-measure when adding a model to the picker; do not guess. A value that is too
# high is worse than one that is too low: compaction fires too late and the model
# call fails outright, instead of merely trimming sooner than necessary.
MODEL_MAX_INPUT_TOKENS: dict[str, int] = {
    # Anthropic — 1M is the default and the maximum; there is no separate
    # long-context model ID and no beta header is required.
    "us.anthropic.claude-opus-5": 1_000_000,
    "us.anthropic.claude-sonnet-5": 1_000_000,
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": 200_000,
    # OpenAI GPT-5.6 via Mantle — 1.05M on the base model IDs (Bedrock, Aug 2026).
    "openai.gpt-5.6-sol": 1_050_000,
    "openai.gpt-5.6-terra": 1_050_000,
    "openai.gpt-5.6-luna": 1_050_000,
    # gpt-oss is the smallest window in the picker.
    "openai.gpt-oss-120b-1:0": 131_072,
    "xai.grok-4.3": 1_048_576,
    "google.gemma-4-31b": 262_144,
    "google.gemma-4-26b-a4b": 262_144,
    # Registered as a Mantle model but not currently offered in the picker.
    "google.gemma-4-e2b": 131_072,
    "deepseek.v3.2": 163_840,
    "zai.glm-5": 202_752,
    "zai.glm-4.7": 202_752,
    "moonshotai.kimi-k2.5": 262_144,
    "minimax.minimax-m2.5": 196_608,
    "qwen.qwen3-235b-a22b-2507-v1:0": 262_144,
    "mistral.mistral-large-3-675b-instruct": 262_144,
}

# Applied to models missing from the table above. Deliberately pessimistic: an
# unknown model is more likely to be small than to be a 1M frontier model, and
# under-estimating only compacts earlier than needed.
DEFAULT_MAX_INPUT_TOKENS = 131_072


def get_max_input_tokens(model_id: Optional[str]) -> int:
    """Return the model's measured input-token limit, or a conservative default."""
    if not model_id:
        return DEFAULT_MAX_INPUT_TOKENS
    limit = MODEL_MAX_INPUT_TOKENS.get(model_id)
    if limit is None:
        logger.warning(
            "model_id=<%s> | no measured context limit; using conservative default %d. "
            "Measure the real limit and add it to MODEL_MAX_INPUT_TOKENS.",
            model_id, DEFAULT_MAX_INPUT_TOKENS,
        )
        return DEFAULT_MAX_INPUT_TOKENS
    return limit


