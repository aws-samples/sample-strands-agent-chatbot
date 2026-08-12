"""
Browser automation tools using AgentCore Browser + Nova Act.
Each tool returns a screenshot to show current browser state.
"""

import logging
from typing import Dict, Any, Optional, List
from strands import tool, ToolContext
from skill import register_skill
from .lib.browser_controller import get_or_create_controller
from .lib.tool_response import build_success_response, build_image_response

logger = logging.getLogger(__name__)


def _build_browser_response(content: list, metadata: dict) -> Dict[str, Any]:
    """Build structured response for browser tools, handling mixed text+image content."""
    text_blocks = [b for b in content if "text" in b]
    image_blocks = [b for b in content if "image" in b]
    if image_blocks:
        return build_image_response(text_blocks, image_blocks, metadata)
    else:
        text = text_blocks[0]["text"] if text_blocks else ""
        return build_success_response(text, metadata)


def _format_tab_summary(tabs: List[Dict], current_tab: int = 0) -> str:
    """Format tab list as a compact summary string.

    Args:
        tabs: List of tab info dicts with 'index', 'title', 'is_current'
        current_tab: Current tab index (fallback if is_current not in tabs)

    Returns:
        Formatted string like: "**Tabs** (3): [0] Google | [1] Amazon <- | [2] GitHub"
        Returns empty string if only one tab.
    """
    if not tabs or len(tabs) <= 1:
        return ""

    tab_parts = []
    for tab in tabs:
        title = tab.get('title', 'Untitled')[:20]  # Truncate long titles
        is_current = tab.get('is_current', tab['index'] == current_tab)
        marker = " <-" if is_current else ""
        tab_parts.append(f"[{tab['index']}] {title}{marker}")

    return f"**Tabs** ({len(tabs)}): " + " | ".join(tab_parts)


def _format_tab_list_detailed(tabs: List[Dict]) -> str:
    """Format tab list with full details for get_page_info.

    Args:
        tabs: List of tab info dicts

    Returns:
        Formatted multi-line string with full tab details
    """
    if not tabs:
        return "No tabs open"

    lines = [f"**All Tabs** ({len(tabs)}):"]
    for tab in tabs:
        title = tab.get('title', 'Untitled')[:50]
        url = tab.get('url', 'about:blank')
        marker = "  <- current" if tab.get('is_current') else ""
        lines.append(f"  [{tab['index']}] {title}{marker}")
        lines.append(f"      {url}")

    return "\n".join(lines)


@tool(context=True)
def browser_act(instruction: str, starting_url: Optional[str] = None, tool_context: ToolContext = None) -> Dict[str, Any]:
    """
    Execute browser UI actions using an agent. Handles sequential visible UI tasks.

    Capabilities:
    - Actions: click, type, scroll, select dropdowns
    - Can execute up to 3 predictable steps in sequence
    - Optional starting_url: navigate to a page first, then execute the instruction

    Limitations:
    - Has 5-step limit. If fails, check screenshot and retry from current state
    - For DOM attributes, use browser_get_page_info()

    Args:
        instruction: Natural language instruction for UI actions.
                    Use numbered steps for predictable sequences:
                    "1. Type 'laptop' in search box 2. Click search button 3. Click first result"
        starting_url: Optional URL to navigate to before executing the instruction.
                     Use this to open a page and interact with it in a single call.

    Returns screenshot showing the result.
    """
    try:
        # Get session_id from ToolContext to avoid race condition with os.environ
        session_id = tool_context.invocation_state.get("session_id")
        if not session_id and hasattr(tool_context.agent, '_session_manager'):
            session_id = tool_context.agent._session_manager.session_id
            logger.info(f"[browser_act] Using session_id from agent._session_manager: {session_id}")
        elif session_id:
            logger.info(f"[browser_act] Using session_id from invocation_state: {session_id}")
        else:
            raise ValueError("session_id not found in ToolContext")

        controller = get_or_create_controller(session_id)

        # Navigate to starting URL if provided
        if starting_url:
            nav_result = controller.navigate(starting_url)
            if nav_result["status"] != "success":
                return {
                    "content": [{"text": f"**Navigation failed**: {nav_result.get('message', 'Unknown error')}"}],
                    "status": "error"
                }

        result = controller.act(instruction)

        status_emoji = "[OK]" if result["status"] == "success" else "[WARN]"

        # Format tab summary if multiple tabs
        tab_summary = _format_tab_summary(
            result.get('tabs', []),
            result.get('current_tab', 0)
        )
        tab_line = f"\n{tab_summary}" if tab_summary else ""

        nav_line = f"\n**Started at**: {starting_url}" if starting_url else ""
        content = [{
            "text": f"""{status_emoji} **Action executed**
{nav_line}
**Instruction**: {instruction}
**Result**: {result.get('message', 'Action completed')}
**Current URL**: {result.get('current_url', 'N/A')}
**Page Title**: {result.get('page_title', 'N/A')}{tab_line}

Current page state is shown in the screenshot below."""
        }]

        # Add screenshot as image content (raw bytes, like code interpreter)
        if result.get("screenshot"):
            content.append({
                "image": {
                    "format": "jpeg",
                    "source": {
                        "bytes": result["screenshot"]  # Raw bytes
                    }
                }
            })

        # Get browser session info for Live View
        metadata = {}
        if controller.browser_session_client and controller.browser_session_client.session_id:
            metadata["browserSessionId"] = controller.browser_session_client.session_id
            if controller.browser_id:
                metadata["browserId"] = controller.browser_id

        return {
            "content": content,
            "status": "success",  # Bedrock API requirement: only "success" or "error"
            "metadata": metadata
        }

    except Exception as e:
        logger.error(f"browser_act failed: {e}")
        return {
            "content": [{
                "text": f"**Action error**: {str(e)}\n\n**Instruction**: {instruction}"
            }],
            "status": "error"
        }



@tool(context=True)
def browser_get_page_info(
    url: Optional[str] = None,
    text: bool = False,
    tables: bool = False,
    links: bool = False,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Get page structure and DOM data - FAST, no AI needed.

    Returns page structure, interactive elements, and optional content extraction.

    Args:
        url: Optional URL to navigate to before getting page info.
             Use this to open a page and inspect it in a single call.
        text: If True, include full page text content
        tables: If True, extract and return all table data
        links: If True, return all links on the page (not just top 10 visible)

    Default output (always returned):
    - URL, title, scroll position, all open tabs
    - Interactive elements: buttons, links, input fields
    - Content summary: headings, image count, has_form, has_table
    - State: alerts, modals, loading indicators

    Returns JSON (no screenshot).
    """
    try:
        # Get session_id from ToolContext
        session_id = tool_context.invocation_state.get("session_id")
        if not session_id and hasattr(tool_context.agent, '_session_manager'):
            session_id = tool_context.agent._session_manager.session_id
            logger.info(f"[browser_get_page_info] Using session_id from agent._session_manager: {session_id}")
        elif session_id:
            logger.info(f"[browser_get_page_info] Using session_id from invocation_state: {session_id}")
        else:
            raise ValueError("session_id not found in ToolContext")

        controller = get_or_create_controller(session_id)

        # Navigate to URL if provided
        if url:
            nav_result = controller.navigate(url)
            if nav_result["status"] != "success":
                return {
                    "content": [{"text": f"**Navigation failed**: {nav_result.get('message', 'Unknown error')}"}],
                    "status": "error"
                }

        result = controller.get_page_info(text=text, tables=tables, all_links=links)

        if result["status"] == "success":
            import json

            # Format the structured data
            page_data = {
                "page": result["page"],
                "interactive": result["interactive"],
                "content": result["content"],
                "state": result["state"],
                "navigation": result["navigation"]
            }

            page_data_str = json.dumps(page_data, indent=2, ensure_ascii=False)

            # Build summary text
            page = result["page"]
            interactive = result["interactive"]
            content = result["content"]
            state = result["state"]

            summary_lines = []
            summary_lines.append(f"**URL**: {page['url']}")
            summary_lines.append(f"**Title**: {page['title']}")
            summary_lines.append(f"**Scroll**: {page['scroll']['percentage']}% ({page['scroll']['current']}/{page['scroll']['max']}px)")
            summary_lines.append("")

            # Interactive summary
            summary_lines.append("**Interactive Elements**:")
            summary_lines.append(f"- Buttons: {len(interactive['buttons'])} visible")
            summary_lines.append(f"- Links: {len(interactive['links'])} visible")
            summary_lines.append(f"- Inputs: {len(interactive['inputs'])} fields")
            summary_lines.append("")

            # Content summary
            summary_lines.append("**Content**:")
            summary_lines.append(f"- Headings: {len(content['headings'])}")
            summary_lines.append(f"- Images: {content['image_count']}")
            summary_lines.append(f"- Has form: {'Yes' if content['has_form'] else 'No'}")
            summary_lines.append(f"- Has table: {'Yes' if content['has_table'] else 'No'}")

            # State warnings
            if state['has_alerts']:
                summary_lines.append("")
                summary_lines.append(f"**Alerts detected**: {len(state['alert_messages'])}")
            if state['has_modals']:
                summary_lines.append("**Modal is open**")
            if state['has_loading']:
                summary_lines.append("⏳ **Page is loading**")

            # Add detailed tab information
            tabs = result.get('tabs', [])
            if tabs:
                summary_lines.append("")
                tab_details = _format_tab_list_detailed(tabs)
                summary_lines.append(tab_details)

            summary = "\n".join(summary_lines)

            # Build extra sections for optional data
            extra_sections = ""
            if text and content.get("text"):
                extra_sections += f"\n\n**Page Text**:\n{content['text'][:5000]}"
            if tables and content.get("tables"):
                tables_str = json.dumps(content["tables"], indent=2, ensure_ascii=False)
                extra_sections += f"\n\n**Tables**:\n```json\n{tables_str}\n```"
            if links:
                links_data = interactive.get("links", [])
                links_str = json.dumps(links_data, indent=2, ensure_ascii=False)
                extra_sections += f"\n\n**All Links** ({len(links_data)}):\n```json\n{links_str}\n```"

            response_content = [{
                "text": f"""**Page information collected**

{summary}

**Full Details**:
```json
{page_data_str}
```{extra_sections}"""
            }]

            # Get browser session info for Live View
            # Note: URL generation moved to BFF for on-demand refresh capability
            metadata = {}
            if controller.browser_session_client and controller.browser_session_client.session_id:
                metadata["browserSessionId"] = controller.browser_session_client.session_id
                if controller.browser_id:
                    metadata["browserId"] = controller.browser_id

            return _build_browser_response(response_content, metadata)
        else:
            return {
                "content": [{
                    "text": f"**Failed to get page info**\n\n{result.get('message', 'Unknown error')}"
                }],
                "status": "error"
            }

    except Exception as e:
        logger.error(f"browser_get_page_info failed: {e}")
        return {
            "content": [{
                "text": f"**Error getting page info**: {str(e)}"
            }],
            "status": "error"
        }


@tool(context=True)
def browser_manage_tabs(
    action: str,
    tab_index: Optional[int] = None,
    url: Optional[str] = None,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Manage browser tabs - switch between tabs, close a tab, or create a new tab.

    NOTE: To VIEW all open tabs, use browser_get_page_info() instead.
    This tool is for ACTIONS only (switch/close/create).

    Args:
        action: Action to perform on tabs
            - "switch": Switch to the tab at tab_index (makes it the active tab)
            - "close": Close the tab at tab_index
            - "create": Create a new tab and navigate to url

        tab_index: Tab index (0-based). Required for "switch" and "close" actions.
                   Use -1 for the last tab. Ignored for "create" action.

        url: URL to open in new tab. Required for "create" action.
             If not provided for "create", opens about:blank.

    Examples:
        - Switch to first tab: browser_manage_tabs(action="switch", tab_index=0)
        - Switch to last tab: browser_manage_tabs(action="switch", tab_index=-1)
        - Close second tab: browser_manage_tabs(action="close", tab_index=1)
        - Create new tab: browser_manage_tabs(action="create", url="https://google.com")

    Returns screenshot of the current active tab after the action.
    """
    try:
        # Get session_id from ToolContext
        session_id = tool_context.invocation_state.get("session_id")
        if not session_id and hasattr(tool_context.agent, '_session_manager'):
            session_id = tool_context.agent._session_manager.session_id
            logger.info(f"[browser_manage_tabs] Using session_id from agent._session_manager: {session_id}")
        elif session_id:
            logger.info(f"[browser_manage_tabs] Using session_id from invocation_state: {session_id}")
        else:
            raise ValueError("session_id not found in ToolContext")

        controller = get_or_create_controller(session_id)

        # Validate action
        valid_actions = ["switch", "close", "create"]
        if action not in valid_actions:
            return {
                "content": [{
                    "text": f"**Invalid action**: '{action}'. Must be one of: {', '.join(valid_actions)}\n\nTip:**Tip**: To view all tabs, use browser_get_page_info() instead."
                }],
                "status": "error"
            }

        # Handle each action
        if action == "switch":
            if tab_index is None:
                return {
                    "content": [{
                        "text": "**tab_index required** for 'switch' action. Example: browser_manage_tabs(action='switch', tab_index=0)"
                    }],
                    "status": "error"
                }

            result = controller.switch_tab(tab_index)

            if result["status"] == "success":
                tab_details = _format_tab_list_detailed(result.get('tabs', []))

                content = [{
                    "text": f"""**Switched to tab {result.get('current_tab', tab_index)}**

**URL**: {result.get('current_url', 'N/A')}
**Title**: {result.get('page_title', 'N/A')}

{tab_details}

Current tab screenshot shown below."""
                }]

                if result.get("screenshot"):
                    content.append({
                        "image": {
                            "format": "jpeg",
                            "source": {"bytes": result["screenshot"]}
                        }
                    })

                metadata = {}
                if controller.browser_session_client and controller.browser_session_client.session_id:
                    metadata["browserSessionId"] = controller.browser_session_client.session_id
                    if controller.browser_id:
                        metadata["browserId"] = controller.browser_id

                return _build_browser_response(content, metadata)
            else:
                return {
                    "content": [{
                        "text": f"**Switch failed**: {result.get('message', 'Unknown error')}"
                    }],
                    "status": "error"
                }

        elif action == "close":
            if tab_index is None:
                return {
                    "content": [{
                        "text": "**tab_index required** for 'close' action. Example: browser_manage_tabs(action='close', tab_index=1)"
                    }],
                    "status": "error"
                }

            result = controller.close_tab(tab_index)

            if result["status"] == "success":
                tab_details = _format_tab_list_detailed(result.get('tabs', []))

                content = [{
                    "text": f"""**Tab closed**

{result.get('message', 'Tab closed successfully')}

**Now on tab {result.get('current_tab', 0)}**:
**URL**: {result.get('current_url', 'N/A')}
**Title**: {result.get('page_title', 'N/A')}

{tab_details}

Current tab screenshot shown below."""
                }]

                if result.get("screenshot"):
                    content.append({
                        "image": {
                            "format": "jpeg",
                            "source": {"bytes": result["screenshot"]}
                        }
                    })

                metadata = {}
                if controller.browser_session_client and controller.browser_session_client.session_id:
                    metadata["browserSessionId"] = controller.browser_session_client.session_id
                    if controller.browser_id:
                        metadata["browserId"] = controller.browser_id

                return _build_browser_response(content, metadata)
            else:
                return {
                    "content": [{
                        "text": f"**Close failed**: {result.get('message', 'Unknown error')}"
                    }],
                    "status": "error"
                }

        elif action == "create":
            # URL is optional, defaults to about:blank
            create_url = url or "about:blank"

            result = controller.create_tab(create_url)

            if result["status"] == "success":
                tab_details = _format_tab_list_detailed(result.get('tabs', []))

                content = [{
                    "text": f"""**New tab created**

{result.get('message', 'Tab created successfully')}

**Now on tab {result.get('current_tab', 0)}**:
**URL**: {result.get('current_url', 'N/A')}
**Title**: {result.get('page_title', 'N/A')}

{tab_details}

Current tab screenshot shown below."""
                }]

                if result.get("screenshot"):
                    content.append({
                        "image": {
                            "format": "jpeg",
                            "source": {"bytes": result["screenshot"]}
                        }
                    })

                metadata = {}
                if controller.browser_session_client and controller.browser_session_client.session_id:
                    metadata["browserSessionId"] = controller.browser_session_client.session_id
                    if controller.browser_id:
                        metadata["browserId"] = controller.browser_id

                return _build_browser_response(content, metadata)
            else:
                return {
                    "content": [{
                        "text": f"**Create failed**: {result.get('message', 'Unknown error')}"
                    }],
                    "status": "error"
                }

    except Exception as e:
        logger.error(f"browser_manage_tabs failed: {e}")
        return {
            "content": [{
                "text": f"**Tab management error**: {str(e)}"
            }],
            "status": "error"
        }


@tool(context=True)
def browser_save_screenshot(filename: str, tool_context: ToolContext) -> Dict[str, Any]:
    """
    Save current browser screenshot to workspace for use in documents/reports.

    IMPORTANT: Browser screenshots are NOT automatically saved. Other browser tools
    (navigate, act, etc.) only display screenshots in chat - they don't persist them.
    You MUST call this tool to save a screenshot to workspace.

    When to use:
    - User wants to include browser screenshots in Word/PowerPoint documents
    - User asks to "capture" or "save" what's on the browser screen
    - Creating visual evidence or documentation of web pages

    Args:
        filename: Image filename (e.g., "search-results.png", "product-page.jpg")
                 Must end with .png, .jpg, or .jpeg

    Returns text confirmation with saved location. The saved image can then be
    referenced by filename in document tools (Word, Excel, PowerPoint).
    """
    try:
        # Validate filename
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            return {
                "content": [{
                    "text": "**Invalid filename**: Must end with .png, .jpg, or .jpeg"
                }],
                "status": "error"
            }

        # Get session_id from ToolContext
        session_id = tool_context.invocation_state.get("session_id")
        if not session_id and hasattr(tool_context.agent, '_session_manager'):
            session_id = tool_context.agent._session_manager.session_id
            logger.info(f"[browser_save_screenshot] Using session_id from agent._session_manager: {session_id}")
        elif session_id:
            logger.info(f"[browser_save_screenshot] Using session_id from invocation_state: {session_id}")
        else:
            raise ValueError("session_id not found in ToolContext")

        user_id = tool_context.invocation_state.get("user_id")
        if not user_id:
            raise ValueError("user_id not found in ToolContext")

        # Get current browser controller
        controller = get_or_create_controller(session_id)

        # Ensure browser is connected
        if not controller._connected:
            controller.connect()

        # Get current page info for context
        page_info = controller.get_page_info()
        if page_info.get("status") != "success":
            return {
                "content": [{
                    "text": "**Failed to capture screenshot**: Browser not ready"
                }],
                "status": "error"
            }

        # Take screenshot using controller's method
        screenshot_bytes = controller._take_screenshot()

        if not screenshot_bytes:
            return {
                "content": [{
                    "text": "**No screenshot data available**"
                }],
                "status": "error"
            }

        # Save to workspace using ImageManager
        from workspace import ImageManager

        image_manager = ImageManager(user_id=user_id, session_id=session_id)
        image_manager.save_to_s3(filename, screenshot_bytes)

        # Get current page info for context
        current_url = page_info.get("page", {}).get("url", "Unknown")
        current_title = page_info.get("page", {}).get("title", "Untitled")

        return {
            "content": [{
                "text": f"""**Screenshot saved to workspace**

**Filename**: {filename}
**Source**: {current_title}
**URL**: {current_url}

This image is now available in workspace and can be referenced by filename in document tools."""
            }],
            "status": "success"
        }

    except Exception as e:
        logger.error(f"browser_save_screenshot failed: {e}")
        return {
            "content": [{
                "text": f"**Screenshot save error**: {str(e)}"
            }],
            "status": "error"
        }


# --- Skill registration ---
register_skill("browser-automation", tools=[browser_act, browser_get_page_info, browser_manage_tabs, browser_save_screenshot])
