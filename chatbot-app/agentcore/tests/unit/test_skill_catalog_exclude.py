"""Disabled skills are hidden from the catalog and unavailable to execution."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from skill.skill_registry import SkillRegistry


def _make_skill(root: Path, name: str, description: str) -> None:
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"
    )


@pytest.fixture
def registry():
    tmp = tempfile.mkdtemp()
    _make_skill(Path(tmp), "arxiv-search", "Search ArXiv papers")
    _make_skill(Path(tmp), "excel-spreadsheets", "Excel spreadsheets")
    _make_skill(Path(tmp), "weather", "Weather forecast")
    reg = SkillRegistry(skills_dir=tmp)
    reg.discover_skills()
    yield reg
    shutil.rmtree(tmp)


def test_catalog_without_exclude_lists_all_skills(registry):
    cat = registry.get_catalog()
    assert "arxiv-search" in cat
    assert "excel-spreadsheets" in cat
    assert "weather" in cat


def test_catalog_excludes_named_skills(registry):
    cat = registry.get_catalog(exclude={"excel-spreadsheets"})
    assert "excel-spreadsheets" not in cat
    assert "arxiv-search" in cat
    assert "weather" in cat


def test_catalog_excludes_multiple(registry):
    cat = registry.get_catalog(exclude=["excel-spreadsheets", "weather"])
    assert "excel-spreadsheets" not in cat
    assert "weather" not in cat
    assert "arxiv-search" in cat


def test_catalog_all_excluded_returns_empty(registry):
    cat = registry.get_catalog(exclude={"arxiv-search", "excel-spreadsheets", "weather"})
    assert cat == ""


def test_catalog_unknown_exclude_is_noop(registry):
    cat = registry.get_catalog(exclude={"nonexistent-skill"})
    assert "arxiv-search" in cat
    assert "excel-spreadsheets" in cat
    assert "weather" in cat


def test_discovery_excludes_disabled_skill(tmp_path):
    _make_skill(tmp_path, "weather", "Weather forecast")
    _make_skill(tmp_path, "github", "GitHub tools")
    reg = SkillRegistry(skills_dir=str(tmp_path))

    reg.discover_skills(exclude={"github"})

    assert reg.skill_names == ["weather"]
    with pytest.raises(KeyError, match="Unknown skill"):
        reg.load_instructions("github")
