"""Registry API client for AgentCore Registry.

Queries Registry at startup to build the service catalog:
  - Skill records: discovery (tool-to-skill mapping, source routing)
  - MCP/A2A records: endpoint + credential for runtime invocation

Replaces the local YAML loader (registry/loader.py) and SSM-based
endpoint discovery as the single source of truth.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)


@dataclass
class SkillRecord:
    name: str
    description: str
    source: str = "builtin"
    source_record: Optional[str] = None
    tools: List[str] = field(default_factory=list)
    skill_md: str = ""
    endpoint_url: str = ""


class RegistryClient:
    """Read-only client for AgentCore Registry discovery."""

    def __init__(self, registry_id: str, region: Optional[str] = None):
        self._region = region or os.environ.get("AWS_REGION", "us-west-2")
        self._registry_id = registry_id
        self._client = boto3.client(
            "bedrock-agentcore-control", region_name=self._region
        )

        self._skills: Dict[str, SkillRecord] = {}
        self._tool_skill_map: Dict[str, str] = {}
        self._mcp_skills: set = set()
        self._a2a_tools: Dict[str, str] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _ensure_loaded(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_all()
            self._loaded = True

    def _load_all(self):
        logger.info(f"[Registry] Loading records from registry {self._registry_id}")

        records = self._list_all_records()
        for record in records:
            if record.get("descriptorType") == "AGENT_SKILLS":
                self._process_skill_record(record.get("name", ""), record.get("recordId", ""))

        for skill_name, skill in self._skills.items():
            for tool in skill.tools:
                if tool not in self._tool_skill_map:
                    self._tool_skill_map[tool] = skill_name
            if skill.source == "mcp":
                self._mcp_skills.add(skill_name)
            elif skill.source == "a2a":
                self._a2a_tools[f"agentcore_{skill_name}"] = skill_name

        logger.info(f"[Registry] Loaded {len(self._skills)} skills")

    def _list_all_records(self) -> List[dict]:
        records = []
        for status in ("APPROVED",):
            token = None
            while True:
                kwargs = {
                    "registryId": self._registry_id,
                    "status": status,
                    "maxResults": 100,
                }
                if token:
                    kwargs["nextToken"] = token
                resp = self._client.list_registry_records(**kwargs)
                records.extend(resp.get("registryRecords", []))
                token = resp.get("nextToken")
                if not token:
                    break
        return records

    def _process_skill_record(self, name: str, record_id: str):
        try:
            detail = self._client.get_registry_record(
                registryId=self._registry_id, recordId=record_id
            )
        except Exception as e:
            logger.warning(f"[Registry] Failed to get skill record {name}: {e}")
            return

        descriptors = detail.get("descriptors", {})
        agent_skills = descriptors.get("agentSkills", {})

        skill_md = ""
        skill_md_obj = agent_skills.get("skillMd", {})
        if skill_md_obj:
            skill_md = skill_md_obj.get("inlineContent", "")

        source = "builtin"
        source_record = None
        tools: List[str] = []
        endpoint_url = ""

        skill_def = agent_skills.get("skillDefinition", {})
        if skill_def:
            content = skill_def.get("inlineContent", "")
            if content:
                try:
                    parsed = json.loads(content)
                    meta = parsed.get("_meta", {})
                    source = meta.get("source", "builtin")
                    source_record = meta.get("sourceRecord")
                    tools = meta.get("tools", [])
                    endpoint_url = meta.get("endpointUrl", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

        self._skills[name] = SkillRecord(
            name=name,
            description=detail.get("description", ""),
            source=source,
            source_record=source_record,
            tools=tools,
            skill_md=skill_md,
            endpoint_url=endpoint_url,
        )
        logger.debug(
            f"[Registry] Skill: {name} source={source} "
            f"endpoint={'yes' if endpoint_url else 'no'} tools={len(tools)}"
        )

    # -- Public API --

    @property
    def skills(self) -> Dict[str, SkillRecord]:
        self._ensure_loaded()
        return self._skills

    def tool_to_skill_map(self) -> Dict[str, str]:
        self._ensure_loaded()
        return self._tool_skill_map

    def mcp_runtime_skills(self) -> set:
        self._ensure_loaded()
        return self._mcp_skills

    def a2a_skill_tools(self) -> Dict[str, str]:
        self._ensure_loaded()
        return self._a2a_tools

    def _first_endpoint_by_source(self, source: str) -> Optional[str]:
        self._ensure_loaded()
        for skill in self._skills.values():
            if skill.source == source and skill.endpoint_url:
                return skill.endpoint_url
        return None

    def get_gateway_url(self) -> Optional[str]:
        return self._first_endpoint_by_source("gateway")

    def get_mcp_endpoint(self, source: str) -> Optional[str]:
        """Return the first registered endpoint for an MCP source."""
        if source not in {"gateway", "mcp"}:
            raise ValueError(f"Unsupported MCP source: {source}")
        return self._first_endpoint_by_source(source)

    def get_a2a_endpoint_url(self, agent_name: str) -> Optional[str]:
        """Return endpoint URL for a specific A2A agent."""
        self._ensure_loaded()
        skill = self._skills.get(agent_name)
        if skill and skill.source == "a2a":
            return skill.endpoint_url
        return None

    def get_a2a_skill(self, agent_name: str) -> Optional[SkillRecord]:
        """Return SkillRecord for a specific A2A agent."""
        self._ensure_loaded()
        skill = self._skills.get(agent_name)
        if skill and skill.source == "a2a":
            return skill
        return None


# -- Singleton --

_client_instance: Optional[RegistryClient] = None
_client_lock = threading.Lock()


def get_registry_client() -> Optional[RegistryClient]:
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    with _client_lock:
        if _client_instance is not None:
            return _client_instance

        registry_id = os.environ.get("REGISTRY_ID")
        if not registry_id:
            project = os.environ.get("PROJECT_NAME", "strands-agent-chatbot")
            env = os.environ.get("ENVIRONMENT", "dev")
            region = os.environ.get("AWS_REGION", "us-west-2")
            try:
                ssm = boto3.client("ssm", region_name=region)
                resp = ssm.get_parameter(
                    Name=f"/{project}/{env}/registry/registry-id"
                )
                registry_id = resp["Parameter"]["Value"]
            except Exception as e:
                logger.warning(f"[Registry] Cannot resolve registry ID: {e}")
                return None

        _client_instance = RegistryClient(registry_id)
        return _client_instance
