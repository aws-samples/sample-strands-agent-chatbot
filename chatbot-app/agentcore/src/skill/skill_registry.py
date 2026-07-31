"""
SkillRegistry — directory-based skill discovery with tool binding.

L1 discovery reads SKILL.md frontmatter from the skills/ directory (single source of truth).
Tool objects are then bound to discovered skills by matching _skill_name.

Progressive disclosure levels:
  Level 1: discover_skills() + get_catalog()  → skill name + description for system prompt
  Level 2: load_instructions()                → SKILL.md body content
  Level 3: bind_tools() + get_tools()         → AgentTool objects for execution via skill_executor
"""

import inspect
import logging
import os
import re
from collections.abc import Iterable

from skill.tool_names import canonical_tool_name

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Registry that discovers skills from directory and binds tools to them."""

    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = skills_dir
        # skill_name → { description, type, compose, tools, sources }
        self._skills: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Phase 1: Directory-based skill discovery (L1)
    # ------------------------------------------------------------------

    def discover_skills(self, exclude: Iterable[str] | None = None) -> None:
        """Scan skills/ directory for SKILL.md files and build the skill index.

        Reads frontmatter from each SKILL.md to extract:
          - name: skill identifier
          - description: one-line description for catalog
          - type: "tool" (default) | "instruction" | "composite"
          - compose: list of skill names (for composite skills)

        This is the single source of truth for skill metadata.
        """
        if not os.path.isdir(self.skills_dir):
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return

        excluded = set(exclude or ())
        for entry in sorted(os.listdir(self.skills_dir)):
            skill_dir = os.path.join(self.skills_dir, entry)
            skill_md = os.path.join(skill_dir, "SKILL.md")

            if not os.path.isfile(skill_md):
                continue

            meta = self._parse_frontmatter(skill_md)
            name = meta.get("name", entry)
            if name in excluded:
                continue

            self._skills[name] = {
                "description": meta.get("description", ""),
                "type": meta.get("type", "tool"),
                "compose": meta.get("compose", []),
                "tools": [],
                "sources": {},  # func_name → { "file": str, "func": callable }
            }

        discovered = list(self._skills.keys())
        logger.info(f"SkillRegistry discovered {len(discovered)} skills from directory: {discovered}")

    # ------------------------------------------------------------------
    # Phase 2: Bind tool objects to discovered skills
    # ------------------------------------------------------------------

    def bind_tools(self, tools: list) -> None:
        """Bind tool objects to already-discovered skills by matching _skill_name.

        Tools without _skill_name are ignored (non-skill tools).
        Tools referencing unknown skills produce a warning and are skipped.
        Handles both local tools (with _tool_func) and MCP tools (with mcp_client).
        """
        for tool_obj in tools:
            name = getattr(tool_obj, "_skill_name", None)
            if not name:
                continue

            if name not in self._skills:
                logger.warning(
                    f"Tool '{canonical_tool_name(tool_obj)}' references "
                    f"unknown skill '{name}' — skipping (no SKILL.md found)"
                )
                continue

            self._skills[name]["tools"].append(tool_obj)

            # Capture source info — only for local tools (MCP tools have no _tool_func)
            is_mcp = hasattr(tool_obj, "mcp_client")
            if is_mcp:
                logger.debug(
                    f"MCP tool '{canonical_tool_name(tool_obj)}' bound to skill '{name}'"
                )
                continue

            func = getattr(tool_obj, "_tool_func", None)
            if func is not None:
                func_name = func.__name__
                try:
                    source_file = inspect.getfile(func)
                    self._skills[name]["sources"][func_name] = {
                        "file": source_file,
                        "func": func,
                    }
                except (TypeError, OSError):
                    logger.debug(f"Could not resolve source for {func_name}")

        bound_counts = {
            name: len(info["tools"])
            for name, info in self._skills.items()
            if info["tools"]
        }
        logger.info(f"SkillRegistry bound tools: {bound_counts}")

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    # ------------------------------------------------------------------
    # Level 1: catalog (injected into system prompt at init)
    # ------------------------------------------------------------------

    def get_catalog(self, exclude: Iterable[str] | None = None) -> str:
        """Generate the Level 1 skill catalog for the system prompt.

        Skills listed in `exclude` are omitted so the agent never sees a
        disabled skill as available.
        """
        if not self._skills:
            return ""

        excluded = set(exclude or ())
        visible = {name: info for name, info in self._skills.items() if name not in excluded}
        if not visible:
            return ""

        lines = [
            "## Available Skills",
            "",
            "Use skill_dispatcher to activate a skill, then skill_executor to run its tools.",
            "",
        ]
        for name, info in visible.items():
            skill_type = info.get("type", "tool")
            suffix = ""
            if skill_type == "instruction":
                suffix = " _(guidelines only, no tools)_"
            elif skill_type == "composite":
                composed = ", ".join(info.get("compose", []))
                suffix = f" _(combines: {composed})_"
            lines.append(f"- **{name}**: {info['description']}{suffix}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Level 2: instructions (returned by skill_dispatcher)
    # ------------------------------------------------------------------

    def load_instructions(self, skill_name: str) -> str:
        """Read and return the SKILL.md body for a skill (Level 2 content).

        Strips the YAML frontmatter and returns only the markdown body.
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        # Resolve: {skills_dir}/{skill_name}/SKILL.md
        full_path = os.path.join(self.skills_dir, skill_name, "SKILL.md")

        if not os.path.isfile(full_path):
            logger.warning(f"SKILL.md not found at {full_path}")
            return f"Instructions file not found for skill '{skill_name}'."

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        return self._strip_frontmatter(content)

    # ------------------------------------------------------------------
    # Level 2+: source code (read tool implementations on demand)
    # ------------------------------------------------------------------

    def list_sources(self, skill_name: str) -> list[dict]:
        """List available source functions for a skill.

        Returns a list of { "function": str, "file": str } dicts.
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        sources = self._skills[skill_name].get("sources", {})
        return [
            {"function": func_name, "file": info["file"]}
            for func_name, info in sources.items()
        ]

    def load_source(self, skill_name: str, function_name: str) -> str:
        """Read the source code of a registered tool function.

        Uses inspect.getsource() on the function object captured at
        bind_tools() time — deterministic, no path guessing.

        Args:
            skill_name: The skill identifier.
            function_name: The function name (e.g. "create_presentation").

        Returns:
            Source code as a string.
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        sources = self._skills[skill_name].get("sources", {})
        if function_name not in sources:
            available = list(sources.keys())
            raise KeyError(
                f"Function '{function_name}' not found in skill '{skill_name}'. "
                f"Available: {available}"
            )

        func = sources[function_name]["func"]
        return inspect.getsource(func)

    # ------------------------------------------------------------------
    # Level 2+: reference files (additional docs in skill directory)
    # ------------------------------------------------------------------

    def list_references(self, skill_name: str) -> list[str]:
        """List available reference files in a skill's directory.

        Returns filenames (excluding SKILL.md) that the LLM can request
        via skill_dispatcher(skill_name, reference="filename").
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        skill_dir = os.path.join(self.skills_dir, skill_name)
        if not os.path.isdir(skill_dir):
            return []

        return sorted(
            f for f in os.listdir(skill_dir)
            if f != "SKILL.md" and os.path.isfile(os.path.join(skill_dir, f))
        )

    def load_reference(self, skill_name: str, filename: str) -> str:
        """Read a reference file from a skill's directory.

        Args:
            skill_name: The skill identifier.
            filename: File to read (e.g. "editing.md"). Must be a direct
                      child of the skill directory — no path separators allowed.

        Returns:
            File content as a string.

        Raises:
            KeyError: If the skill is unknown.
            FileNotFoundError: If the reference file doesn't exist.
            ValueError: If the filename contains path separators.
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        # Prevent path traversal
        if os.sep in filename or "/" in filename:
            raise ValueError(f"Invalid filename: '{filename}'. Must be a plain filename, no paths.")

        full_path = os.path.join(self.skills_dir, skill_name, filename)
        if not os.path.isfile(full_path):
            available = self.list_references(skill_name)
            raise FileNotFoundError(
                f"Reference '{filename}' not found for skill '{skill_name}'. "
                f"Available: {available}"
            )

        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Level 3: tools (executed via skill_executor)
    # ------------------------------------------------------------------

    def get_tools(self, skill_name: str) -> list:
        """Return the AgentTool objects for a given skill.

        For composite skills, aggregates tools from all composed skills.
        Deduplicates by tool_name (first occurrence wins).
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        info = self._skills[skill_name]

        if info.get("type") == "composite":
            tools = []
            seen = set()
            for ref in info.get("compose", []):
                if ref not in self._skills:
                    logger.warning(
                        f"Composite skill '{skill_name}' references "
                        f"unknown skill '{ref}' — skipping"
                    )
                    continue
                for t in self._skills[ref]["tools"]:
                    tool_name = canonical_tool_name(t)
                    if tool_name not in seen:
                        tools.append(t)
                        seen.add(tool_name)
            return tools

        return list(info["tools"])

    def get_skill_type(self, skill_name: str) -> str:
        """Return the type of a skill: tool, instruction, or composite."""
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")
        return self._skills[skill_name].get("type", "tool")

    # ------------------------------------------------------------------
    # Level 3+: scripts (executed via skill_executor)
    # ------------------------------------------------------------------

    def list_scripts(self, skill_name: str) -> list[str]:
        """List available scripts in a skill's scripts/ directory.

        Returns filenames (.py, .sh) that can be executed
        via skill_executor(skill_name, script_name="...").

        Args:
            skill_name: The skill identifier

        Returns:
            List of script filenames
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        skill_dir = os.path.join(self.skills_dir, skill_name)
        scripts_dir = os.path.join(skill_dir, "scripts")

        if not os.path.isdir(scripts_dir):
            return []

        # Only allow .py and .sh files
        allowed_extensions = ('.py', '.sh')
        return sorted(
            f for f in os.listdir(scripts_dir)
            if f.endswith(allowed_extensions) and os.path.isfile(os.path.join(scripts_dir, f))
        )

    def get_script(self, skill_name: str, script_name: str) -> dict:
        """Get script info from a skill's scripts/ directory.

        Args:
            skill_name: The skill identifier
            script_name: Script filename (e.g., "cleanup_cache.py")

        Returns:
            Dict with:
                - path: Absolute path to script
                - executable: Whether script has execute permission

        Raises:
            KeyError: If skill or script not found
            ValueError: If script_name contains path separators (security)
        """
        if skill_name not in self._skills:
            raise KeyError(f"Unknown skill: '{skill_name}'. Available: {self.skill_names}")

        # Prevent path traversal
        if os.sep in script_name or "/" in script_name or ".." in script_name:
            raise ValueError(
                f"Invalid script name: '{script_name}'. "
                f"Must be a plain filename without path separators."
            )

        # Validate extension
        if not (script_name.endswith('.py') or script_name.endswith('.sh')):
            raise ValueError(
                f"Invalid script type: '{script_name}'. "
                f"Only .py and .sh files are allowed."
            )

        skill_dir = os.path.join(self.skills_dir, skill_name)
        scripts_dir = os.path.join(skill_dir, "scripts")
        script_path = os.path.join(scripts_dir, script_name)

        if not os.path.isfile(script_path):
            available = self.list_scripts(skill_name)
            raise KeyError(
                f"Script '{script_name}' not found in skill '{skill_name}'. "
                f"Available: {available}"
            )

        return {
            "path": os.path.abspath(script_path),
            "executable": os.access(script_path, os.X_OK),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter (between --- delimiters) and return the body."""
        if not content.startswith("---"):
            return content

        end_idx = content.find("---", 3)
        if end_idx == -1:
            return content

        return content[end_idx + 3:].strip()

    @staticmethod
    def _parse_frontmatter(filepath: str) -> dict:
        """Parse YAML frontmatter from a SKILL.md file.

        Supports simple key-value pairs and lists. Uses a lightweight
        regex-based parser to avoid requiring PyYAML as a dependency.

        Supported formats:
            name: my-skill
            description: One-line description
            type: composite
            compose:
              - skill-a
              - skill-b
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, IOError) as e:
            logger.warning(f"Could not read {filepath}: {e}")
            return {}

        if not content.startswith("---"):
            return {}

        end_idx = content.find("---", 3)
        if end_idx == -1:
            return {}

        frontmatter = content[3:end_idx].strip()
        meta: dict = {}
        current_key = None

        for line in frontmatter.split("\n"):
            line = line.rstrip()

            # List item: "  - value"
            if re.match(r"^\s+-\s+", line) and current_key is not None:
                value = re.sub(r"^\s+-\s+", "", line).strip()
                if not isinstance(meta.get(current_key), list):
                    meta[current_key] = []
                meta[current_key].append(value)
                continue

            # Key-value: "key: value"
            match = re.match(r"^(\w[\w-]*)\s*:\s*(.*)", line)
            if match:
                key = match.group(1)
                value = match.group(2).strip()
                current_key = key
                if value:
                    meta[key] = value
                else:
                    # Value on next lines (list or multiline)
                    meta[key] = None
                continue

        return meta
