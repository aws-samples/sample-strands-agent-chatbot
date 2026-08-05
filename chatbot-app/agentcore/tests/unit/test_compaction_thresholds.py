"""Tests for model-sized compaction thresholds and conditional truncation.

Two behaviours are pinned here:

1. The compaction threshold is derived from the model's context window, not a
   single constant. The picker spans 131k to 1.05M tokens, so one fixed number
   either wastes most of a 1M window or fires too late on the smallest model.

2. Load-time truncation of tool output only runs when the conversation is
   actually near that threshold. It used to run on every session load, which
   discarded old tool results and replaced images and documents with
   placeholders even for conversations using a fraction of the window.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from agent.config.constants import (  # noqa: E402
    COMPACTION_CONTEXT_RATIO,
    COMPACTION_TOKEN_CAP,
    COMPACTION_TOKEN_FLOOR,
    compaction_threshold_for,
)


from agent.config import model_context_windows as _mcw  # noqa: E402


def _model_factory():
    """The context-window registry.

    Deliberately not agents.model_factory: importing that pulls in ChatAgent and
    the whole tool stack, which is exactly the coupling this module avoids.
    """
    return _mcw


class TestCompactionThresholdForWindow:
    """compaction_threshold_for() = min(window * ratio, cap), floored."""

    def test_large_window_is_capped(self):
        # A 1M model would otherwise wait until 700k, where summarizing the
        # history is itself a slow and failure-prone model call.
        assert compaction_threshold_for(1_000_000) == COMPACTION_TOKEN_CAP
        assert compaction_threshold_for(1_050_000) == COMPACTION_TOKEN_CAP

    def test_small_window_scales_with_the_model(self):
        assert compaction_threshold_for(200_000) == 140_000
        assert compaction_threshold_for(262_144) == 183_500

    def test_threshold_always_leaves_headroom(self):
        # The whole point is to compact *before* the model rejects the request.
        for window in (131_072, 163_840, 200_000, 262_144, 1_000_000, 1_050_000):
            assert compaction_threshold_for(window) < window

    def test_tiny_window_gets_the_floor(self):
        # Without a floor a small model would compact almost immediately,
        # leaving no usable history.
        assert compaction_threshold_for(1_000) == COMPACTION_TOKEN_FLOOR

    def test_ratio_is_applied_below_the_cap(self):
        window = 300_000
        assert compaction_threshold_for(window) == int(window * COMPACTION_CONTEXT_RATIO)

    def test_monotonic_in_window_size(self):
        windows = [131_072, 163_840, 196_608, 262_144, 1_000_000]
        thresholds = [compaction_threshold_for(w) for w in windows]
        assert thresholds == sorted(thresholds)


class TestMeasuredModelWindows:
    """The registry values were measured against the live endpoints."""

    def test_frontier_models_are_one_million(self):
        get_max_input_tokens = _model_factory().get_max_input_tokens

        for model_id in (
            "us.anthropic.claude-opus-5",
            "us.anthropic.claude-sonnet-5",
        ):
            assert get_max_input_tokens(model_id) == 1_000_000
        for model_id in (
            "openai.gpt-5.6-sol",
            "openai.gpt-5.6-terra",
            "openai.gpt-5.6-luna",
        ):
            assert get_max_input_tokens(model_id) == 1_050_000

    def test_small_window_model_is_not_overstated(self):
        # gpt-oss is the smallest window in the picker; the old fixed 100k
        # threshold left it only 31k of headroom.
        get_max_input_tokens = _model_factory().get_max_input_tokens

        assert get_max_input_tokens("openai.gpt-oss-120b-1:0") == 131_072
        assert compaction_threshold_for(131_072) < 100_000

    def test_unknown_model_falls_back_conservatively(self):
        mf = _model_factory()
        DEFAULT_MAX_INPUT_TOKENS = mf.DEFAULT_MAX_INPUT_TOKENS
        get_max_input_tokens = mf.get_max_input_tokens

        assert get_max_input_tokens("some.model-we-have-not-measured") == DEFAULT_MAX_INPUT_TOKENS
        assert get_max_input_tokens(None) == DEFAULT_MAX_INPUT_TOKENS

    def test_fallback_is_no_larger_than_the_smallest_measured_model(self):
        # Overstating an unknown window is the dangerous direction: compaction
        # fires too late and the model call fails outright.
        mf = _model_factory()
        DEFAULT_MAX_INPUT_TOKENS = mf.DEFAULT_MAX_INPUT_TOKENS
        MODEL_MAX_INPUT_TOKENS = mf.MODEL_MAX_INPUT_TOKENS

        assert DEFAULT_MAX_INPUT_TOKENS <= min(MODEL_MAX_INPUT_TOKENS.values())

    def test_every_picker_model_is_measured(self):
        # A model in the picker but absent here silently gets the conservative
        # fallback, which quietly over-compacts a large-context model.
        from agents.model_factory import MANTLE_MODELS

        MODEL_MAX_INPUT_TOKENS = _model_factory().MODEL_MAX_INPUT_TOKENS

        missing = set(MANTLE_MODELS) - set(MODEL_MAX_INPUT_TOKENS)
        assert not missing, f"Mantle models without a measured window: {missing}"


def _manager(**overrides):
    """Build a CompactingSessionManager with its parent __init__ stubbed out."""
    from agent.session.compacting_session_manager import CompactingSessionManager

    kwargs = {
        "agentcore_memory_config": MagicMock(),
        "region_name": "us-west-2",
        "token_threshold": 500_000,
        **overrides,
    }
    with patch(
        "agent.session.compacting_session_manager."
        "AgentCoreMemorySessionManager.__init__",
        return_value=None,
    ):
        return CompactingSessionManager(**kwargs)


def _messages(char_count: int):
    return [{"role": "user", "content": [{"text": "x" * char_count}]}]


class TestConditionalTruncation:
    """Truncation is a response to size, not something done on every load."""

    def test_skipped_for_a_small_conversation(self):
        manager = _manager(token_threshold=500_000)
        should, estimated = manager._should_truncate(_messages(4_000))
        assert should is False
        assert estimated < 500_000

    def test_runs_once_the_estimate_reaches_the_threshold(self):
        manager = _manager(token_threshold=1_000)
        # ~4 chars/token, so 40k chars is well past a 1k-token threshold.
        should, estimated = manager._should_truncate(_messages(40_000))
        assert should is True
        assert estimated >= 1_000

    def test_threshold_tracks_the_model_window(self):
        # The same conversation is worth truncating on a small model and not on
        # a large one — which a single fixed constant cannot express.
        conversation = _messages(600_000)  # ~150k tokens
        small = _manager(token_threshold=compaction_threshold_for(131_072))
        large = _manager(token_threshold=compaction_threshold_for(1_000_000))

        assert small._should_truncate(conversation)[0] is True
        assert large._should_truncate(conversation)[0] is False

    def test_unserializable_content_is_treated_as_large(self):
        # Failing open here would skip compaction on exactly the payloads most
        # likely to be huge.
        manager = _manager(token_threshold=500_000)

        class Unserializable:
            def __repr__(self):  # pragma: no cover - forced json failure
                raise ValueError("nope")

        with patch(
            "agent.session.compacting_session_manager.json.dumps",
            side_effect=TypeError("unserializable"),
        ):
            should, _ = manager._should_truncate([{"role": "user", "content": Unserializable()}])
        assert should is True

    def test_estimate_grows_with_conversation_size(self):
        manager = _manager()
        assert manager._estimate_tokens(_messages(400)) < manager._estimate_tokens(
            _messages(400_000)
        )

    def test_empty_history_is_never_truncated(self):
        manager = _manager(token_threshold=COMPACTION_TOKEN_FLOOR)
        assert manager._should_truncate([])[0] is False
