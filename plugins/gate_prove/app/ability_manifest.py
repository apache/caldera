from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


SAFETY_RELEVANT_FIELDS = (
    "ability_id",
    "name",
    "description",
    "tactic",
    "technique_id",
    "technique_name",
    "platforms",
    "privilege",
    "parsers",
    "cleanup",
    "scope",
)


def ability_content_hash(ability: Mapping[str, Any]) -> str:
    """Return a stable digest over fields that affect execution and cleanup."""
    canonical = {field: ability.get(field) for field in SAFETY_RELEVANT_FIELDS}
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ManifestValidation:
    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    content_hash: str = ""


class AbilityManifestValidator:
    """Validate the safety envelope attached to an AI-generated ability.

    The validator deliberately does not judge whether a command is malicious or
    effective. It proves that generated content has the minimum metadata needed
    for bounded, attributable and reversible adversary emulation.
    """

    REQUIRED_ABILITY_FIELDS = (
        "ability_id",
        "name",
        "description",
        "tactic",
        "technique_id",
        "technique_name",
        "platforms",
        "parsers",
        "cleanup",
        "scope",
    )
    REQUIRED_PROVENANCE_FIELDS = ("generator", "model", "created_at", "content_hash")
    REQUIRED_SCOPE_FIELDS = ("targets", "expires_at", "max_executions")

    def validate(
        self,
        ability: Mapping[str, Any],
        provenance: Mapping[str, Any],
        *,
        expected_ability_id: str = "",
        expected_technique_id: str = "",
    ) -> ManifestValidation:
        errors: list[str] = []

        self._require_nonempty(ability, self.REQUIRED_ABILITY_FIELDS, "ability", errors)
        self._require_nonempty(provenance, self.REQUIRED_PROVENANCE_FIELDS, "provenance", errors)

        scope = ability.get("scope")
        if isinstance(scope, Mapping):
            self._require_nonempty(scope, self.REQUIRED_SCOPE_FIELDS, "scope", errors)
            max_executions = scope.get("max_executions")
            if not isinstance(max_executions, int) or isinstance(max_executions, bool) or max_executions < 1:
                errors.append("scope.max_executions must be a positive integer")
            targets = scope.get("targets")
            if targets and (not isinstance(targets, list) or not all(isinstance(item, str) for item in targets)):
                errors.append("scope.targets must be a list of target identifiers")
            self._validate_expiry(scope.get("expires_at"), errors)
        elif scope is not None:
            errors.append("ability.scope must be an object")

        if "platforms" in ability and not isinstance(ability.get("platforms"), Mapping):
            errors.append("ability.platforms must be an object")
        if "parsers" in ability and not isinstance(ability.get("parsers"), list):
            errors.append("ability.parsers must be a list")
        if "cleanup" in ability and not isinstance(ability.get("cleanup"), list):
            errors.append("ability.cleanup must be a list")

        digest = ability_content_hash(ability)
        claimed_hash = provenance.get("content_hash")
        if claimed_hash and claimed_hash != digest:
            errors.append("provenance.content_hash does not match the ability manifest")

        if expected_ability_id and ability.get("ability_id") != expected_ability_id:
            errors.append("ability.ability_id does not match the dispatch request")
        if expected_technique_id and ability.get("technique_id") != expected_technique_id:
            errors.append("ability.technique_id does not match the dispatch request")

        return ManifestValidation(valid=not errors, errors=tuple(errors), content_hash=digest)

    @staticmethod
    def _validate_expiry(value: Any, errors: list[str]) -> None:
        if not value:
            return
        try:
            expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            errors.append("scope.expires_at must be an ISO-8601 timestamp")
            return
        if expiry.tzinfo is None:
            errors.append("scope.expires_at must include a timezone")
            return
        if expiry <= datetime.now(timezone.utc):
            errors.append("scope.expires_at must be in the future")

    @staticmethod
    def _require_nonempty(
        value: Mapping[str, Any], fields: tuple[str, ...], prefix: str, errors: list[str]
    ) -> None:
        for field_name in fields:
            item = value.get(field_name)
            if item is None or item == "" or item == [] or item == {}:
                errors.append(f"{prefix}.{field_name} is required")
