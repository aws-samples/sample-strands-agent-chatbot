"""
PowerPoint Presentation Tools

Tools for creating and editing PowerPoint presentations.
- create_presentation: Creates new presentations using PptxGenJS (JavaScript) via Code Interpreter (Deno)
- All editing tools: Direct XML manipulation via PptxEngine (no Code Interpreter required)
"""

import json
import hashlib
import logging
import math
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Dict, Any

from strands import tool, ToolContext
from skill import register_skill
from workspace import PowerPointManager

from .lib.ppt_utils import (
    validate_presentation_name,
    get_user_session_ids,
    save_ppt_artifact,
    get_file_compatibility_error,
)
from .lib.tool_response import build_success_response, build_image_response
from .lib.pptx_engine import PptxEngine
from .lib.pptxgenjs_runner import run_pptxgenjs

logger = logging.getLogger(__name__)

# Backward compatibility aliases
_validate_presentation_name = validate_presentation_name
_get_user_session_ids = get_user_session_ids
_save_ppt_artifact = save_ppt_artifact
_get_file_compatibility_error_response = get_file_compatibility_error


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_or_error(ppt_manager: PowerPointManager, filename: str):
    """Load bytes from S3 or return an error response dict."""
    try:
        return ppt_manager.load_from_s3(filename), None
    except FileNotFoundError:
        docs = ppt_manager.list_s3_documents()
        available = [
            d["filename"]
            for d in docs
            if d["filename"].lower().endswith(".pptx")
        ]
        msg = f"**Presentation not found**: {filename}"
        if available:
            msg += "\n\n**Available:**\n" + "\n".join(f"- {f}" for f in available)
        return None, {"content": [{"text": msg}], "status": "error"}


def _existing_presentation_filename(presentation_name: str) -> str:
    """Preserve the exact uploaded filename while rejecting unsafe paths."""
    name = presentation_name.strip()
    if name.lower().endswith(".pptx"):
        filename = name
    else:
        filename = f"{name}.pptx"
    if (
        not name
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ValueError("Invalid existing presentation name")
    return filename


def _validate_slide_updates(slide_updates: list, slide_count: int) -> None:
    """Validate the complete edit batch before mutating the package."""
    allowed_actions = {
        "set_text": {"text"},
        "replace_text": {"find", "replace"},
        "replace_image": {"image_name"},
    }
    seen_slides = set()
    for update_index, update in enumerate(slide_updates):
        if not isinstance(update, dict):
            raise ValueError(f"slide_updates[{update_index}] must be an object")
        slide_index = update.get("slide_index")
        if not isinstance(slide_index, int) or not 0 <= slide_index < slide_count:
            raise ValueError(
                f"slide_index {slide_index!r} out of range (0-{slide_count - 1})"
            )
        if slide_index in seen_slides:
            raise ValueError(
                f"slide_index {slide_index} appears more than once; combine its operations"
            )
        seen_slides.add(slide_index)

        operations = update.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError(
                f"slide_updates[{update_index}].operations must be a non-empty list"
            )
        for operation_index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                raise ValueError(
                    f"slide_updates[{update_index}].operations[{operation_index}] "
                    "must be an object"
                )
            action = operation.get("action")
            if action not in allowed_actions:
                raise ValueError(
                    f"Unsupported action {action!r}; allowed: "
                    f"{sorted(allowed_actions)}"
                )
            element_id = operation.get("element_id")
            if not isinstance(element_id, int) or element_id < 0:
                raise ValueError("element_id must be a non-negative integer")
            for field in allowed_actions[action]:
                if not isinstance(operation.get(field), str):
                    raise ValueError(f"{action}.{field} must be a string")
            if action == "replace_text" and not operation["find"]:
                raise ValueError("replace_text.find must not be empty")
            if action == "replace_image" and not operation["image_name"]:
                raise ValueError("replace_image.image_name must not be empty")


def _available_system_fonts() -> set[str] | None:
    try:
        result = subprocess.run(
            ["fc-list", "--format", "%{family}\n"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    fonts = set()
    for line in result.stdout.splitlines():
        fonts.update(
            name.strip().casefold() for name in line.split(",") if name.strip()
        )
    return fonts


def _is_current_deck_spec(spec: Any, source_sha256: str) -> bool:
    required_keys = {
        "schema_version",
        "source_sha256",
        "slide_size",
        "theme",
        "explicit_fonts",
        "layouts",
        "slides",
    }
    return (
        isinstance(spec, dict)
        and required_keys.issubset(spec)
        and spec["schema_version"] == 1
        and spec["source_sha256"] == source_sha256
        and isinstance(spec["theme"], dict)
        and isinstance(spec["slides"], list)
    )


def _save_and_respond(
    ppt_manager, tool_context, output_filename, output_bytes,
    tool_name, user_id, session_id, success_msg, extra_meta=None
):
    """Save bytes to S3, register artifact, return success response."""
    s3_info = ppt_manager.save_to_s3(output_filename, output_bytes)
    _save_ppt_artifact(
        tool_context=tool_context,
        filename=output_filename,
        s3_url=s3_info["s3_url"],
        size_kb=s3_info["size_kb"],
        tool_name=tool_name,
        user_id=user_id,
        session_id=session_id,
    )
    meta = {
        "filename": output_filename,
        "s3_url": s3_info["s3_url"],
        "size_kb": s3_info["size_kb"],
        "tool_type": "powerpoint_presentation",
        "user_id": user_id,
        "session_id": session_id,
    }
    if extra_meta:
        meta.update(extra_meta)
    return build_success_response(success_msg, meta)


def _load_edit_or_error(ppt_manager: PowerPointManager, edit_id: str):
    try:
        draft_bytes, edit_state = ppt_manager.load_edit(edit_id)
        return draft_bytes, edit_state, None
    except FileNotFoundError:
        return None, None, {
            "content": [{
                "text": (
                    f"**Edit draft not found**: {edit_id}\n\n"
                    "Call `begin_presentation_edit` for the source presentation."
                )
            }],
            "status": "error",
        }


def _save_edit_and_respond(
    ppt_manager: PowerPointManager,
    edit_id: str,
    edit_state: Dict[str, Any],
    output_bytes: bytes,
    success_msg: str,
    extra_meta: Dict[str, Any] | None = None,
):
    """Conditionally replace one hidden draft without creating an artifact."""
    draft_etag = ppt_manager.save_edit(
        edit_id,
        output_bytes,
        edit_state["draft_etag"],
    )
    metadata = {
        "edit_id": edit_id,
        "draft_etag": draft_etag,
        "source_filename": edit_state["source_filename"],
        "tool_type": "powerpoint_edit_draft",
    }
    if extra_meta:
        metadata.update(extra_meta)
    return build_success_response(success_msg, metadata)


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def get_slide_design_reference(topic: str = "all") -> Dict[str, Any]:
    """Get compact design-system guidance for new PptxGenJS presentations.

    Use this only for new decks or intentional redesigns. Existing decks should
    derive their design system with inspect_presentation instead.

    Args:
        topic: "colors" | "typography" | "layouts" | "pitfalls" | "all"
    """
    guidelines = {
        "colors": """## Color System

Derive colors from the subject, brand assets, and intended use. Define semantic
tokens before drawing: background, surface, text, muted text, primary accent, and
at most one signal color. Use a neutral-dominant system with strong contrast.

Rules:
- Reuse identical tokens across slides and charts
- Encode meaning with labels or shape as well as color
- Use light and dark surfaces only when they support hierarchy
- Verify foreground/background contrast after rendering
- PptxGenJS hex values are six digits without `#`
- Use transparency/opacity properties, never eight-digit color strings""",

        "typography": """## Typography System

Use fonts installed in the target environment. Define one heading face, one body
face, and a small reusable type scale. Preserve established sizes for repeated
slide functions.

Rules:
- Use takeaway titles when the slide makes an argument
- Left-align paragraphs; center only intentionally grouped content
- Keep body text readable at presentation distance
- Split dense slides instead of shrinking below the established body size
- Use `charSpacing`, not `letterSpacing`
- Use `breakLine: true` between rich-text array lines
- Validate missing fonts and inspect renderer substitution""",

        "layouts": """## Functional Layouts

Choose geometry by slide function:
- Opening: literal title, restrained visual, minimal metadata
- Context: annotated image, map, timeline, or compact evidence
- Argument: one claim with two or three supporting proof points
- Data: one chart with a takeaway title and direct labels
- Comparison: aligned columns with a consistent comparison basis
- Process: ordered stages with direction and ownership
- Decision: options, criteria, recommendation, and consequence
- Closing: decision/request and next action

Use masters for repeated chrome and repeat exact grid coordinates for repeated
functions. Vary composition only when content needs a different function.""",

        "pitfalls": """## Common Mistakes to Avoid

1. Starting slide code before writing the narrative and slide plan
2. Using generic palette recipes unrelated to the subject or source
3. Treating decorative shapes as meaningful visuals
4. Reusing option objects across calls; PptxGenJS mutates them
5. Using `#` or eight-digit opacity hex values
6. Using Unicode bullet characters instead of `bullet: true`
7. Shrinking text to fit instead of editing or splitting content
8. Stretching images instead of cropping or containing them
9. Trusting successful generation without structural validation and rendering
10. Repeating a layout regardless of each slide's functional purpose""",
    }

    if topic == "all":
        content = "\n\n".join(guidelines.values())
    elif topic in guidelines:
        content = guidelines[topic]
    else:
        return {"content": [{"text": f"Unknown topic: {topic}. Available: {list(guidelines.keys())} or 'all'"}], "status": "error"}

    return build_success_response(content, {"topic": topic})


@tool(context=True)
def list_my_powerpoint_presentations(tool_context: ToolContext) -> Dict[str, Any]:
    """List all PowerPoint presentations in workspace.

    Returns:
        Formatted list of presentations with metadata
    """
    try:
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)
        documents = [
            document
            for document in ppt_manager.list_s3_documents()
            if document["filename"].lower().endswith(".pptx")
        ]
        workspace_list = ppt_manager.format_file_list(documents)
        return build_success_response(workspace_list, {
            "count": len(documents),
            "presentations": [doc["filename"] for doc in documents],
        })
    except Exception as e:
        logger.error(f"list_my_powerpoint_presentations error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error listing presentations:** {str(e)}"}], "status": "error"}


@tool(context=True)
def begin_presentation_edit(
    presentation_name: str,
    tool_context: ToolContext,
    restart: bool = False,
) -> Dict[str, Any]:
    """Open one hidden, reusable draft for a source presentation.

    Uploaded sources remain immutable. Repeated calls for an unchanged source
    return the existing draft unless ``restart`` is true.

    Args:
        presentation_name: Exact source filename from the PowerPoint list; the
            .pptx extension is optional
        restart: Discard the current draft and restart from the source
    """
    try:
        filename = _existing_presentation_filename(presentation_name)
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)
        edit_state = ppt_manager.begin_edit(filename, restart=restart)
        action = "Resumed" if edit_state["reused"] else "Opened"
        return build_success_response(
            (
                f"**{action} PowerPoint edit**: {filename}\n\n"
                f"- Edit ID: `{edit_state['edit_id']}`\n"
                "- The uploaded source remains unchanged.\n"
                "- Use this edit ID for every mutation, validation, and preview."
            ),
            {
                "edit_id": edit_state["edit_id"],
                "source_filename": filename,
                "reused": edit_state["reused"],
                "tool_type": "powerpoint_edit_draft",
                "user_id": user_id,
                "session_id": session_id,
            },
        )
    except Exception as e:
        logger.error("begin_presentation_edit error: %s", e, exc_info=True)
        return {
            "content": [{"text": f"**Error opening presentation edit:** {str(e)}"}],
            "status": "error",
        }


@tool(context=True)
def finalize_presentation_edit(
    edit_id: str,
    output_name: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Validate a hidden draft and publish exactly one PowerPoint artifact.

    Args:
        edit_id: Edit ID returned by begin_presentation_edit
        output_name: Final presentation name WITHOUT extension
    """
    try:
        valid_name, error_msg = _validate_presentation_name(output_name)
        if not valid_name:
            return {
                "content": [{
                    "text": f"**Invalid output name**: {output_name}\n\n{error_msg}"
                }],
                "status": "error",
            }

        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)
        draft_bytes, edit_state, err = _load_edit_or_error(ppt_manager, edit_id)
        if err:
            return err

        output_filename = f"{output_name}.pptx"
        if output_filename == edit_state["source_filename"]:
            return {
                "content": [{
                    "text": (
                        "**Output name must differ from the immutable source**\n\n"
                        f"Source: {edit_state['source_filename']}"
                    )
                }],
                "status": "error",
            }
        try:
            ppt_manager.resolve_presentation(output_filename)
            return {
                "content": [{
                    "text": (
                        f"**Already exists**: {output_filename}\n\n"
                        "Choose a final output name that is not already in Workspace."
                    )
                }],
                "status": "error",
            }
        except FileNotFoundError:
            pass

        with PptxEngine(draft_bytes) as engine:
            validation = engine.validate()
        if not validation["valid"]:
            return {
                "content": [{
                    "text": (
                        "**Draft failed structural validation**\n\n"
                        f"Errors: {validation['error_count']}. "
                        "Fix the draft before finalizing."
                    )
                }],
                "status": "error",
                "metadata": {"edit_id": edit_id, "validation": validation},
            }

        success_msg = (
            f"**Finalized**: {output_filename}\n\n"
            f"Published from `{edit_id}` with "
            f"{validation['warning_count']} review warning(s)."
        )
        response = _save_and_respond(
            ppt_manager,
            tool_context,
            output_filename,
            draft_bytes,
            "finalize_presentation_edit",
            user_id,
            session_id,
            success_msg,
            {"edit_id": edit_id, "validation": validation},
        )
        try:
            ppt_manager.discard_edit(edit_id)
        except Exception as cleanup_error:
            logger.warning(
                "Published %s but could not remove draft %s: %s",
                output_filename,
                edit_id,
                cleanup_error,
            )
        return response
    except Exception as e:
        logger.error("finalize_presentation_edit error: %s", e, exc_info=True)
        return {
            "content": [{"text": f"**Error finalizing presentation:** {str(e)}"}],
            "status": "error",
        }


@tool(context=True)
def discard_presentation_edit(
    edit_id: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Discard a hidden PowerPoint draft without changing its source."""
    try:
        user_id, session_id = _get_user_session_ids(tool_context)
        PowerPointManager(user_id, session_id).discard_edit(edit_id)
        return build_success_response(
            f"**Discarded PowerPoint edit**: `{edit_id}`",
            {"edit_id": edit_id, "tool_type": "powerpoint_edit_draft"},
        )
    except Exception as e:
        logger.error("discard_presentation_edit error: %s", e, exc_info=True)
        return {
            "content": [{"text": f"**Error discarding presentation edit:** {str(e)}"}],
            "status": "error",
        }


@tool(context=True)
def inspect_presentation(
    presentation_name: str,
    tool_context: ToolContext,
    persist_spec: bool = True,
    refresh_spec: bool = False,
) -> Dict[str, Any]:
    """Inspect source structure and derive a compact, reusable deck specification.

    Run this before editing an existing presentation or template. The source must
    exist in the session PowerPoint workspace; this tool never creates a substitute.

    Args:
        presentation_name: Exact presentation filename; extension optional
        persist_spec: Store the derived deck spec beside the source for later turns
        refresh_spec: Ignore a matching persisted spec and inspect the package again
    """
    try:
        filename = _existing_presentation_filename(presentation_name)
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)
        source_bytes, err = _load_or_error(ppt_manager, filename)
        if err:
            return err

        spec_filename = f".deck-spec-{filename}.json"
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        spec = None
        spec_source = "generated"
        if persist_spec and not refresh_spec:
            try:
                persisted_spec = json.loads(
                    ppt_manager.load_from_s3(spec_filename).decode("utf-8")
                )
                if _is_current_deck_spec(persisted_spec, source_sha256):
                    spec = persisted_spec
                    spec_source = "persisted"
            except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
                pass

        if spec is None:
            with PptxEngine(source_bytes) as engine:
                spec = engine.get_deck_spec()
            spec.update({
                "source_filename": filename,
                "source_sha256": source_sha256,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })

        if persist_spec and spec_source == "generated":
            ppt_manager.save_to_s3(
                spec_filename,
                json.dumps(spec, indent=2, ensure_ascii=True).encode("utf-8"),
                metadata={"type": "deck_spec", "source": filename},
            )

        summary = (
            f"**Inspected source**: {filename}\n\n"
            f"- Slides: {len(spec['slides'])}\n"
            f"- Layouts: {len(spec['layouts'])}\n"
            f"- Size: {spec['slide_size']['width_inches']} × "
            f"{spec['slide_size']['height_inches']} inches\n"
            f"- Theme: {spec['theme'].get('name') or 'unnamed'}\n"
            f"- Explicit fonts: "
            f"{', '.join(spec['explicit_fonts']) or 'none'}\n"
            f"- Spec source: {spec_source}"
        )
        if persist_spec:
            summary += f"\n- Persisted spec: {spec_filename}"
        return build_success_response(summary, {
            "filename": filename,
            "deck_spec_filename": spec_filename if persist_spec else None,
            "deck_spec_source": spec_source,
            "deck_spec": spec,
            "tool_type": "powerpoint_presentation",
            "user_id": user_id,
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"inspect_presentation error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error inspecting presentation:** {str(e)}"}], "status": "error"}


@tool(context=True)
def validate_presentation(
    presentation_name: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Validate PPTX package integrity, geometry, placeholders, and font availability.

    Structural errors are deterministic. Geometry and overflow findings are
    conservative warnings and must be confirmed with rendered previews.

    Args:
        presentation_name: Exact presentation filename; extension optional
    """
    try:
        filename = _existing_presentation_filename(presentation_name)
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)
        source_bytes, err = _load_or_error(ppt_manager, filename)
        if err:
            return err

        with PptxEngine(source_bytes) as engine:
            report = engine.validate()
            if report["valid"]:
                deck_spec = engine.get_deck_spec()
                required_fonts = set(deck_spec["explicit_fonts"])
                required_fonts.update(
                    font
                    for font in deck_spec["theme"]["fonts"].values()
                    if font
                )
                explicit_fonts = sorted(required_fonts, key=str.casefold)
            else:
                explicit_fonts = []

        available_fonts = _available_system_fonts()
        if available_fonts is None:
            report["font_check"] = {
                "status": "unavailable",
                "message": "System font catalog is unavailable; verify fonts in PowerPoint.",
            }
        else:
            missing_fonts = [
                font for font in explicit_fonts
                if font.casefold() not in available_fonts
            ]
            report["font_check"] = {
                "status": "warning" if missing_fonts else "ok",
                "explicit_fonts": explicit_fonts,
                "missing_fonts": missing_fonts,
                "message": (
                    "Missing fonts may be substituted by the renderer."
                    if missing_fonts
                    else "All explicitly named fonts are installed."
                ),
            }
            if missing_fonts:
                report["warning_count"] += 1
                report["warnings"].append({
                    "code": "missing_fonts",
                    "fonts": missing_fonts,
                    "message": "Missing fonts may be substituted by the renderer.",
                })

        status = "passed" if report["valid"] else "failed"
        summary = (
            f"**Validation {status}**: {filename}\n\n"
            f"- Structural errors: {report['error_count']}\n"
            f"- Review warnings: {report['warning_count']}\n"
            f"- Font check: {report['font_check']['status']}\n\n"
            "Render and visually inspect affected slides before delivery."
        )
        response = build_success_response(summary, {
            "filename": filename,
            "validation": report,
            "tool_type": "powerpoint_presentation",
            "user_id": user_id,
            "session_id": session_id,
        })
        if not report["valid"]:
            response["status"] = "error"
        return response
    except Exception as e:
        logger.error(f"validate_presentation error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error validating presentation:** {str(e)}"}], "status": "error"}


@tool(context=True)
def get_presentation_layouts(
    presentation_name: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Get all available slide layouts from a presentation.

    Returns layout names to use with add_slide. Call this before adding slides.

    Args:
        presentation_name: Exact presentation filename; extension optional
    """
    try:
        filename = _existing_presentation_filename(presentation_name)
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, err = _load_or_error(ppt_manager, filename)
        if err:
            return err

        with PptxEngine(source_bytes) as engine:
            layouts = engine.get_layouts()

        text = f"**Available Layouts**: {filename}\n\n**Total:** {len(layouts)}\n\n"
        text += "\n".join(f'- "{l["name"]}" (index {l["index"]}, {l["placeholder_count"]} placeholders)' for l in layouts)  # noqa: E741

        return build_success_response(text, {
            "filename": filename,
            "layouts": layouts,
            "tool_type": "powerpoint_presentation",
            "user_id": user_id,
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"get_presentation_layouts error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error getting layouts:** {str(e)}"}], "status": "error"}


@tool(context=True)
def analyze_presentation(
    presentation_name: str,
    tool_context: ToolContext,
    slide_index: int | None = None,
    include_notes: bool = False,
) -> Dict[str, Any]:
    """Analyze presentation structure: element IDs, positions, text content.

    Element Types: text | picture | table | chart | group | unknown
    Role Tags: [TITLE] [BODY] [SUBTITLE] [FOOTER] (empty = regular shape)

    Args:
        presentation_name: Exact presentation filename; extension optional
        slide_index: Optional 0-based slide index. None = analyze all slides.
        include_notes: Include speaker notes in output (default False)
    """
    try:
        filename = _existing_presentation_filename(presentation_name)
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, err = _load_or_error(ppt_manager, filename)
        if err:
            return err

        with PptxEngine(source_bytes) as engine:
            order = engine.get_slide_order()

            if slide_index is not None:
                if not (0 <= slide_index < len(order)):
                    return {"content": [{"text": f"**Invalid slide_index {slide_index}**: presentation has {len(order)} slides (0-{len(order)-1})"}], "status": "error"}
                targets = [(slide_index, order[slide_index]["filename"])]
            else:
                targets = [(i, s["filename"]) for i, s in enumerate(order)]

            slides_data = []
            for idx, slide_filename in targets:
                info = engine.analyze_slide(slide_filename, include_notes)
                slides_data.append({
                    "slide_index": idx,
                    "title": info.get("title"),
                    "elements": info.get("elements", []),
                    **({"notes": info.get("notes", "")} if include_notes else {}),
                })

        analysis = {"total_slides": len(order), "slides": slides_data}

        # Format output text
        if slide_index is not None:
            output_text = f"**Slide Analysis**: {filename} — Slide {slide_index + 1}\n\n"
        else:
            output_text = f"**Presentation Analysis**: {filename}\n\n**Total slides:** {len(order)}\n\n"

        for slide in slides_data:
            output_text += f"### Slide {slide['slide_index'] + 1}"
            if slide.get("title"):
                output_text += f": {slide['title']}"
            output_text += "\n"
            for elem in slide["elements"]:
                role_tag = f" [{elem['role']}]" if elem.get("role") else ""
                preview = (elem.get("text") or "")[:80].replace("\n", " ↵ ")
                output_text += (
                    f"  - id={elem['id']} type={elem['type']}{role_tag}"
                    f" pos=({elem['position']['left']}\", {elem['position']['top']}\")"
                    f"{f': {preview}' if preview else ''}\n"
                )
            if include_notes and slide.get("notes"):
                output_text += f"  📝 Notes: {slide['notes'][:100]}\n"
            output_text += "\n"

        return build_success_response(output_text, {
            "filename": filename,
            "analysis": analysis,
            "tool_type": "powerpoint_presentation",
            "user_id": user_id,
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"analyze_presentation error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error analyzing presentation:** {str(e)}"}], "status": "error"}


@tool(context=True)
def update_slide_content(
    edit_id: str,
    slide_updates: list,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Update one or more slides with operations in a single call.

    Args:
        edit_id: Edit ID returned by begin_presentation_edit
        slide_updates: List of slide update dicts:
            [
                {
                    "slide_index": int,  # 0-based
                    "operations": [
                        {"action": "set_text",     "element_id": int, "text": str},
                        {"action": "replace_text", "element_id": int, "find": str, "replace": str},
                        {"action": "replace_image","element_id": int, "image_name": str},
                    ]
                }
            ]
    Notes:
        - set_text: Multi-line text via \\n creates multiple paragraphs
        - replace_image: image_name is a filename from your image workspace (S3)
        - Batch all changes into ONE call to avoid parallel data loss
    """
    try:
        if not slide_updates or not isinstance(slide_updates, list):
            return {"content": [{"text": "**Invalid slide_updates**: must be a non-empty list"}], "status": "error"}

        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, edit_state, err = _load_edit_or_error(ppt_manager, edit_id)
        if err:
            return err

        working_bytes = source_bytes
        with PptxEngine(working_bytes) as engine:
            order = engine.get_slide_order()
            _validate_slide_updates(slide_updates, len(order))
            for update in slide_updates:
                idx = update["slide_index"]
                slide_filename = order[idx]["filename"]
                for op in update.get("operations", []):
                    action = op.get("action")
                    eid = op.get("element_id")
                    if action == "set_text":
                        engine.set_text(slide_filename, eid, op["text"])
                    elif action == "replace_text":
                        engine.replace_text(slide_filename, eid, op["find"], op["replace"])
                    elif action == "replace_image":
                        image_name = op.get("image_name", "")
                        from workspace import ImageManager
                        img_manager = ImageManager(user_id, session_id)
                        img_bytes = img_manager.load_from_s3(image_name)
                        ext = image_name.rsplit(".", 1)[-1].lower() if "." in image_name else "png"
                        engine.replace_image(slide_filename, eid, img_bytes, ext)
            working_bytes = engine.pack()

        total_ops = sum(len(u.get("operations", [])) for u in slide_updates)
        success_msg = (
            f"**Updated draft**: `{edit_id}`\n\n"
            f"Applied {total_ops} operation(s) across {len(slide_updates)} slide(s)."
        )
        return _save_edit_and_respond(
            ppt_manager, edit_id, edit_state, working_bytes, success_msg,
            {"slide_count": len(slide_updates), "operation_count": total_ops},
        )
    except Exception as e:
        logger.error(f"update_slide_content error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


@tool(context=True)
def add_slide(
    edit_id: str,
    layout_name: str,
    position: int,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Add a new blank slide at the given position.

    Use get_presentation_layouts() to get exact layout names.
    Use update_slide_content() afterwards to populate the slide with content.

    Args:
        edit_id: Edit ID returned by begin_presentation_edit
        layout_name: Exact layout name from get_presentation_layouts()
        position: Insert position (0-based). Use -1 to append at end.
    """
    try:
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, edit_state, err = _load_edit_or_error(ppt_manager, edit_id)
        if err:
            return err

        with PptxEngine(source_bytes) as engine:
            new_slide = engine.add_slide(layout_name, position)
            order = engine.get_slide_order()
            new_index = next(i for i, s in enumerate(order) if s["filename"] == new_slide)
            output_bytes = engine.pack()

        success_msg = (
            f"**Added slide to draft**: `{edit_id}`\n\n"
            f"Layout: \"{layout_name}\" → Slide {new_index + 1} (index {new_index})\n\n"
            f"Use `update_slide_content` with slide_index={new_index} to add content."
        )
        return _save_edit_and_respond(
            ppt_manager, edit_id, edit_state, output_bytes, success_msg,
            {"new_slide_index": new_index, "layout_name": layout_name},
        )
    except Exception as e:
        logger.error(f"add_slide error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


@tool(context=True)
def delete_slides(
    edit_id: str,
    slide_indices: list,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Delete slides by 0-based indices.

    Args:
        edit_id: Edit ID returned by begin_presentation_edit
        slide_indices: List of 0-based indices to delete (e.g., [2, 5, 10])
    """
    try:
        if not slide_indices or not isinstance(slide_indices, list):
            return {"content": [{"text": "**Invalid slide_indices**: must be a non-empty list"}], "status": "error"}

        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, edit_state, err = _load_edit_or_error(ppt_manager, edit_id)
        if err:
            return err

        with PptxEngine(source_bytes) as engine:
            total_before = len(engine.get_slide_order())
            engine.delete_slides(slide_indices)
            total_after = len(engine.get_slide_order())
            output_bytes = engine.pack()

        success_msg = (
            f"**Deleted slides from draft**: `{edit_id}`\n\n"
            f"Removed {total_before - total_after} slide(s). "
            f"Remaining: {total_after}"
        )
        return _save_edit_and_respond(
            ppt_manager, edit_id, edit_state, output_bytes, success_msg,
            {"deleted_count": total_before - total_after, "remaining_count": total_after},
        )
    except Exception as e:
        logger.error(f"delete_slides error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


@tool(context=True)
def move_slide(
    edit_id: str,
    from_index: int,
    to_index: int,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Move a slide from one position to another.

    Args:
        edit_id: Edit ID returned by begin_presentation_edit
        from_index: Source position (0-based)
        to_index: Target position (0-based)
    """
    try:
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, edit_state, err = _load_edit_or_error(ppt_manager, edit_id)
        if err:
            return err

        with PptxEngine(source_bytes) as engine:
            engine.move_slide(from_index, to_index)
            output_bytes = engine.pack()

        success_msg = (
            f"**Moved slide in draft**: `{edit_id}`\n\n"
            f"Slide {from_index + 1} → position {to_index + 1}"
        )
        return _save_edit_and_respond(
            ppt_manager, edit_id, edit_state, output_bytes, success_msg,
            {"from_index": from_index, "to_index": to_index},
        )
    except Exception as e:
        logger.error(f"move_slide error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


@tool(context=True)
def duplicate_slide(
    edit_id: str,
    source_index: int,
    tool_context: ToolContext,
    insert_position: int = -1,
) -> Dict[str, Any]:
    """Duplicate an existing slide.

    Args:
        edit_id: Edit ID returned by begin_presentation_edit
        source_index: Slide to duplicate (0-based)
        insert_position: Where to insert duplicate (0-based, -1 = append after source)
    """
    try:
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, edit_state, err = _load_edit_or_error(ppt_manager, edit_id)
        if err:
            return err

        position = insert_position if insert_position >= 0 else source_index + 1

        with PptxEngine(source_bytes) as engine:
            new_slide = engine.duplicate_slide(source_index, position)
            order = engine.get_slide_order()
            new_index = next(i for i, s in enumerate(order) if s["filename"] == new_slide)
            output_bytes = engine.pack()

        success_msg = (
            f"**Duplicated slide in draft**: `{edit_id}`\n\n"
            f"Slide {source_index + 1} → new slide at position {new_index + 1} (index {new_index})"
        )
        return _save_edit_and_respond(
            ppt_manager, edit_id, edit_state, output_bytes, success_msg,
            {"source_index": source_index, "new_index": new_index},
        )
    except Exception as e:
        logger.error(f"duplicate_slide error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


@tool(context=True)
def update_slide_notes(
    edit_id: str,
    slide_index: int,
    notes_text: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Update speaker notes for a specific slide.

    Args:
        edit_id: Edit ID returned by begin_presentation_edit
        slide_index: Slide index (0-based)
        notes_text: New notes content (use \\n for multi-line)
    """
    try:
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        source_bytes, edit_state, err = _load_edit_or_error(ppt_manager, edit_id)
        if err:
            return err

        with PptxEngine(source_bytes) as engine:
            order = engine.get_slide_order()
            if not (0 <= slide_index < len(order)):
                return {"content": [{"text": f"**Invalid slide_index {slide_index}**: presentation has {len(order)} slides"}], "status": "error"}
            engine.update_notes(order[slide_index]["filename"], notes_text)
            output_bytes = engine.pack()

        success_msg = (
            f"**Updated notes in draft**: `{edit_id}`\n\n"
            f"Slide {slide_index + 1} notes updated."
        )
        return _save_edit_and_respond(
            ppt_manager, edit_id, edit_state, output_bytes, success_msg,
            {"slide_index": slide_index},
        )
    except Exception as e:
        logger.error(f"update_slide_notes error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


@tool(context=True)
def create_presentation(
    presentation_name: str,
    slides: list | str | None,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Create a new presentation from scratch using PptxGenJS (JavaScript).

    Each slide is defined by a `custom_code` JavaScript snippet.
    The PptxGenJS instance is available as `pres`. Create your slide with `pres.addSlide()`.

    Args:
        presentation_name: Output name without extension (e.g., "sales-deck")
        slides: List of slide definitions:
            [{"custom_code": "let slide = pres.addSlide(); slide.addText(...)"}]
            Or None to create a blank presentation.

    PptxGenJS quick reference:
        let slide = pres.addSlide();
        slide.background = { color: "1E2761" };
        slide.addText("Title", { x: 0.5, y: 0.3, w: 12, h: 1.2, fontSize: 44, color: "FFFFFF", bold: true, fontFace: "Georgia" });
        slide.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.15, h: 7.5, fill: { color: "CADCFC" } });
        slide.addImage({ path: "img.png", x: 7, y: 1, w: 5.5, h: 4.5 });
        slide.addChart(pres.charts.BAR, [{ name: "S", labels: ["Q1","Q2"], values: [100, 120] }], { x: 1, y: 2, w: 8, h: 4 });

    Call get_slide_design_reference() for color palettes, typography, and layout ideas.

    Notes:
        - NEVER use "#" with hex colors
        - NEVER reuse option objects across addShape calls (PptxGenJS mutates in-place)
        - Layout is LAYOUT_WIDE (13.3" × 7.5")
    """
    try:
        # Parse slides if it's a JSON string
        if isinstance(slides, str):
            try:
                slides = json.loads(slides)
            except json.JSONDecodeError:
                fixed = re.sub(r",(\s*[}\]])", r"\1", slides)
                fixed = re.sub(r"//.*?$", "", fixed, flags=re.MULTILINE)
                try:
                    slides = json.loads(fixed)
                except json.JSONDecodeError as e:
                    return {"content": [{"text": f"**Invalid JSON for slides**: {str(e)}"}], "status": "error"}

        is_valid, error_msg = _validate_presentation_name(presentation_name)
        if not is_valid:
            return {"content": [{"text": f"**Invalid name**: {presentation_name}\n\n{error_msg}"}], "status": "error"}

        output_filename = f"{presentation_name}.pptx"
        user_id, session_id = _get_user_session_ids(tool_context)
        ppt_manager = PowerPointManager(user_id, session_id)

        try:
            ppt_manager.load_from_s3(output_filename)
            return {"content": [{"text": f"**Already exists**: {output_filename}\n\nUse a different name or delete the existing file first."}], "status": "error"}
        except FileNotFoundError:
            pass

        from builtin_tools.code_interpreter_tool import get_ci_session
        ci = get_ci_session(tool_context)
        if ci is None:
            return {"content": [{"text": "**Code Interpreter not configured**"}], "status": "error"}

        # Upload workspace images so slide code can reference them by filename
        ppt_manager.load_workspace_images_to_ci(ci)

        effective_slides = slides or []
        output_bytes = run_pptxgenjs(effective_slides, output_filename, ci)

        total_slides = len(effective_slides)
        success_msg = (
            f"**Created**: {output_filename}\n\n"
            f"{total_slides} slide(s), {len(output_bytes) // 1024} KB\n\n"
            f"Use `analyze_presentation` to inspect, `update_slide_content` to edit."
        )
        return _save_and_respond(
            ppt_manager, tool_context, output_filename, output_bytes,
            "create_presentation", user_id, session_id, success_msg,
            {"slide_count": total_slides},
        )
    except Exception as e:
        logger.error(f"create_presentation error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


def _render_presentation_images(
    pptx_bytes: bytes,
    filename: str,
    slide_numbers: list[int],
    dpi: int,
    max_slides: int | None = None,
):
    import tempfile
    from pdf2image import convert_from_path, pdfinfo_from_path

    with tempfile.TemporaryDirectory() as tmp:
        pptx_path = os.path.join(tmp, filename)
        with open(pptx_path, "wb") as file_handle:
            file_handle.write(pptx_bytes)

        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, pptx_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"PDF conversion failed: {result.stderr.strip()}")

        pdf_path = os.path.join(tmp, filename.replace(".pptx", ".pdf"))
        if not os.path.exists(pdf_path):
            raise RuntimeError(
                "PDF file was not created; LibreOffice may have failed silently."
            )

        total_slides = pdfinfo_from_path(pdf_path).get("Pages", 1)
        targets = slide_numbers or list(range(1, total_slides + 1))
        invalid = [slide for slide in targets if slide < 1 or slide > total_slides]
        if invalid:
            raise ValueError(
                f"Invalid slide number(s): {invalid}. "
                f"Presentation has {total_slides} slides."
            )
        if max_slides is not None and len(targets) > max_slides:
            raise ValueError(
                f"Selected {len(targets)} slides; this view supports at most "
                f"{max_slides}. Request smaller slide-number batches."
            )

        rendered = []
        for slide_number in targets:
            images = convert_from_path(
                pdf_path,
                first_page=slide_number,
                last_page=slide_number,
                dpi=dpi,
            )
            if images:
                rendered.append((slide_number, images[0]))
        return rendered, total_slides


@tool(context=True)
def preview_presentation_slides(
    presentation_name: str,
    slide_numbers: list[int],
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Get slide screenshots for visual inspection before editing.

    Images are sent to you (the agent), not displayed to the user.
    Use BEFORE modifying to understand exact layout and formatting.

    Args:
        presentation_name: Presentation name without extension
        slide_numbers: 1-indexed slide numbers to preview. Empty list [] = all slides.
    """
    import io

    user_id, session_id = _get_user_session_ids(tool_context)
    filename = _existing_presentation_filename(presentation_name)
    logger.info(f"preview_presentation_slides: {filename}, slides {slide_numbers}")

    try:
        ppt_manager = PowerPointManager(user_id, session_id)
        pptx_bytes, err = _load_or_error(ppt_manager, filename)
        if err:
            return err

        rendered, total_slides = _render_presentation_images(
            pptx_bytes, filename, slide_numbers, dpi=150
        )
        target_slides = [slide_number for slide_number, _ in rendered]
        content = [{
            "text": f"**{filename}** — {len(target_slides)} of {total_slides} slide(s)"
        }]

        for slide_num, image in rendered:
            max_dim = 1800
            if image.width > max_dim or image.height > max_dim:
                ratio = min(max_dim / image.width, max_dim / image.height)
                image = image.resize(
                    (int(image.width * ratio), int(image.height * ratio)),
                    resample=image.Resampling.LANCZOS
                    if hasattr(image, "Resampling")
                    else 1,
                )
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            content.append({"text": f"**Slide {slide_num}**"})
            content.append({
                "image": {
                    "format": "png",
                    "source": {"bytes": buffer.getvalue()},
                }
            })

        text_blocks = [block for block in content if "text" in block]
        image_blocks = [block for block in content if "image" in block]
        return build_image_response(text_blocks, image_blocks, {
            "filename": filename,
            "slide_numbers": target_slides,
            "total_slides": total_slides,
            "tool_type": "powerpoint_presentation",
            "user_id": user_id,
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"preview_presentation_slides error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


@tool(context=True)
def preview_presentation_montage(
    presentation_name: str,
    slide_numbers: list[int],
    tool_context: ToolContext,
    columns: int = 4,
) -> Dict[str, Any]:
    """Render up to 24 slides as one contact sheet for deck-level visual review.

    Use this for the first visual pass, then call preview_presentation_slides only
    for slides that need detailed inspection. Slide numbers are 1-based.

    Args:
        presentation_name: Presentation name without extension
        slide_numbers: 1-based slide numbers. Empty list [] selects all slides.
        columns: Contact-sheet columns, from 2 through 6
    """
    import io
    from PIL import Image, ImageDraw

    user_id, session_id = _get_user_session_ids(tool_context)
    filename = _existing_presentation_filename(presentation_name)
    try:
        if not 2 <= columns <= 6:
            return {
                "content": [{"text": "**Invalid columns**: choose a value from 2 to 6"}],
                "status": "error",
            }
        ppt_manager = PowerPointManager(user_id, session_id)
        pptx_bytes, err = _load_or_error(ppt_manager, filename)
        if err:
            return err

        rendered, total_slides = _render_presentation_images(
            pptx_bytes, filename, slide_numbers, dpi=100, max_slides=24
        )
        if not rendered:
            return {"content": [{"text": "**No slides rendered**"}], "status": "error"}

        label_height = 28
        cell_width = max(image.width for _, image in rendered)
        cell_height = max(image.height for _, image in rendered) + label_height
        rows = math.ceil(len(rendered) / columns)
        montage = Image.new(
            "RGB",
            (cell_width * columns, cell_height * rows),
            color="white",
        )
        draw = ImageDraw.Draw(montage)
        for item_index, (slide_number, image) in enumerate(rendered):
            column = item_index % columns
            row = item_index // columns
            x = column * cell_width
            y = row * cell_height
            draw.text((x + 8, y + 7), f"Slide {slide_number}", fill="black")
            montage.paste(image.convert("RGB"), (x, y + label_height))

        max_dimension = 1800
        if montage.width > max_dimension or montage.height > max_dimension:
            ratio = min(
                max_dimension / montage.width,
                max_dimension / montage.height,
            )
            montage = montage.resize(
                (int(montage.width * ratio), int(montage.height * ratio)),
                resample=Image.Resampling.LANCZOS
                if hasattr(Image, "Resampling")
                else 1,
            )

        buffer = io.BytesIO()
        montage.save(buffer, format="PNG")
        target_slides = [slide_number for slide_number, _ in rendered]
        return build_image_response(
            [{
                "text": (
                    f"**{filename} montage** — {len(target_slides)} of "
                    f"{total_slides} slide(s)"
                )
            }],
            [{
                "image": {
                    "format": "png",
                    "source": {"bytes": buffer.getvalue()},
                }
            }],
            {
                "filename": filename,
                "slide_numbers": target_slides,
                "total_slides": total_slides,
                "tool_type": "powerpoint_presentation",
                "user_id": user_id,
                "session_id": session_id,
            },
        )
    except Exception as e:
        logger.error(f"preview_presentation_montage error: {e}", exc_info=True)
        return {"content": [{"text": f"**Error:** {str(e)}"}], "status": "error"}


register_skill("powerpoint-presentations", tools=[
    get_slide_design_reference, list_my_powerpoint_presentations, inspect_presentation,
    begin_presentation_edit, finalize_presentation_edit, discard_presentation_edit,
    validate_presentation, get_presentation_layouts, analyze_presentation,
    create_presentation, update_slide_content, add_slide, delete_slides, move_slide,
    duplicate_slide, update_slide_notes, preview_presentation_montage,
    preview_presentation_slides,
])
