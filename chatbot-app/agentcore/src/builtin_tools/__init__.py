"""Lazy exports for built-in AWS-powered tools.

Focused runtimes such as the general subagent only import Code Interpreter
tools. Lazy loading prevents those runtimes from importing browser and Office
dependencies that are not part of their capability profile.
"""

from importlib import import_module

_EXPORTS = {
    "generate_chart": (".diagram_tool", "generate_chart"),
    "create_visual_design": (".diagram_tool", "create_visual_design"),
    "browser_act": (".nova_act_browser_tools", "browser_act"),
    "browser_get_page_info": (
        ".nova_act_browser_tools",
        "browser_get_page_info",
    ),
    "browser_manage_tabs": (".nova_act_browser_tools", "browser_manage_tabs"),
    "browser_save_screenshot": (
        ".nova_act_browser_tools",
        "browser_save_screenshot",
    ),
    "create_word_document": (".word_document_tool", "create_word_document"),
    "modify_word_document": (".word_document_tool", "modify_word_document"),
    "list_my_word_documents": (
        ".word_document_tool",
        "list_my_word_documents",
    ),
    "read_word_document": (".word_document_tool", "read_word_document"),
    "preview_word_page": (".word_document_tool", "preview_word_page"),
    "create_excel_spreadsheet": (
        ".excel_spreadsheet_tool",
        "create_excel_spreadsheet",
    ),
    "modify_excel_spreadsheet": (
        ".excel_spreadsheet_tool",
        "modify_excel_spreadsheet",
    ),
    "list_my_excel_spreadsheets": (
        ".excel_spreadsheet_tool",
        "list_my_excel_spreadsheets",
    ),
    "read_excel_spreadsheet": (
        ".excel_spreadsheet_tool",
        "read_excel_spreadsheet",
    ),
    "preview_excel_sheets": (
        ".excel_spreadsheet_tool",
        "preview_excel_sheets",
    ),
    "get_slide_design_reference": (
        ".powerpoint_presentation_tool",
        "get_slide_design_reference",
    ),
    "list_my_powerpoint_presentations": (
        ".powerpoint_presentation_tool",
        "list_my_powerpoint_presentations",
    ),
    "get_presentation_layouts": (
        ".powerpoint_presentation_tool",
        "get_presentation_layouts",
    ),
    "analyze_presentation": (
        ".powerpoint_presentation_tool",
        "analyze_presentation",
    ),
    "create_presentation": (
        ".powerpoint_presentation_tool",
        "create_presentation",
    ),
    "update_slide_content": (
        ".powerpoint_presentation_tool",
        "update_slide_content",
    ),
    "add_slide": (".powerpoint_presentation_tool", "add_slide"),
    "delete_slides": (".powerpoint_presentation_tool", "delete_slides"),
    "move_slide": (".powerpoint_presentation_tool", "move_slide"),
    "duplicate_slide": (".powerpoint_presentation_tool", "duplicate_slide"),
    "update_slide_notes": (
        ".powerpoint_presentation_tool",
        "update_slide_notes",
    ),
    "preview_presentation_slides": (
        ".powerpoint_presentation_tool",
        "preview_presentation_slides",
    ),
    "execute_code": (".code_interpreter_tool", "execute_code"),
    "execute_command": (".code_interpreter_tool", "execute_command"),
    "file_operations": (".code_interpreter_tool", "file_operations"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name == "BUILTIN_TOOLS":
        tools = [__getattr__(tool_name) for tool_name in __all__]
        globals()[name] = tools
        return tools
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
