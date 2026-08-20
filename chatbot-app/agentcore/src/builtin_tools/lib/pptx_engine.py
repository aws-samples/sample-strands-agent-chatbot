"""PPTX XML Engine

Direct XML manipulation for PowerPoint files.
Ported from pptx skill: scripts/office/unpack.py, pack.py, clean.py, add_slide.py

Usage:
    from .lib.pptx_engine import PptxEngine

    pptx_bytes = ppt_manager.load_from_s3("deck.pptx")
    with PptxEngine(pptx_bytes) as engine:
        engine.delete_slides([0])
        engine.move_slide(1, 0)
        result_bytes = engine.pack()
    ppt_manager.save_edit(edit_id, result_bytes, expected_etag)
"""

import io
import logging
import math
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

import defusedxml.minidom

logger = logging.getLogger(__name__)

EMU_PER_INCH = 914400

SMART_QUOTE_REPLACEMENTS = {
    "\u201c": "&#x201C;",
    "\u201d": "&#x201D;",
    "\u2018": "&#x2018;",
    "\u2019": "&#x2019;",
}

SHAPE_TAGS = {"p:sp", "p:pic", "p:graphicFrame", "p:grpSp", "p:cxnSp"}


class PptxEngine:
    """Context manager for PPTX XML operations.

    Unpacks the PPTX on enter, provides edit methods, repacks on pack().
    Temp directory is cleaned up on exit regardless of exceptions.
    """

    def __init__(self, pptx_bytes: bytes):
        self._bytes = pptx_bytes
        self._tmpdir: Optional[Path] = None

    def __enter__(self) -> "PptxEngine":
        self._tmpdir = Path(tempfile.mkdtemp(prefix="pptx_engine_"))
        self._unpack()
        return self

    def __exit__(self, *args):
        if self._tmpdir and self._tmpdir.exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    @property
    def dir(self) -> Path:
        return self._tmpdir

    # ── Unpack / Pack ─────────────────────────────────────────────────────────

    def _unpack(self):
        """Extract PPTX zip, pretty-print XML, escape smart quotes."""
        with zipfile.ZipFile(io.BytesIO(self._bytes), "r") as zf:
            package_root = self._tmpdir.resolve()
            for member in zf.infolist():
                destination = (package_root / member.filename).resolve()
                try:
                    destination.relative_to(package_root)
                except ValueError as exc:
                    raise ValueError(
                        f"Unsafe package path: {member.filename}"
                    ) from exc
                zf.extract(member, package_root)

        xml_files = (
            list(self._tmpdir.rglob("*.xml"))
            + list(self._tmpdir.rglob("*.rels"))
        )
        for f in xml_files:
            _pretty_print_xml(f)
            _escape_smart_quotes(f)

    def pack(self) -> bytes:
        """Clean orphaned files, condense XML, repack to bytes."""
        self.clean()
        buf = io.BytesIO()
        tmp = Path(tempfile.mkdtemp())
        try:
            content_dir = tmp / "c"
            shutil.copytree(self._tmpdir, content_dir)
            for pattern in ("*.xml", "*.rels"):
                for f in content_dir.rglob(pattern):
                    _condense_xml(f)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in content_dir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(content_dir))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return buf.getvalue()

    # ── Slide order ───────────────────────────────────────────────────────────

    def get_slide_order(self) -> List[Dict[str, str]]:
        """Return ordered slides: [{'sld_id': '256', 'rid': 'rId1', 'filename': 'slide1.xml'}]"""
        pres_path = self._tmpdir / "ppt" / "presentation.xml"
        pres_rels_path = self._tmpdir / "ppt" / "_rels" / "presentation.xml.rels"

        rels_dom = defusedxml.minidom.parse(str(pres_rels_path))
        rid_to_file: Dict[str, str] = {}
        for rel in rels_dom.getElementsByTagName("Relationship"):
            rid = rel.getAttribute("Id")
            target = rel.getAttribute("Target")
            rel_type = rel.getAttribute("Type")
            if "slide" in rel_type and "Layout" not in rel_type and target.startswith("slides/"):
                rid_to_file[rid] = target.replace("slides/", "")

        pres_content = pres_path.read_text(encoding="utf-8")
        slide_ids = re.findall(r'<p:sldId\s+id="(\d+)"\s+r:id="([^"]+)"', pres_content)
        return [
            {"sld_id": sld_id, "rid": rid, "filename": rid_to_file[rid]}
            for sld_id, rid in slide_ids
            if rid in rid_to_file
        ]

    def _set_slide_order(self, ordered: List[Dict[str, str]]):
        """Rewrite presentation.xml sldIdLst."""
        pres_path = self._tmpdir / "ppt" / "presentation.xml"
        content = pres_path.read_text(encoding="utf-8")
        new_list = "".join(
            f'<p:sldId id="{s["sld_id"]}" r:id="{s["rid"]}"/>'
            for s in ordered
        )
        content = re.sub(
            r"<p:sldIdLst>.*?</p:sldIdLst>",
            f"<p:sldIdLst>{new_list}</p:sldIdLst>",
            content,
            flags=re.DOTALL,
        )
        pres_path.write_text(content, encoding="utf-8")

    # ── Layouts ───────────────────────────────────────────────────────────────

    def get_layouts(self) -> List[Dict[str, Any]]:
        """Return available layouts: [{'index': 0, 'name': 'Title Slide', 'filename': 'slideLayout1.xml'}]"""
        layouts_dir = self._tmpdir / "ppt" / "slideLayouts"
        result = []
        layout_files = sorted(
            layouts_dir.glob("slideLayout*.xml"),
            key=lambda f: int(re.search(r"\d+", f.name).group()),
        )
        for idx, layout_file in enumerate(layout_files):
            try:
                dom = defusedxml.minidom.parse(str(layout_file))
                csld = dom.getElementsByTagName("p:cSld")
                name = csld[0].getAttribute("name") if csld else layout_file.stem
                ph_count = len(dom.getElementsByTagName("p:ph"))
                result.append({"index": idx, "name": name, "filename": layout_file.name, "placeholder_count": ph_count})
            except Exception:
                result.append({"index": idx, "name": layout_file.stem, "filename": layout_file.name, "placeholder_count": 0})
        return result

    def get_deck_spec(self) -> Dict[str, Any]:
        """Return a compact, reusable description of the deck's design system."""
        width, height = self._get_slide_size()
        theme = self._get_theme_spec()
        slide_summaries = []

        for index, slide in enumerate(self.get_slide_order()):
            info = self.analyze_slide(slide["filename"])
            elements = info.get("elements", [])
            type_counts = Counter(element.get("type", "unknown") for element in elements)
            slide_summaries.append({
                "slide_index": index,
                "title": info.get("title"),
                "layout": self._get_slide_layout_name(slide["filename"]),
                "element_count": len(elements),
                "element_types": dict(sorted(type_counts.items())),
                "text_characters": sum(
                    len(element.get("text") or "") for element in elements
                ),
            })

        return {
            "schema_version": 1,
            "slide_size": {
                "width_inches": round(width, 3),
                "height_inches": round(height, 3),
                "aspect_ratio": round(width / height, 4) if height else None,
            },
            "theme": theme,
            "explicit_fonts": self._get_explicit_fonts(),
            "layouts": self.get_layouts(),
            "slides": slide_summaries,
        }

    def validate(self) -> Dict[str, Any]:
        """Validate package integrity and report conservative slide geometry issues."""
        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        required_parts = [
            "[Content_Types].xml",
            "_rels/.rels",
            "ppt/presentation.xml",
            "ppt/_rels/presentation.xml.rels",
        ]

        for part in required_parts:
            if not (self._tmpdir / part).is_file():
                errors.append({
                    "code": "missing_required_part",
                    "part": part,
                    "message": f"Required package part is missing: {part}",
                })

        xml_files = list(self._tmpdir.rglob("*.xml")) + list(
            self._tmpdir.rglob("*.rels")
        )
        for xml_file in xml_files:
            try:
                defusedxml.minidom.parse(str(xml_file))
            except Exception as exc:
                errors.append({
                    "code": "invalid_xml",
                    "part": str(xml_file.relative_to(self._tmpdir)),
                    "message": str(exc),
                })

        content_types_path = self._tmpdir / "[Content_Types].xml"
        if content_types_path.exists():
            try:
                content_types_dom = defusedxml.minidom.parse(
                    str(content_types_path)
                )
                override_parts = set()
                for override in content_types_dom.getElementsByTagName("Override"):
                    part_name = override.getAttribute("PartName")
                    override_parts.add(part_name)
                    part_path = self._tmpdir / part_name.lstrip("/")
                    if not part_path.exists():
                        errors.append({
                            "code": "missing_content_type_part",
                            "part": part_name,
                            "message": (
                                "Content type override references a missing part: "
                                f"{part_name}"
                            ),
                        })
                for slide_file in (self._tmpdir / "ppt" / "slides").glob(
                    "slide*.xml"
                ):
                    part_name = f"/ppt/slides/{slide_file.name}"
                    if part_name not in override_parts:
                        errors.append({
                            "code": "missing_slide_content_type",
                            "part": part_name,
                            "message": (
                                "Slide is missing its content type override: "
                                f"{part_name}"
                            ),
                        })
            except Exception:
                pass

        for rels_file in self._tmpdir.rglob("*.rels"):
            try:
                rels_dom = defusedxml.minidom.parse(str(rels_file))
            except Exception:
                continue
            for rel in rels_dom.getElementsByTagName("Relationship"):
                target = rel.getAttribute("Target")
                if not target or rel.getAttribute("TargetMode") == "External":
                    continue
                parsed = urlparse(target)
                if parsed.scheme or parsed.netloc:
                    continue
                target_path = _resolve_relationship_target(
                    self._tmpdir, rels_file, target
                )
                try:
                    target_path.relative_to(self._tmpdir.resolve())
                except ValueError:
                    errors.append({
                        "code": "relationship_outside_package",
                        "part": str(rels_file.relative_to(self._tmpdir)),
                        "relationship_id": rel.getAttribute("Id"),
                        "target": target,
                        "message": "Relationship target escapes the package root.",
                    })
                    continue
                if not target_path.exists():
                    errors.append({
                        "code": "missing_relationship_target",
                        "part": str(rels_file.relative_to(self._tmpdir)),
                        "relationship_id": rel.getAttribute("Id"),
                        "target": target,
                        "message": f"Relationship target does not exist: {target}",
                    })

        try:
            width, height = self._get_slide_size()
            slide_order = self.get_slide_order()
            for slide_index, slide in enumerate(slide_order):
                info = self.analyze_slide(slide["filename"])
                elements = info.get("elements", [])
                warnings.extend(
                    _lint_slide_geometry(slide_index, elements, width, height)
                )
        except Exception as exc:
            errors.append({
                "code": "slide_analysis_failed",
                "message": str(exc),
            })

        return {
            "valid": not errors,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
            "checks": {
                "required_parts": True,
                "xml_parse": True,
                "content_types": True,
                "relationship_targets": True,
                "slide_bounds": True,
                "text_overlap": True,
                "placeholder_text": True,
                "text_overflow_heuristic": True,
            },
        }

    def _get_slide_size(self) -> tuple[float, float]:
        pres_path = self._tmpdir / "ppt" / "presentation.xml"
        if not pres_path.exists():
            return 13.333, 7.5
        dom = defusedxml.minidom.parse(str(pres_path))
        sizes = dom.getElementsByTagName("p:sldSz")
        if not sizes:
            return 13.333, 7.5
        cx = int(sizes[0].getAttribute("cx") or 0)
        cy = int(sizes[0].getAttribute("cy") or 0)
        if not cx or not cy:
            return 13.333, 7.5
        return cx / EMU_PER_INCH, cy / EMU_PER_INCH

    def _get_theme_spec(self) -> Dict[str, Any]:
        theme_files = sorted((self._tmpdir / "ppt" / "theme").glob("theme*.xml"))
        if not theme_files:
            return {"name": None, "colors": {}, "fonts": {}}

        dom = defusedxml.minidom.parse(str(theme_files[0]))
        theme_nodes = dom.getElementsByTagName("a:theme")
        theme_name = theme_nodes[0].getAttribute("name") if theme_nodes else None
        colors: Dict[str, str] = {}
        schemes = dom.getElementsByTagName("a:clrScheme")
        if schemes:
            for child in schemes[0].childNodes:
                if child.nodeType != child.ELEMENT_NODE:
                    continue
                values = child.getElementsByTagName("a:srgbClr")
                if values:
                    colors[child.tagName.split(":")[-1]] = values[0].getAttribute("val")
                    continue
                values = child.getElementsByTagName("a:sysClr")
                if values:
                    colors[child.tagName.split(":")[-1]] = (
                        values[0].getAttribute("lastClr")
                        or values[0].getAttribute("val")
                    )

        fonts: Dict[str, Optional[str]] = {"major": None, "minor": None}
        for key, tag in (("major", "a:majorFont"), ("minor", "a:minorFont")):
            nodes = dom.getElementsByTagName(tag)
            if not nodes:
                continue
            latin = nodes[0].getElementsByTagName("a:latin")
            if latin:
                fonts[key] = latin[0].getAttribute("typeface") or None

        return {"name": theme_name, "colors": colors, "fonts": fonts}

    def _get_explicit_fonts(self) -> List[str]:
        fonts = set()
        search_roots = [
            self._tmpdir / "ppt" / "slides",
            self._tmpdir / "ppt" / "slideLayouts",
            self._tmpdir / "ppt" / "slideMasters",
        ]
        for search_root in search_roots:
            if not search_root.exists():
                continue
            for xml_file in search_root.rglob("*.xml"):
                try:
                    dom = defusedxml.minidom.parse(str(xml_file))
                except Exception:
                    continue
                for tag in ("a:latin", "a:ea", "a:cs"):
                    for node in dom.getElementsByTagName(tag):
                        typeface = node.getAttribute("typeface").strip()
                        if typeface and not typeface.startswith("+"):
                            fonts.add(typeface)
        return sorted(fonts, key=str.casefold)

    def _get_slide_layout_name(self, slide_filename: str) -> Optional[str]:
        rels_path = (
            self._tmpdir / "ppt" / "slides" / "_rels" / f"{slide_filename}.rels"
        )
        if not rels_path.exists():
            return None
        dom = defusedxml.minidom.parse(str(rels_path))
        for rel in dom.getElementsByTagName("Relationship"):
            if "slideLayout" not in rel.getAttribute("Type"):
                continue
            target_path = _resolve_relationship_target(
                self._tmpdir, rels_path, rel.getAttribute("Target")
            )
            if not target_path.exists():
                return None
            layout_dom = defusedxml.minidom.parse(str(target_path))
            csld = layout_dom.getElementsByTagName("p:cSld")
            return csld[0].getAttribute("name") if csld else target_path.stem
        return None

    # ── Analyze ───────────────────────────────────────────────────────────────

    def analyze_slide(self, slide_filename: str, include_notes: bool = False) -> Dict[str, Any]:
        """Parse slide XML and return structured element info."""
        slide_path = self._tmpdir / "ppt" / "slides" / slide_filename
        dom = defusedxml.minidom.parse(str(slide_path))

        sp_tree_list = dom.getElementsByTagName("p:spTree")
        if not sp_tree_list:
            return {"elements": [], "title": None}

        elements = []
        idx = 0
        for child in sp_tree_list[0].childNodes:
            if child.nodeType != child.ELEMENT_NODE or child.tagName not in SHAPE_TAGS:
                continue
            elements.append(_parse_shape(child, idx))
            idx += 1

        title = next(
            (e["text"] for e in elements if e.get("role") == "TITLE" and e.get("text")),
            None,
        )
        result: Dict[str, Any] = {"elements": elements, "title": title}
        if include_notes:
            result["notes"] = self._get_slide_notes(slide_filename)
        return result

    def _get_slide_notes(self, slide_filename: str) -> str:
        rels_path = (
            self._tmpdir / "ppt" / "slides" / "_rels" / f"{slide_filename}.rels"
        )
        if not rels_path.exists():
            return ""
        rels_dom = defusedxml.minidom.parse(str(rels_path))
        for rel in rels_dom.getElementsByTagName("Relationship"):
            if "notesSlide" in rel.getAttribute("Type"):
                target = rel.getAttribute("Target")
                notes_path = (self._tmpdir / "ppt" / "slides" / target).resolve()
                if notes_path.exists():
                    notes_dom = defusedxml.minidom.parse(str(notes_path))
                    return _extract_text(notes_dom)
        return ""

    # ── Edit text ─────────────────────────────────────────────────────────────

    def set_text(self, slide_filename: str, element_id: int, text: str):
        """Replace all text in a shape, preserving formatting from the first paragraph."""
        slide_path = self._tmpdir / "ppt" / "slides" / slide_filename
        dom = defusedxml.minidom.parseString(slide_path.read_bytes())
        shape = _get_shape_by_id(dom, element_id)
        if shape is None:
            raise ValueError(f"element_id {element_id} not found in {slide_filename}")

        tx_body_list = shape.getElementsByTagName("p:txBody")
        if not tx_body_list:
            raise ValueError(f"Shape {element_id} has no text body")
        tx_body = tx_body_list[0]

        # Capture formatting from first paragraph/run
        existing_paras = [
            c for c in tx_body.childNodes
            if c.nodeType == c.ELEMENT_NODE and c.tagName == "a:p"
        ]
        template_ppr_xml = ""
        template_rpr_xml = ""
        if existing_paras:
            ppr = [c for c in existing_paras[0].childNodes
                   if c.nodeType == c.ELEMENT_NODE and c.tagName == "a:pPr"]
            if ppr:
                template_ppr_xml = ppr[0].toxml()
            runs = existing_paras[0].getElementsByTagName("a:r")
            if runs:
                rpr = runs[0].getElementsByTagName("a:rPr")
                if rpr:
                    template_rpr_xml = rpr[0].toxml()

        # Remove all existing <a:p>
        for p in list(c for c in tx_body.childNodes
                      if c.nodeType == c.ELEMENT_NODE and c.tagName == "a:p"):
            tx_body.removeChild(p)

        # Build new paragraphs (one per line)
        a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
        for line in text.split("\n"):
            para_xml = (
                f'<a:p xmlns:a="{a_ns}">'
                f"{template_ppr_xml}"
                f"<a:r>{template_rpr_xml}<a:t>{_escape_xml(line)}</a:t></a:r>"
                f"</a:p>"
            )
            frag = defusedxml.minidom.parseString(para_xml)
            imported = dom.importNode(frag.getElementsByTagName("a:p")[0], True)
            tx_body.appendChild(imported)

        slide_path.write_bytes(dom.toxml(encoding="utf-8"))

    def replace_text(self, slide_filename: str, element_id: int, find: str, replace: str):
        """Find and replace text within a shape."""
        if not find:
            raise ValueError("find must be a non-empty string")
        slide_path = self._tmpdir / "ppt" / "slides" / slide_filename
        dom = defusedxml.minidom.parseString(slide_path.read_bytes())
        shape = _get_shape_by_id(dom, element_id)
        if shape is None:
            raise ValueError(f"element_id {element_id} not found in {slide_filename}")

        replacement_count = 0
        for paragraph in shape.getElementsByTagName("a:p"):
            text_nodes = paragraph.getElementsByTagName("a:t")
            values = [
                node.firstChild.nodeValue
                if node.firstChild and node.firstChild.nodeValue
                else ""
                for node in text_nodes
            ]
            combined = "".join(values)
            matches = list(re.finditer(re.escape(find), combined))
            if not matches:
                continue

            offsets = []
            cursor = 0
            for value in values:
                offsets.append((cursor, cursor + len(value)))
                cursor += len(value)

            output_values = [""] * len(values)
            cursor = 0
            for match in matches:
                _distribute_original_text(
                    combined, offsets, output_values, cursor, match.start()
                )
                start_index = next(
                    index
                    for index, (_, end) in enumerate(offsets)
                    if match.start() < end
                )
                output_values[start_index] += replace
                cursor = match.end()
            _distribute_original_text(
                combined, offsets, output_values, cursor, len(combined)
            )

            for node, value in zip(text_nodes, output_values):
                _set_text_node_value(dom, node, value)
            replacement_count += len(matches)

        if replacement_count == 0:
            raise ValueError(
                f"Text {find!r} was not found in element_id {element_id} "
                f"of {slide_filename}"
            )

        slide_path.write_bytes(dom.toxml(encoding="utf-8"))

    # ── Replace image ─────────────────────────────────────────────────────────

    def replace_image(self, slide_filename: str, element_id: int, image_bytes: bytes, image_ext: str = "png"):
        """Replace image in a picture shape with new image bytes."""
        slide_path = self._tmpdir / "ppt" / "slides" / slide_filename
        dom = defusedxml.minidom.parseString(slide_path.read_bytes())
        shape = _get_shape_by_id(dom, element_id)
        if shape is None:
            raise ValueError(f"element_id {element_id} not found in {slide_filename}")

        blip_list = shape.getElementsByTagName("a:blip")
        if not blip_list:
            raise ValueError(f"Shape {element_id} has no image blip")
        r_embed = blip_list[0].getAttribute("r:embed")
        if not r_embed:
            raise ValueError(f"Shape {element_id} blip has no r:embed attribute")

        rels_path = (
            self._tmpdir / "ppt" / "slides" / "_rels" / f"{slide_filename}.rels"
        )
        rels_content = rels_path.read_text(encoding="utf-8")
        match = re.search(
            rf'Id="{re.escape(r_embed)}"[^>]+Target="([^"]+)"', rels_content
        )
        if not match:
            match = re.search(
                rf'Target="([^"]+)"[^>]+Id="{re.escape(r_embed)}"', rels_content
            )
        if not match:
            raise ValueError(f"Relationship {r_embed} not found in .rels")

        rel_target = match.group(1)
        media_path = (self._tmpdir / "ppt" / "slides" / rel_target).resolve()
        old_ext = media_path.suffix.lstrip(".")

        if old_ext.lower() != image_ext.lower():
            new_rel_target = rel_target.replace(f".{old_ext}", f".{image_ext}")
            rels_content = rels_content.replace(rel_target, new_rel_target)
            rels_path.write_text(rels_content, encoding="utf-8")
            media_path = (self._tmpdir / "ppt" / "slides" / new_rel_target).resolve()

        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(image_bytes)

    # ── Add / duplicate / delete / move ───────────────────────────────────────

    def add_slide(self, layout_name: str, position: int = -1) -> str:
        """Add a new slide from layout name. Returns new slide filename."""
        layouts = self.get_layouts()
        match = next((l for l in layouts if l["name"] == layout_name), None)  # noqa: E741
        if not match:
            available = [l["name"] for l in layouts]  # noqa: E741
            raise ValueError(f"Layout '{layout_name}' not found. Available: {available}")
        new_filename = self._create_slide_from_layout(match["filename"])
        self._insert_into_order(new_filename, position)
        return new_filename

    def duplicate_slide(self, slide_index: int, position: int = -1) -> str:
        """Duplicate a slide by 0-based index. Returns new slide filename."""
        order = self.get_slide_order()
        if not (0 <= slide_index < len(order)):
            raise ValueError(f"slide_index {slide_index} out of range (0-{len(order)-1})")
        source = order[slide_index]["filename"]
        new_filename = self._duplicate_slide_file(source)
        self._insert_into_order(new_filename, position)
        return new_filename

    def delete_slides(self, indices: List[int]):
        """Remove slides at given 0-based indices from presentation order."""
        order = self.get_slide_order()
        new_order = [s for i, s in enumerate(order) if i not in indices]
        self._set_slide_order(new_order)

    def move_slide(self, from_index: int, to_index: int):
        """Move a slide from one position to another (0-based)."""
        order = self.get_slide_order()
        if not (0 <= from_index < len(order)):
            raise ValueError(f"from_index {from_index} out of range")
        slide = order.pop(from_index)
        insert_at = to_index if to_index >= 0 else len(order)
        order.insert(insert_at, slide)
        self._set_slide_order(order)

    def _create_slide_from_layout(self, layout_file: str) -> str:
        slides_dir = self._tmpdir / "ppt" / "slides"
        rels_dir = slides_dir / "_rels"
        rels_dir.mkdir(exist_ok=True)

        next_num = _get_next_slide_number(slides_dir)
        dest = f"slide{next_num}.xml"

        slide_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
            ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree>"
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            "<p:grpSpPr><a:xfrm>"
            '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/>'
            "</a:xfrm></p:grpSpPr>"
            "</p:spTree></p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
            "</p:sld>"
        )
        (slides_dir / dest).write_text(slide_xml, encoding="utf-8")

        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1"'
            f' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"'
            f' Target="../slideLayouts/{layout_file}"/>'
            "</Relationships>"
        )
        (rels_dir / f"{dest}.rels").write_text(rels_xml, encoding="utf-8")

        _add_to_content_types(self._tmpdir, dest)
        _add_to_presentation_rels(self._tmpdir, dest)
        return dest

    def _duplicate_slide_file(self, source: str) -> str:
        slides_dir = self._tmpdir / "ppt" / "slides"
        rels_dir = slides_dir / "_rels"

        next_num = _get_next_slide_number(slides_dir)
        dest = f"slide{next_num}.xml"
        shutil.copy2(slides_dir / source, slides_dir / dest)

        src_rels = rels_dir / f"{source}.rels"
        if src_rels.exists():
            rels_content = src_rels.read_text(encoding="utf-8")
            # Remove notes slide reference from duplicate
            rels_content = re.sub(
                r'\s*<Relationship[^>]*notesSlide[^>]*/>\s*', "\n", rels_content
            )
            (rels_dir / f"{dest}.rels").write_text(rels_content, encoding="utf-8")

        _add_to_content_types(self._tmpdir, dest)
        _add_to_presentation_rels(self._tmpdir, dest)
        return dest

    def _insert_into_order(self, new_filename: str, position: int):
        """Insert a newly created slide into sldIdLst at the given position."""
        pres_rels_path = self._tmpdir / "ppt" / "_rels" / "presentation.xml.rels"
        rels_content = pres_rels_path.read_text(encoding="utf-8")
        match = re.search(
            rf'Id="([^"]+)"[^>]*Target="slides/{re.escape(new_filename)}"', rels_content
        )
        if not match:
            match = re.search(
                rf'Target="slides/{re.escape(new_filename)}"[^>]*Id="([^"]+)"', rels_content
            )
        if not match:
            return

        rid = match.group(1)
        order = self.get_slide_order()
        next_id = max((int(s["sld_id"]) for s in order), default=255) + 1
        new_entry = {"sld_id": str(next_id), "rid": rid, "filename": new_filename}

        if position < 0:
            order.append(new_entry)
        else:
            order.insert(position, new_entry)
        self._set_slide_order(order)

    # ── Speaker notes ─────────────────────────────────────────────────────────

    def update_notes(self, slide_filename: str, notes_text: str):
        """Set speaker notes text for a slide."""
        rels_path = (
            self._tmpdir / "ppt" / "slides" / "_rels" / f"{slide_filename}.rels"
        )
        if not rels_path.exists():
            return
        rels_dom = defusedxml.minidom.parse(str(rels_path))
        for rel in rels_dom.getElementsByTagName("Relationship"):
            if "notesSlide" in rel.getAttribute("Type"):
                target = rel.getAttribute("Target")
                notes_path = (self._tmpdir / "ppt" / "slides" / target).resolve()
                if not notes_path.exists():
                    return
                notes_dom = defusedxml.minidom.parse(str(notes_path))
                # Find the body placeholder and replace its text
                a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
                for sp in notes_dom.getElementsByTagName("p:sp"):
                    ph_list = sp.getElementsByTagName("p:ph")
                    ph_type = ph_list[0].getAttribute("type") if ph_list else ""
                    if ph_type in ("", "body", "obj"):
                        tx_body_list = sp.getElementsByTagName("p:txBody")
                        if not tx_body_list:
                            continue
                        tx_body = tx_body_list[0]
                        for p in list(
                            c for c in tx_body.childNodes
                            if c.nodeType == c.ELEMENT_NODE and c.tagName == "a:p"
                        ):
                            tx_body.removeChild(p)
                        para_xml = (
                            f'<a:p xmlns:a="{a_ns}">'
                            f"<a:r><a:t>{_escape_xml(notes_text)}</a:t></a:r>"
                            f"</a:p>"
                        )
                        frag = defusedxml.minidom.parseString(para_xml)
                        imported = notes_dom.importNode(
                            frag.getElementsByTagName("a:p")[0], True
                        )
                        tx_body.appendChild(imported)
                        break
                notes_path.write_bytes(notes_dom.toxml(encoding="utf-8"))
                return

    # ── Clean ─────────────────────────────────────────────────────────────────

    def clean(self):
        """Remove orphaned slides, media files, and update Content_Types."""
        referenced_slides = self._get_referenced_slides()
        self._remove_orphaned_slides(referenced_slides)
        self._remove_orphaned_media()

    def _get_referenced_slides(self) -> set:
        pres_path = self._tmpdir / "ppt" / "presentation.xml"
        pres_rels_path = self._tmpdir / "ppt" / "_rels" / "presentation.xml.rels"
        if not pres_path.exists() or not pres_rels_path.exists():
            return set()

        rels_dom = defusedxml.minidom.parse(str(pres_rels_path))
        rid_to_file: Dict[str, str] = {}
        for rel in rels_dom.getElementsByTagName("Relationship"):
            rid = rel.getAttribute("Id")
            target = rel.getAttribute("Target")
            rel_type = rel.getAttribute("Type")
            if "slide" in rel_type and "Layout" not in rel_type and target.startswith("slides/"):
                rid_to_file[rid] = target.replace("slides/", "")

        pres_content = pres_path.read_text(encoding="utf-8")
        referenced_rids = set(re.findall(r'<p:sldId[^>]*r:id="([^"]+)"', pres_content))
        return {rid_to_file[rid] for rid in referenced_rids if rid in rid_to_file}

    def _remove_orphaned_slides(self, referenced: set):
        slides_dir = self._tmpdir / "ppt" / "slides"
        rels_dir = slides_dir / "_rels"
        pres_rels_path = self._tmpdir / "ppt" / "_rels" / "presentation.xml.rels"
        removed = []

        for f in slides_dir.glob("slide*.xml"):
            if f.name not in referenced:
                f.unlink()
                removed.append(f.name)
                rels = rels_dir / f"{f.name}.rels"
                if rels.exists():
                    rels.unlink()

        if removed and pres_rels_path.exists():
            content = pres_rels_path.read_text(encoding="utf-8")
            for name in removed:
                content = re.sub(
                    rf'<Relationship[^>]*"slides/{re.escape(name)}"[^>]*/>', "", content
                )
            pres_rels_path.write_text(content, encoding="utf-8")
            _update_content_types(
                self._tmpdir, [f"ppt/slides/{n}" for n in removed]
            )

    def _remove_orphaned_media(self):
        """Remove unreferenced files from media/, charts/, diagrams/ directories."""
        referenced: set = set()
        for rels_file in self._tmpdir.rglob("*.rels"):
            try:
                dom = defusedxml.minidom.parse(str(rels_file))
                for rel in dom.getElementsByTagName("Relationship"):
                    target = rel.getAttribute("Target")
                    if not target:
                        continue
                    target_path = (rels_file.parent.parent / target).resolve()
                    try:
                        referenced.add(
                            target_path.relative_to(self._tmpdir.resolve())
                        )
                    except ValueError:
                        pass
            except Exception:
                pass

        removed = []
        for dir_name in ("media", "embeddings", "charts", "diagrams"):
            dir_path = self._tmpdir / "ppt" / dir_name
            if not dir_path.exists():
                continue
            for f in dir_path.glob("*"):
                if f.is_file():
                    rel_path = f.relative_to(self._tmpdir)
                    if rel_path not in referenced:
                        f.unlink()
                        removed.append(str(rel_path))

        if removed:
            _update_content_types(self._tmpdir, removed)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _pretty_print_xml(xml_file: Path):
    try:
        dom = defusedxml.minidom.parseString(xml_file.read_bytes())
        xml_file.write_bytes(dom.toprettyxml(indent="  ", encoding="utf-8"))
    except Exception:
        pass


def _escape_smart_quotes(xml_file: Path):
    try:
        content = xml_file.read_text(encoding="utf-8")
        for char, entity in SMART_QUOTE_REPLACEMENTS.items():
            content = content.replace(char, entity)
        xml_file.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _condense_xml(xml_file: Path):
    """Remove whitespace-only text nodes (except inside <a:t>), condense for ZIP."""
    try:
        with open(xml_file, encoding="utf-8") as f:
            dom = defusedxml.minidom.parse(f)
        for element in dom.getElementsByTagName("*"):
            if element.tagName.endswith(":t"):
                continue
            for child in list(element.childNodes):
                if (
                    child.nodeType == child.TEXT_NODE
                    and child.nodeValue
                    and child.nodeValue.strip() == ""
                ) or child.nodeType == child.COMMENT_NODE:
                    element.removeChild(child)
        xml_file.write_bytes(dom.toxml(encoding="UTF-8"))
    except Exception as e:
        logger.warning(f"condense_xml failed for {xml_file.name}: {e}")
        raise


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _get_next_slide_number(slides_dir: Path) -> int:
    existing = [
        int(m.group(1))
        for f in slides_dir.glob("slide*.xml")
        if (m := re.match(r"slide(\d+)\.xml", f.name))
    ]
    return max(existing) + 1 if existing else 1


def _add_to_content_types(unpacked_dir: Path, dest: str):
    ct_path = unpacked_dir / "[Content_Types].xml"
    content = ct_path.read_text(encoding="utf-8")
    override = (
        f'<Override PartName="/ppt/slides/{dest}"'
        f' ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
    )
    if f"/ppt/slides/{dest}" not in content:
        content = content.replace("</Types>", f"  {override}\n</Types>")
        ct_path.write_text(content, encoding="utf-8")


def _add_to_presentation_rels(unpacked_dir: Path, dest: str) -> str:
    pres_rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"
    content = pres_rels_path.read_text(encoding="utf-8")
    rids = [int(m) for m in re.findall(r'Id="rId(\d+)"', content)]
    next_rid = max(rids) + 1 if rids else 1
    rid = f"rId{next_rid}"
    new_rel = (
        f'<Relationship Id="{rid}"'
        f' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"'
        f' Target="slides/{dest}"/>'
    )
    if f"slides/{dest}" not in content:
        content = content.replace("</Relationships>", f"  {new_rel}\n</Relationships>")
        pres_rels_path.write_text(content, encoding="utf-8")
    return rid


def _update_content_types(unpacked_dir: Path, removed_paths: List[str]):
    ct_path = unpacked_dir / "[Content_Types].xml"
    if not ct_path.exists():
        return
    content = ct_path.read_text(encoding="utf-8")
    for path in removed_paths:
        part_name = "/" + path.replace("\\", "/").lstrip("/")
        content = re.sub(
            rf'<Override\s+PartName="{re.escape(part_name)}"[^/]*/>', "", content
        )
    ct_path.write_text(content, encoding="utf-8")


def _get_shape_by_id(dom, element_id: int):
    """Return the element_id-th shape node in p:spTree (0-based)."""
    sp_tree_list = dom.getElementsByTagName("p:spTree")
    if not sp_tree_list:
        return None
    idx = 0
    for child in sp_tree_list[0].childNodes:
        if child.nodeType != child.ELEMENT_NODE or child.tagName not in SHAPE_TAGS:
            continue
        if idx == element_id:
            return child
        idx += 1
    return None


def _set_text_node_value(dom, text_node, value: str):
    if text_node.firstChild:
        text_node.firstChild.nodeValue = value
    else:
        text_node.appendChild(dom.createTextNode(value))


def _distribute_original_text(
    combined: str,
    offsets: List[tuple[int, int]],
    output_values: List[str],
    start: int,
    end: int,
) -> None:
    for index, (node_start, node_end) in enumerate(offsets):
        segment_start = max(start, node_start)
        segment_end = min(end, node_end)
        if segment_start < segment_end:
            output_values[index] += combined[segment_start:segment_end]


def _parse_shape(node, element_id: int) -> Dict[str, Any]:
    tag = node.tagName
    elem: Dict[str, Any] = {"id": element_id}

    if tag == "p:sp":
        elem["type"] = "text"
        elem["role"] = _get_placeholder_role(node)
        elem["text"] = _extract_text(node)
        elem["position"] = _get_position(node)
        elem.update(_get_text_metrics(node))
    elif tag == "p:pic":
        elem["type"] = "picture"
        elem["role"] = ""
        elem["text"] = ""
        elem["position"] = _get_position(node)
    elif tag == "p:graphicFrame":
        uri_nodes = node.getElementsByTagName("a:graphicData")
        uri = uri_nodes[0].getAttribute("uri") if uri_nodes else ""
        elem["type"] = "table" if "table" in uri else "chart"
        elem["text"] = _extract_table_text(node) if "table" in uri else ""
        elem["role"] = ""
        elem["position"] = _get_position(node)
    elif tag == "p:grpSp":
        elem["type"] = "group"
        elem["role"] = ""
        elem["text"] = ""
        elem["position"] = _get_position(node)
    else:
        elem["type"] = "unknown"
        elem["role"] = ""
        elem["text"] = ""
        elem["position"] = {"left": 0, "top": 0}

    return elem


def _get_placeholder_role(sp_node) -> str:
    ph_list = sp_node.getElementsByTagName("p:ph")
    if not ph_list:
        return ""
    ph_type = ph_list[0].getAttribute("type")
    return {
        "title": "TITLE",
        "ctrTitle": "TITLE",
        "subTitle": "SUBTITLE",
        "body": "BODY",
        "obj": "BODY",
        "dt": "FOOTER",
        "ftr": "FOOTER",
        "sldNum": "FOOTER",
    }.get(ph_type, "BODY")


def _extract_text(node) -> str:
    texts = []
    for p in node.getElementsByTagName("a:p"):
        runs = p.getElementsByTagName("a:t")
        line = "".join(
            t.firstChild.nodeValue for t in runs if t.firstChild and t.firstChild.nodeValue
        )
        if line:
            texts.append(line)
    return "\n".join(texts)


def _extract_table_text(node) -> str:
    rows = []
    for tr in node.getElementsByTagName("a:tr"):
        cells = []
        for tc in tr.getElementsByTagName("a:tc"):
            t_nodes = tc.getElementsByTagName("a:t")
            cell_text = "".join(
                t.firstChild.nodeValue for t in t_nodes if t.firstChild and t.firstChild.nodeValue
            )
            cells.append(cell_text)
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def _get_position(node) -> Dict[str, float]:
    off_list = node.getElementsByTagName("a:off")
    ext_list = node.getElementsByTagName("a:ext")
    x = int(off_list[0].getAttribute("x") or 0) if off_list else 0
    y = int(off_list[0].getAttribute("y") or 0) if off_list else 0
    cx = int(ext_list[0].getAttribute("cx") or 0) if ext_list else 0
    cy = int(ext_list[0].getAttribute("cy") or 0) if ext_list else 0
    return {
        "left": round(x / EMU_PER_INCH, 3),
        "top": round(y / EMU_PER_INCH, 3),
        "width": round(cx / EMU_PER_INCH, 3),
        "height": round(cy / EMU_PER_INCH, 3),
    }


def _get_text_metrics(node) -> Dict[str, Any]:
    sizes = []
    for tag in ("a:rPr", "a:defRPr", "a:endParaRPr"):
        for props in node.getElementsByTagName(tag):
            raw_size = props.getAttribute("sz")
            if raw_size.isdigit():
                sizes.append(int(raw_size) / 100)
    return {
        "font_size_pt": max(sizes) if sizes else None,
        "autofit": bool(
            node.getElementsByTagName("a:normAutofit")
            or node.getElementsByTagName("a:spAutoFit")
        ),
    }


def _resolve_relationship_target(
    package_root: Path, rels_file: Path, target: str
) -> Path:
    clean_target = unquote(target.split("#", 1)[0].replace("\\", "/"))
    if clean_target.startswith("/"):
        return (package_root / clean_target.lstrip("/")).resolve()
    source_dir = rels_file.parent.parent
    return (source_dir / clean_target).resolve()


def _lint_slide_geometry(
    slide_index: int,
    elements: List[Dict[str, Any]],
    slide_width: float,
    slide_height: float,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    tolerance = 0.03
    positioned = []

    for element in elements:
        position = element.get("position") or {}
        left = float(position.get("left", 0))
        top = float(position.get("top", 0))
        width = float(position.get("width", 0))
        height = float(position.get("height", 0))
        if width <= 0 or height <= 0:
            continue

        positioned.append(element)
        if (
            left < -tolerance
            or top < -tolerance
            or left + width > slide_width + tolerance
            or top + height > slide_height + tolerance
        ):
            issues.append({
                "code": "element_out_of_bounds",
                "slide_index": slide_index,
                "element_id": element.get("id"),
                "message": "Element extends beyond the slide boundary.",
                "position": position,
            })

        text = (element.get("text") or "").strip()
        if text and re.search(
            r"(\{\{[^{}]+\}\}|<[^<>]+>|lorem\s+ipsum|click\s+to\s+add)",
            text,
            flags=re.IGNORECASE,
        ):
            issues.append({
                "code": "placeholder_text",
                "slide_index": slide_index,
                "element_id": element.get("id"),
                "message": f"Possible placeholder text remains: {text[:80]}",
            })

        if (
            element.get("type") == "text"
            and text
            and not element.get("autofit")
            and element.get("font_size_pt")
        ):
            font_size = float(element["font_size_pt"])
            line_height = max(0.01, font_size / 72 * 1.2)
            average_char_width = max(0.01, font_size / 72 * 0.52)
            chars_per_line = max(1, int(width / average_char_width))
            estimated_lines = sum(
                max(1, math.ceil(len(line) / chars_per_line))
                for line in text.splitlines() or [text]
            )
            available_lines = max(1, int(height / line_height))
            if estimated_lines > available_lines * 1.25:
                issues.append({
                    "code": "possible_text_overflow",
                    "slide_index": slide_index,
                    "element_id": element.get("id"),
                    "message": (
                        f"Text may require about {estimated_lines} lines but the "
                        f"box fits about {available_lines} at {font_size:g}pt."
                    ),
                })

    text_elements = [
        element
        for element in positioned
        if element.get("type") == "text" and (element.get("text") or "").strip()
    ]
    for index, first in enumerate(text_elements):
        for second in text_elements[index + 1:]:
            overlap_ratio = _overlap_ratio(
                first["position"], second["position"]
            )
            if overlap_ratio >= 0.08:
                issues.append({
                    "code": "text_overlap",
                    "slide_index": slide_index,
                    "element_ids": [first.get("id"), second.get("id")],
                    "overlap_ratio": round(overlap_ratio, 3),
                    "message": "Text boxes overlap and require visual review.",
                })

    return issues


def _overlap_ratio(first: Dict[str, float], second: Dict[str, float]) -> float:
    left = max(first["left"], second["left"])
    top = max(first["top"], second["top"])
    right = min(
        first["left"] + first["width"],
        second["left"] + second["width"],
    )
    bottom = min(
        first["top"] + first["height"],
        second["top"] + second["height"],
    )
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller_area = min(
        first["width"] * first["height"],
        second["width"] * second["height"],
    )
    return intersection / smaller_area if smaller_area else 0.0
