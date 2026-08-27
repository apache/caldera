from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping


TRUST_CONTRACT_VERSION = "1.0.0"


def operation_intent_digest(intent: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in intent.items() if key != "intent_digest"}
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IntentValidation:
    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    intent_digest: str = ""


class OperationIntentValidator:
    """Validate the versioned planning contract submitted before execution."""

    def validate(
        self, intent: Mapping[str, Any], *, expected_operation_id: str = ""
    ) -> IntentValidation:
        errors: list[str] = []
        required = (
            "schema_version",
            "intent_id",
            "operation_id",
            "objective",
            "created_at",
            "expires_at",
            "planner",
            "input_provenance",
            "range",
            "abilities",
            "intent_digest",
        )
        self._require_nonempty(intent, required, "intent", errors)
        if intent.get("schema_version") != TRUST_CONTRACT_VERSION:
            errors.append(f"intent.schema_version must be {TRUST_CONTRACT_VERSION}")
        if expected_operation_id and intent.get("operation_id") != expected_operation_id:
            errors.append("intent.operation_id does not match the operation")

        planner = intent.get("planner")
        if isinstance(planner, Mapping):
            self._require_nonempty(planner, ("name", "version", "model"), "planner", errors)
        elif planner is not None:
            errors.append("intent.planner must be an object")

        range_scope = intent.get("range")
        if isinstance(range_scope, Mapping):
            self._require_nonempty(
                range_scope, ("spec_digest", "targets", "privilege_ceiling"), "range", errors
            )
            self._validate_digest(range_scope.get("spec_digest"), "range.spec_digest", errors)
            targets = range_scope.get("targets")
            if targets and (
                not isinstance(targets, list)
                or not all(isinstance(target, str) and target for target in targets)
            ):
                errors.append("range.targets must be a list of target identifiers")
        elif range_scope is not None:
            errors.append("intent.range must be an object")

        abilities = intent.get("abilities")
        if isinstance(abilities, list) and abilities:
            seen: set[str] = set()
            for index, ability in enumerate(abilities):
                if not isinstance(ability, Mapping):
                    errors.append(f"abilities[{index}] must be an object")
                    continue
                self._require_nonempty(
                    ability, ("ability_id", "manifest_digest"), f"abilities[{index}]", errors
                )
                ability_id = str(ability.get("ability_id", ""))
                if ability_id in seen:
                    errors.append(f"abilities contains duplicate ability_id: {ability_id}")
                seen.add(ability_id)
                self._validate_digest(
                    ability.get("manifest_digest"), f"abilities[{index}].manifest_digest", errors
                )
        elif abilities is not None:
            errors.append("intent.abilities must be a non-empty list")

        provenance = intent.get("input_provenance")
        if isinstance(provenance, list) and provenance:
            for index, item in enumerate(provenance):
                if not isinstance(item, Mapping):
                    errors.append(f"input_provenance[{index}] must be an object")
                    continue
                self._require_nonempty(
                    item, ("kind", "digest"), f"input_provenance[{index}]", errors
                )
                self._validate_digest(item.get("digest"), f"input_provenance[{index}].digest", errors)
        elif provenance is not None:
            errors.append("intent.input_provenance must be a non-empty list")

        self._validate_expiry(intent.get("expires_at"), errors)
        digest = operation_intent_digest(intent)
        claimed_digest = intent.get("intent_digest")
        if claimed_digest and claimed_digest != digest:
            errors.append("intent.intent_digest does not match the operation intent")
        return IntentValidation(not errors, tuple(errors), digest)

    @staticmethod
    def build(
        *,
        operation_id: str,
        objective: str,
        planner: Mapping[str, str],
        input_provenance: Iterable[Mapping[str, str]],
        range_spec_digest: str,
        targets: Iterable[str],
        privilege_ceiling: str,
        abilities: Iterable[Mapping[str, str]],
        ttl_seconds: int = 3600,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        created_at = now or datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            raise ValueError("now must include a timezone")
        intent: dict[str, Any] = {
            "schema_version": TRUST_CONTRACT_VERSION,
            "intent_id": str(uuid.uuid4()),
            "operation_id": operation_id,
            "objective": objective,
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + timedelta(seconds=ttl_seconds)).isoformat(),
            "planner": dict(planner),
            "input_provenance": [dict(item) for item in input_provenance],
            "range": {
                "spec_digest": range_spec_digest,
                "targets": list(targets),
                "privilege_ceiling": privilege_ceiling,
            },
            "abilities": [dict(item) for item in abilities],
        }
        intent["intent_digest"] = operation_intent_digest(intent)
        return intent

    @staticmethod
    def _validate_expiry(value: Any, errors: list[str]) -> None:
        if not value:
            return
        try:
            expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            errors.append("intent.expires_at must be an ISO-8601 timestamp")
            return
        if expiry.tzinfo is None:
            errors.append("intent.expires_at must include a timezone")
        elif expiry <= datetime.now(timezone.utc):
            errors.append("intent.expires_at must be in the future")

    @staticmethod
    def _validate_digest(value: Any, field_name: str, errors: list[str]) -> None:
        if value and (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            errors.append(f"{field_name} must be a lowercase SHA-256 digest")

    @staticmethod
    def _require_nonempty(
        value: Mapping[str, Any], fields: tuple[str, ...], prefix: str, errors: list[str]
    ) -> None:
        for field_name in fields:
            item = value.get(field_name)
            if item is None or item == "" or item == [] or item == {}:
                errors.append(f"{prefix}.{field_name} is required")
