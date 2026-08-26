import io
import os
import sys
import zipfile


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from builtin_tools.lib.pptx_engine import PptxEngine
from builtin_tools import __all__ as builtin_tool_names


def _minimal_pptx(*, missing_layout=False, placeholder=False) -> bytes:
    parts = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        "ppt/presentation.xml": """<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>""",
        "ppt/_rels/presentation.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>""",
        "ppt/slides/slide1.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr/><p:grpSpPr/>
    <p:sp>
      <p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
      <p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="4572000" cy="914400"/></a:xfrm></p:spPr>
      <p:txBody><a:bodyPr/><a:lstStyle/><a:p>
        <a:r><a:rPr sz="2400"><a:latin typeface="Aptos"/></a:rPr><a:t>{"Click to add " if placeholder else ""}Quarter</a:t></a:r>
        <a:r><a:rPr sz="2400"/><a:t>ly Review</a:t></a:r>
      </a:p></p:txBody>
    </p:sp>
  </p:spTree></p:cSld>
</p:sld>""",
        "ppt/slides/_rels/slide1.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>""",
        "ppt/theme/theme1.xml": """<?xml version="1.0" encoding="UTF-8"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Test Theme">
  <a:themeElements>
    <a:clrScheme name="Test Colors">
      <a:dk1><a:srgbClr val="112233"/></a:dk1>
      <a:accent1><a:srgbClr val="008080"/></a:accent1>
    </a:clrScheme>
    <a:fontScheme name="Test Fonts">
      <a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont>
      <a:minorFont><a:latin typeface="Aptos"/></a:minorFont>
    </a:fontScheme>
  </a:themeElements>
</a:theme>""",
    }
    if not missing_layout:
        parts["ppt/slideLayouts/slideLayout1.xml"] = """<?xml version="1.0" encoding="UTF-8"?>
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld name="Title Slide"><p:spTree/></p:cSld>
</p:sldLayout>"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in parts.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_modern_powerpoint_tools_are_exported():
    assert {
        "inspect_presentation",
        "begin_presentation_edit",
        "finalize_presentation_edit",
        "validate_presentation",
        "preview_presentation_montage",
    }.issubset(builtin_tool_names)


def test_get_deck_spec_extracts_source_design_system():
    with PptxEngine(_minimal_pptx()) as engine:
        spec = engine.get_deck_spec()

    assert spec["slide_size"]["width_inches"] == 13.333
    assert spec["theme"]["name"] == "Test Theme"
    assert spec["theme"]["colors"]["accent1"] == "008080"
    assert spec["theme"]["fonts"]["major"] == "Aptos Display"
    assert spec["explicit_fonts"] == ["Aptos"]
    assert spec["slides"][0]["title"] == "Quarterly Review"
    assert spec["slides"][0]["layout"] == "Title Slide"


def test_validate_reports_missing_relationship_target():
    with PptxEngine(_minimal_pptx(missing_layout=True)) as engine:
        report = engine.validate()

    assert report["valid"] is False
    assert any(
        error["code"] == "missing_relationship_target"
        for error in report["errors"]
    )


def test_validate_reports_placeholder_text():
    with PptxEngine(_minimal_pptx(placeholder=True)) as engine:
        report = engine.validate()

    assert report["valid"] is True
    assert any(
        warning["code"] == "placeholder_text"
        for warning in report["warnings"]
    )


def test_replace_text_across_runs_is_exact_and_preserves_package():
    with PptxEngine(_minimal_pptx()) as engine:
        engine.replace_text("slide1.xml", 0, "Quarterly", "Annual")
        output = engine.pack()

    with PptxEngine(output) as engine:
        slide = engine.analyze_slide("slide1.xml")

    assert slide["title"] == "Annual Review"


def test_replace_text_does_not_reprocess_replacement_text():
    with PptxEngine(_minimal_pptx()) as engine:
        engine.replace_text("slide1.xml", 0, "Review", "Review Summary")
        output = engine.pack()

    with PptxEngine(output) as engine:
        slide = engine.analyze_slide("slide1.xml")

    assert slide["title"] == "Quarterly Review Summary"
