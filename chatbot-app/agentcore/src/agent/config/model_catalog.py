"""Declarative model catalog and tier-based model selection."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

TASK_COMPLEXITIES = frozenset({"low", "medium", "high"})
DEFAULT_MAX_INPUT_TOKENS = 131_072
_ALLOWED_TRANSPORTS = frozenset({
    "bedrock",
    "bedrock_responses",
    "mantle_responses",
})
MODEL_ID_ALIASES = {
    "openai.gpt-5.6-sol": "us.openai.gpt-5.6-sol",
    "openai.gpt-5.6-terra": "us.openai.gpt-5.6-terra",
    "openai.gpt-5.6-luna": "us.openai.gpt-5.6-luna",
    "xai.grok-4.3": "us.xai.grok-4.6",
    "xai.grok-4.6": "us.xai.grok-4.6",
}


def normalize_model_id(model_id: str) -> str:
    """Map persisted legacy IDs to the canonical Bedrock Runtime profile ID."""
    return MODEL_ID_ALIASES.get(model_id, model_id)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_id: str
    provider: str
    family: str
    transport: str
    region: Optional[str]
    max_input_tokens: int
    rejects_temperature: bool


@dataclass(frozen=True)
class ModelSelection:
    policy: str
    task_complexity: Optional[str]
    provider: Optional[str]
    family: Optional[str]
    source_model_id: str
    effective_model_id: str
    catalog_revision: str
    applied: bool

    def as_record(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "taskComplexity": self.task_complexity or "",
            "provider": self.provider or "",
            "family": self.family or "",
            "sourceModelId": self.source_model_id,
            "effectiveModelId": self.effective_model_id,
            "catalogRevision": self.catalog_revision,
            "applied": self.applied,
        }


class ModelCatalog:
    def __init__(self, payload: dict[str, Any]):
        self.schema_version = int(payload.get("schemaVersion") or 0)
        if self.schema_version != 1:
            raise ValueError(
                f"Unsupported model catalog schemaVersion: {self.schema_version}"
            )
        self.revision = str(payload.get("revision") or "").strip()
        if not self.revision:
            raise ValueError("Model catalog revision is required")

        raw_matchers = payload.get("providerMatchers") or {}
        self.provider_matchers = {
            str(provider): tuple(str(value).lower() for value in values)
            for provider, values in raw_matchers.items()
        }
        raw_family_matchers = payload.get("familyMatchers") or {}
        self.family_matchers = {
            str(family): tuple(str(value).lower() for value in values)
            for family, values in raw_family_matchers.items()
        }

        self.models: dict[str, ModelSpec] = {}
        self.models_by_id: dict[str, ModelSpec] = {}
        for key, raw in (payload.get("models") or {}).items():
            if "maxInputTokens" not in raw:
                raise ValueError(
                    f"Model catalog entry {key} requires maxInputTokens"
                )
            spec = ModelSpec(
                key=str(key),
                model_id=str(raw.get("id") or ""),
                provider=str(raw.get("provider") or ""),
                family=str(raw.get("family") or ""),
                transport=str(raw.get("transport") or ""),
                region=str(raw["region"]) if raw.get("region") else None,
                max_input_tokens=int(raw["maxInputTokens"]),
                rejects_temperature=bool(raw.get("rejectsTemperature", False)),
            )
            self._validate_spec(spec)
            if spec.model_id in self.models_by_id:
                raise ValueError(f"Duplicate model ID in catalog: {spec.model_id}")
            self.models[spec.key] = spec
            self.models_by_id[spec.model_id] = spec

        self.selection_policies = payload.get("selectionPolicies") or {}
        self._validate_policies()

    @staticmethod
    def _validate_spec(spec: ModelSpec) -> None:
        if not spec.key or not spec.model_id or not spec.provider or not spec.family:
            raise ValueError(f"Incomplete model catalog entry: {spec.key}")
        if spec.transport not in _ALLOWED_TRANSPORTS:
            raise ValueError(
                f"Unsupported transport for {spec.key}: {spec.transport}"
            )
        if spec.transport == "mantle_responses" and not spec.region:
            raise ValueError(f"Mantle model {spec.key} requires a region")
        if spec.max_input_tokens <= 0:
            raise ValueError(f"Invalid maxInputTokens for {spec.key}")

    def _validate_policies(self) -> None:
        for policy_name, families in self.selection_policies.items():
            for family, tiers in families.items():
                missing = TASK_COMPLEXITIES - set(tiers)
                if missing:
                    raise ValueError(
                        f"Policy {policy_name}/{family} is missing tiers: "
                        f"{sorted(missing)}"
                    )
                for complexity, model_key in tiers.items():
                    if complexity not in TASK_COMPLEXITIES:
                        raise ValueError(
                            f"Policy {policy_name}/{family} has invalid tier: "
                            f"{complexity}"
                        )
                    spec = self.models.get(str(model_key))
                    if spec is None:
                        raise ValueError(
                            f"Policy {policy_name}/{family}/{complexity} "
                            f"references unknown model: {model_key}"
                        )
                    if spec.family != family:
                        raise ValueError(
                            f"Policy {policy_name}/{family}/{complexity} "
                            f"references family {spec.family}"
                        )

    def provider_for(self, model_id: str) -> Optional[str]:
        model_id = normalize_model_id(model_id)
        spec = self.models_by_id.get(model_id)
        if spec is not None:
            return spec.provider

        normalized = model_id.lower()
        for provider, matchers in self.provider_matchers.items():
            if any(matcher in normalized for matcher in matchers):
                return provider
        return None

    def family_for(self, model_id: str) -> Optional[str]:
        model_id = normalize_model_id(model_id)
        spec = self.models_by_id.get(model_id)
        if spec is not None:
            return spec.family

        normalized = model_id.lower()
        for family, matchers in self.family_matchers.items():
            if any(matcher in normalized for matcher in matchers):
                return family
        return None

    def resolve(
        self,
        policy: str,
        family: str,
        task_complexity: str,
    ) -> Optional[ModelSpec]:
        complexity = normalize_task_complexity(task_complexity)
        model_key = (
            self.selection_policies.get(policy, {})
            .get(family, {})
            .get(complexity)
        )
        return self.models.get(str(model_key)) if model_key else None


def normalize_task_complexity(
    value: Optional[str],
    *,
    default: Optional[str] = None,
) -> Optional[str]:
    normalized = str(value or default or "").strip().lower()
    if not normalized:
        return None
    if normalized not in TASK_COMPLEXITIES:
        raise ValueError("task_complexity must be low, medium, or high")
    return normalized


def _default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "model-catalog.json"


@lru_cache(maxsize=4)
def _load_catalog(path: str) -> ModelCatalog:
    with Path(path).open("r", encoding="utf-8") as handle:
        return ModelCatalog(json.load(handle))


def get_model_catalog() -> ModelCatalog:
    path = Path(
        os.environ.get("MODEL_CATALOG_PATH") or _default_catalog_path()
    ).resolve()
    return _load_catalog(str(path))


def resolve_general_subagent_model(
    parent_model_id: str,
    task_complexity: Optional[str],
) -> ModelSelection:
    catalog = get_model_catalog()
    canonical_parent_model_id = normalize_model_id(parent_model_id)
    complexity = normalize_task_complexity(task_complexity)
    provider = catalog.provider_for(canonical_parent_model_id)
    family = catalog.family_for(canonical_parent_model_id)
    if complexity is None or family is None:
        return ModelSelection(
            policy="general_subagent",
            task_complexity=complexity,
            provider=provider,
            family=family,
            source_model_id=parent_model_id,
            effective_model_id=canonical_parent_model_id,
            catalog_revision=catalog.revision,
            applied=False,
        )

    spec = catalog.resolve("general_subagent", family, complexity)
    effective_model_id = (
        spec.model_id if spec is not None else canonical_parent_model_id
    )
    return ModelSelection(
        policy="general_subagent",
        task_complexity=complexity,
        provider=provider,
        family=family,
        source_model_id=parent_model_id,
        effective_model_id=effective_model_id,
        catalog_revision=catalog.revision,
        applied=spec is not None,
    )


def resolve_code_agent_model(task_complexity: Optional[str]) -> ModelSelection:
    catalog = get_model_catalog()
    complexity = normalize_task_complexity(task_complexity, default="medium")
    spec = catalog.resolve("code_agent", "claude", complexity)
    if spec is None:
        raise ValueError(
            f"Model catalog has no code_agent/claude/{complexity} selection"
        )
    return ModelSelection(
        policy="code_agent",
        task_complexity=complexity,
        provider="anthropic",
        family="claude",
        source_model_id="",
        effective_model_id=spec.model_id,
        catalog_revision=catalog.revision,
        applied=True,
    )
