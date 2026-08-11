"""Lazy agent package exports.

Keeping package import side-effect free lets focused runtimes import
``agents.model_factory`` without loading browser, voice, and connector stacks.
"""

__all__ = [
    "BaseAgent",
    "ChatAgent",
    "SkillChatAgent",
    "create_agent",
    "VoiceAgent",
]


def __getattr__(name):
    if name == "BaseAgent":
        from agents.base import BaseAgent

        return BaseAgent
    if name == "ChatAgent":
        from agents.chat_agent import ChatAgent

        return ChatAgent
    if name == "SkillChatAgent":
        from agents.skill_chat_agent import SkillChatAgent

        return SkillChatAgent
    if name == "create_agent":
        from agents.factory import create_agent

        return create_agent
    if name == "VoiceAgent":
        from agent.voice_agent import VoiceAgent

        return VoiceAgent
    raise AttributeError(name)
