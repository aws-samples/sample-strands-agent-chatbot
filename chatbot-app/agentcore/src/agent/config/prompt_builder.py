"""Prompt builder — base prompts + date stamps.

Historically this module also loaded per-tool `systemPromptGuidance` from
`tools-config.json` / DynamoDB. That lived-in-two-places knob has been folded
into each skill's SKILL.md, so the guidance-loading code is gone. SkillChatAgent
loads SKILL.md via progressive disclosure; ChatAgent (now only a superclass)
falls back to the base prompt + date.
"""

import logging
from datetime import datetime
from typing import List, Dict, TypedDict

# Import timezone support (zoneinfo for Python 3.9+, fallback to pytz)
try:
    from zoneinfo import ZoneInfo  # noqa: F401
    TIMEZONE_AVAILABLE = True
except ImportError:
    try:
        import pytz  # noqa: F401
        TIMEZONE_AVAILABLE = True
    except ImportError:
        TIMEZONE_AVAILABLE = False

logger = logging.getLogger(__name__)


class SystemContentBlock(TypedDict, total=False):
    """Content block for system prompt - text or cache point."""
    text: str
    cachePoint: Dict[str, str]


# =============================================================================
# Base Prompts
# =============================================================================

BASE_TEXT_PROMPT = """You are an intelligent AI agent with dynamic tool capabilities. You can perform various tasks based on the combination of tools available to you.

<tool_usage>
- Use available tools when they genuinely enhance your response
- You can ONLY use tools that are explicitly provided to you — available tools may change between turns within the same conversation, so always refer to the current set of tools
- Select the most appropriate tool for the task - avoid redundant tool calls
- If you don't have the right tool for a task, clearly inform the user
</tool_usage>

<communication_style>
- For casual, emotional, empathetic, or advice-driven conversations, keep your tone natural, warm, and empathetic
- In casual conversation or chit chat, respond in sentences or paragraphs - avoid using lists
- It's fine for casual responses to be short, just a few sentences long
- For reports, documents, technical documentation, and explanations, write in prose and paragraphs without bullet points or numbered lists - write lists in natural language like "some things include: x, y, and z"
- If you use bullet points, each should be at least 1-2 sentences long unless requested otherwise
- Give concise responses to simple questions, but provide thorough responses to complex and open-ended questions
- Tailor your response format to suit the conversation topic
- Avoid starting responses with flattery like "great question" or "excellent idea" - respond directly
- If you cannot or will not help with something, state what you can't or won't do at the start, keep it brief (1-2 sentences), and offer helpful alternatives if possible
</communication_style>

<response_approach>
- For every query, attempt to give a substantive answer using your knowledge or tools
- Infer user intent from context rather than asking clarifying questions. When users share content (screenshots, messages, documents) with a brief instruction, figure out what they need and act on it immediately
- If the user's intent is reasonably clear from context, just do it. Only ask for clarification when the request is genuinely ambiguous and you cannot make a reasonable assumption
- Provide direct answers while acknowledging uncertainty when needed
- Explain difficult concepts clearly with examples, thought experiments, or metaphors when helpful
- When asking questions, avoid overwhelming with more than one question per response
- If corrected, think through the issue carefully before acknowledging, as users sometimes make errors themselves
</response_approach>

Your goal is to be helpful, accurate, and efficient."""

# Concise variant of the two style sections above.
#
# This replaces them rather than being appended after them. Appending a "be
# brief" block left both sets of instructions in the prompt at once, and the base
# ones are more specific — they mandate prose over lists, a 1-2 sentence minimum
# per bullet, and thorough answers for complex questions. The model split the
# difference and kept answering at length, so the toggle looked broken even
# though the flag was arriving correctly.
#
# Rules adapted from two MIT-licensed prompt skills:
#   https://github.com/ayghri/i-have-adhd   (lead with the action, cap lists,
#     no preamble/recap/closer, explicit "when to break the rules")
#   https://github.com/juliusbrussee/caveman (cut filler not meaning; never drop
#     negations; keep code and error strings verbatim)
#
# Two departures from those sources, both deliberate:
#   1. No persona. caveman drops articles and speaks in fragments, which is a
#      voice rather than brevity; a general assistant doing that reads as broken.
#   2. Explicit language preservation. Both sources are English-first and this
#      app is used in Korean, so an English style block otherwise nudges the
#      model into answering Korean questions in English.
CONCISE_STYLE_SECTIONS = """<communication_style>
- Lead with the answer. If it is a command, path, number, or code, that goes first.
- Prefer the shortest form that is still complete and correct.
- Use a numbered list for multi-step work; one action per step.
- Cap lists at 5 items. If there are more, rank them and split "now" vs "later".
- Keep bullets to a single line where the content allows it.
- No preamble. Never open with "Great question", "Let me", "I'll", "Sure", or
  "Looking at your".
- No recap of what you just did, and no closing pleasantries such as "Hope this
  helps" or "Let me know if you need anything else".
- Cut filler and hedging that carries no information: "just", "basically",
  "actually", "simply", "perhaps", "might possibly".
- Finish the question asked. If something else matters, add it as one short line
  at the end rather than weaving it through.
</communication_style>

<response_approach>
- Give a substantive answer using your knowledge or tools. Brevity never
  justifies a wrong, vague, or unverifiable answer.
- Infer user intent from context rather than asking clarifying questions. Only
  ask when the request is genuinely ambiguous.
- State a cause and a fix together: what is wrong, then what to do.
- Never drop negations — "not", "no", "never", "only", "except" change meaning.
- Numbers, units, version strings, file paths, and error text stay exact and
  verbatim. Code blocks and commands stay complete and runnable.
- Keep a hedge that reflects real uncertainty; removing it manufactures
  confidence you do not have.
- Reply in the language the user wrote in. Compress the style, never switch the
  language.
- Be longer when brevity would delete the answer: when asked to explain, teach,
  compare, or walk through something; when the answer is a set of options (give
  2 to 4, ranked, recommendation first); and when safety, data loss, cost, or
  anything irreversible is involved.
- Do not mention this style, name this mode, or narrate that you are being brief.
</response_approach>"""


BASE_VOICE_PROMPT = """You are a voice assistant.

<voice_style>
- Respond in 1-3 short sentences unless asked for detail
- Use natural spoken language only - no markdown, lists, or code
- Keep tone warm and conversational
- Avoid flattery - respond directly
- If you can't help, state it briefly and offer alternatives
</voice_style>

<tool_usage>
- Use tools when they enhance your response
- When using tools, say briefly what you're doing
- Only use tools explicitly provided to you
</tool_usage>"""


# =============================================================================
# Utilities
# =============================================================================

def get_current_date_pacific() -> str:
    """Get current date and hour in US Pacific timezone."""
    try:
        if TIMEZONE_AVAILABLE:
            try:
                from zoneinfo import ZoneInfo
                pacific_tz = ZoneInfo("America/Los_Angeles")
                now = datetime.now(pacific_tz)
                tz_abbr = now.strftime("%Z")
            except (ImportError, NameError):
                import pytz
                pacific_tz = pytz.timezone("America/Los_Angeles")
                now = datetime.now(pacific_tz)
                tz_abbr = now.strftime("%Z")
            return now.strftime(f"%Y-%m-%d (%A) %H:00 {tz_abbr}")
        now = datetime.utcnow()
        return now.strftime("%Y-%m-%d (%A) %H:00 UTC")
    except Exception as e:
        logger.warning(f"Failed to get Pacific time: {e}, using UTC")
        now = datetime.utcnow()
        return now.strftime("%Y-%m-%d (%A) %H:00 UTC")


# =============================================================================
# System Prompt Builders
# =============================================================================

def _swap_style_sections(prompt: str, replacement: str) -> str:
    """Replace the communication_style + response_approach pair in a prompt.

    The two sections are adjacent in BASE_TEXT_PROMPT, so one span covers both.
    Returns the prompt unchanged if the markers are missing, which keeps a prompt
    edit from silently dropping the style guidance altogether.
    """
    start = prompt.find("<communication_style>")
    end = prompt.find("</response_approach>")
    if start == -1 or end == -1:
        logger.warning(
            "Style section markers not found in base prompt; leaving it unchanged"
        )
        return prompt
    return prompt[:start] + replacement + prompt[end + len("</response_approach>"):]


def build_text_system_prompt(concise: bool = False) -> List[SystemContentBlock]:
    """Base + date for text mode.

    Args:
        concise: Swap the style sections for their concise variants instead of
            appending a second, competing set of instructions.
    """
    current_date = get_current_date_pacific()
    prompt = (
        _swap_style_sections(BASE_TEXT_PROMPT, CONCISE_STYLE_SECTIONS)
        if concise
        else BASE_TEXT_PROMPT
    )
    return [
        {"text": prompt},
        {"text": f"Current date: {current_date}"},
    ]


def build_voice_system_prompt() -> str:
    """Voice system prompt as a single string (Nova Sonic BidiAgent)."""
    current_date = get_current_date_pacific()
    return f"{BASE_VOICE_PROMPT}\n\nCurrent date: {current_date}"


def system_prompt_to_string(system_prompt: List[SystemContentBlock]) -> str:
    """Concatenate content blocks into a plain string."""
    parts = []
    for block in system_prompt or []:
        text = block.get("text") if isinstance(block, dict) else None
        if text:
            parts.append(text)
    return "\n\n".join(parts)
