"""Concise mode swaps the base prompt's style sections instead of appending to them.

The first implementation appended a "be brief" block after the base prompt. Both
sets of instructions then applied at once, and the base ones are more specific —
they mandate prose over lists, a 1-2 sentence minimum per bullet, and thorough
answers for open-ended questions. The model split the difference and kept
answering at length, so the toggle appeared broken even though the flag was
arriving correctly. These tests pin that the conflicting guidance is gone rather
than merely outweighed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from agent.config.prompt_builder import (  # noqa: E402
    BASE_TEXT_PROMPT,
    CONCISE_STYLE_SECTIONS,
    build_text_system_prompt,
    system_prompt_to_string,
)

# Base-prompt phrases that directly contradict brevity.
CONFLICTING = (
    "at least 1-2 sentences",
    "thorough responses",
    "prose and paragraphs",
)


def normal() -> str:
    return system_prompt_to_string(build_text_system_prompt())


def concise() -> str:
    return system_prompt_to_string(build_text_system_prompt(concise=True))


class TestConciseSwap:
    def test_conflicting_guidance_is_removed(self):
        prompt = concise()
        for phrase in CONFLICTING:
            assert phrase not in prompt, f"still instructs: {phrase}"

    def test_conflicting_guidance_is_present_by_default(self):
        # Guards the test above from passing because the base prompt changed.
        prompt = normal()
        for phrase in CONFLICTING:
            assert phrase in prompt

    def test_concise_guidance_replaces_it(self):
        prompt = concise()
        assert "Lead with the answer" in prompt
        assert "No preamble" in prompt

    def test_default_has_no_concise_guidance(self):
        assert "Lead with the answer" not in normal()

    def test_style_sections_appear_exactly_once(self):
        # Two <communication_style> blocks would be the appended-and-competing
        # arrangement all over again.
        prompt = concise()
        assert prompt.count("<communication_style>") == 1
        assert prompt.count("<response_approach>") == 1

    def test_unrelated_guidance_survives_the_swap(self):
        # Only the style sections are replaced; tool rules must not be collateral.
        prompt = concise()
        assert "<tool_usage>" in prompt
        assert "ONLY use tools that are explicitly provided" in prompt

    def test_date_is_still_stamped(self):
        assert "Current date:" in concise()

    def test_swap_is_a_no_op_when_markers_are_missing(self):
        # A prompt edit that drops the markers must not silently delete the
        # style guidance; it should fall through unchanged.
        from agent.config.prompt_builder import _swap_style_sections

        assert _swap_style_sections("no markers here", "REPLACEMENT") == "no markers here"

    def test_swap_uses_the_replacement_text(self):
        from agent.config.prompt_builder import _swap_style_sections

        result = _swap_style_sections(BASE_TEXT_PROMPT, "REPLACEMENT")
        assert "REPLACEMENT" in result
        assert "<communication_style>" not in result


class TestConcisePromptContent:
    """Guards the safety rules a brevity prompt most easily breaks."""

    def test_protects_correctness_over_brevity(self):
        assert "Never drop negations" in CONCISE_STYLE_SECTIONS
        assert "verbatim" in CONCISE_STYLE_SECTIONS

    def test_preserves_the_users_language(self):
        # An English-only style block otherwise nudges the model into answering
        # Korean questions in English.
        assert "language the user wrote in" in CONCISE_STYLE_SECTIONS

    def test_allows_length_where_brevity_would_delete_the_answer(self):
        assert "explain" in CONCISE_STYLE_SECTIONS
        assert "irreversible" in CONCISE_STYLE_SECTIONS

    def test_does_not_announce_itself(self):
        assert "Do not mention this style" in CONCISE_STYLE_SECTIONS


class TestAgentWiring:
    """The flag has to reach the prompt builder from the agent.

    Calls _build_system_prompt directly on a bare instance rather than inspecting
    source: a wiring check that only greps for the word "concise" still passes
    when the agent calls the builder without forwarding the flag.
    """

    @staticmethod
    def _prompt_for(agent_cls, concise: bool) -> str:
        instance = agent_cls.__new__(agent_cls)
        instance.concise_mode = concise
        return system_prompt_to_string(instance._build_system_prompt())

    def test_skill_chat_agent_honours_the_flag(self):
        # SkillChatAgent is the agent the chat path uses; it previously inlined
        # BASE_TEXT_PROMPT, which bypassed the toggle entirely.
        from agents.skill_chat_agent import SkillChatAgent

        assert "Lead with the answer" in self._prompt_for(SkillChatAgent, True)
        assert "Lead with the answer" not in self._prompt_for(SkillChatAgent, False)

    def test_skill_chat_agent_drops_conflicting_guidance(self):
        from agents.skill_chat_agent import SkillChatAgent

        prompt = self._prompt_for(SkillChatAgent, True)
        for phrase in CONFLICTING:
            assert phrase not in prompt

    def test_chat_agent_honours_the_flag(self):
        from agents.chat_agent import ChatAgent

        assert "Lead with the answer" in self._prompt_for(ChatAgent, True)
        assert "Lead with the answer" not in self._prompt_for(ChatAgent, False)

    # The wiring tests above build instances with __new__, which skips __init__ and
    # therefore missed a real break: ChatAgent lists its parameters explicitly
    # rather than taking **kwargs, so passing concise_mode through the factory
    # raised TypeError at runtime while every test still passed.
    def test_agent_constructors_accept_the_flag(self):
        import inspect

        from agents.chat_agent import ChatAgent
        from agents.skill_chat_agent import SkillChatAgent
        from agents.base import BaseAgent

        for cls in (BaseAgent, ChatAgent, SkillChatAgent):
            params = inspect.signature(cls.__init__).parameters
            accepts = "concise_mode" in params or any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            assert accepts, f"{cls.__name__}.__init__ rejects concise_mode"

    def test_factory_forwards_the_flag_to_the_agent(self):
        import inspect

        from agents import factory

        params = inspect.signature(factory.create_agent).parameters
        assert "concise_mode" in params
        assert "concise_mode=concise_mode" in inspect.getsource(factory.create_agent)

    def test_agents_default_to_the_normal_style(self):
        # concise_mode absent entirely (not just False) must not enable it.
        from agents.skill_chat_agent import SkillChatAgent

        instance = SkillChatAgent.__new__(SkillChatAgent)
        prompt = system_prompt_to_string(instance._build_system_prompt())
        assert "Lead with the answer" not in prompt
