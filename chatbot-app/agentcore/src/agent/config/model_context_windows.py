"""Measured context-window sizes per model.

Kept separate from model_factory so that consumers which only need the numbers
(context compaction, in particular) do not have to import the agent stack.
model_factory pulls in the full tool set via agents/__init__, including
optional native dependencies; the session manager must not depend on those.
"""

import logging
from typing import Optional

from agent.config.model_catalog import (
    DEFAULT_MAX_INPUT_TOKENS,
    get_model_catalog,
)

logger = logging.getLogger(__name__)


# Maximum input tokens per model, used to size context compaction relative to the
# model actually in use rather than a single hard-coded number.
#
# Catalog values were measured against the deployed endpoints by sending an
# oversized prompt and reading the limit back out of the rejection, e.g.
#   prompt tokens (1600007) exceed model maximum (1050000) for openai.gpt-5.6-luna
#   prompt is too long: 1600056 tokens > 1000000 maximum   (claude-opus-5)
# Published docs agree where they exist, but the probe is what these are from:
# the limit that matters is the one our account and region actually enforce.
#
# Note the spread — 131k to 1.05M, an 8x range. Sizing compaction off one
# constant either wastes most of a 1M window or overflows a 131k one.
#
# Re-measure when adding a model to the catalog; do not guess. A value that is
# too high is worse than one that is too low: compaction fires too late and the
# model call fails outright, instead of merely trimming sooner than necessary.
MODEL_MAX_INPUT_TOKENS: dict[str, int] = {
    spec.model_id: spec.max_input_tokens
    for spec in get_model_catalog().models.values()
}


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
