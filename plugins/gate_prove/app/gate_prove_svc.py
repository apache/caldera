from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any, Dict, Optional

from plugins.gate_prove.app.ability_manifest import AbilityManifestValidator
from plugins.gate_prove.app.ledger import OperationLedger
from plugins.gate_prove.app.schema import (
    DESTRUCTIVE_ATTACK_PATTERNS,
    AbilityDecision,
    GateDisposition,
)


class GateProveService:
    """Service to evaluate adversary abilities before dispatch in Caldera operations."""

    def __init__(
        self,
        prove_token: str = "",
        ledger: OperationLedger | None = None,
        ledger_path: str = "artifacts/caldera_action_ledger.jsonl",
        manifest_validator: AbilityManifestValidator | None = None,
    ) -> None:
        self.prove_token = prove_token or os.environ.get("CALDERA_PROVE_TOKEN", "")
        self.ledger = ledger if ledger is not None else OperationLedger(Path(ledger_path))
        self.manifest_validator = manifest_validator or AbilityManifestValidator()

    def is_kill_switch_engaged(self) -> bool:
        flag = os.environ.get("CALDERA_KILL_SWITCH", "").strip().lower()
        if flag in {"1", "true", "yes", "on"}:
            return True
        kill_file = Path(os.environ.get("CALDERA_KILL_SWITCH_FILE", "artifacts/KILL"))
        return kill_file.exists()

    def is_destructive_technique(self, technique_id: str) -> bool:
        tech_upper = technique_id.upper().strip()
        for pattern in DESTRUCTIVE_ATTACK_PATTERNS:
            if tech_upper.startswith(pattern):
                return True
        return False

    def evaluate_ability(
        self,
        operation_id: str,
        ability_id: str,
        technique_id: str,
        technique_name: str = "",
        approved: bool = False,
        offered_token: str = "",
        simulate: bool = False,
        ability_manifest: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> AbilityDecision:
        # 1. Kill Switch Check
        if self.is_kill_switch_engaged():
            lid, rhash = self.ledger.record(
                operation_id, ability_id, technique_id, "deny", False, "kill_switch_engaged"
            )
            return AbilityDecision(
                operation_id=operation_id,
                ability_id=ability_id,
                technique_id=technique_id,
                technique_name=technique_name,
                disposition="deny",
                allowed=False,
                requires_hitl=True,
                never_equate_intent_to_approval=True,
                reason="kill_switch_engaged",
                ledger_id=lid,
                receipt_hash=rhash,
                kill_switch=True,
            )

        # 2. AI-generated abilities must carry a complete, untampered safety envelope.
        if ability_manifest is not None or provenance is not None:
            validation = self.manifest_validator.validate(
                ability_manifest or {},
                provenance or {},
                expected_ability_id=ability_id,
                expected_technique_id=technique_id,
            )
            if not validation.valid:
                reason = "invalid_ability_manifest:" + ";".join(validation.errors)
                lid, rhash = self.ledger.record(
                    operation_id,
                    ability_id,
                    technique_id,
                    "deny",
                    False,
                    reason,
                    metadata={"content_hash": validation.content_hash, "validation_errors": list(validation.errors)},
                )
                return AbilityDecision(
                    operation_id=operation_id,
                    ability_id=ability_id,
                    technique_id=technique_id,
                    technique_name=technique_name,
                    disposition="deny",
                    allowed=False,
                    requires_hitl=True,
                    never_equate_intent_to_approval=True,
                    reason=reason,
                    ledger_id=lid,
                    receipt_hash=rhash,
                )

        # 3. Simulation Mode Check
        if simulate:
            lid, rhash = self.ledger.record(
                operation_id, ability_id, technique_id, "simulate", True, "simulation_mode_requested"
            )
            return AbilityDecision(
                operation_id=operation_id,
                ability_id=ability_id,
                technique_id=technique_id,
                technique_name=technique_name,
                disposition="simulate",
                allowed=True,
                requires_hitl=False,
                never_equate_intent_to_approval=True,
                reason="simulation_mode_requested",
                ledger_id=lid,
                receipt_hash=rhash,
            )

        # 4. Destructive / High-Blast Techniques Check
        if self.is_destructive_technique(technique_id):
            token_valid = bool(
                self.prove_token
                and offered_token
                and hmac.compare_digest(self.prove_token.strip(), offered_token.strip())
            )
            if approved and token_valid:
                lid, rhash = self.ledger.record(
                    operation_id, ability_id, technique_id, "allow", True, "hitl_token_verified"
                )
                return AbilityDecision(
                    operation_id=operation_id,
                    ability_id=ability_id,
                    technique_id=technique_id,
                    technique_name=technique_name,
                    disposition="allow",
                    allowed=True,
                    requires_hitl=True,
                    never_equate_intent_to_approval=True,
                    reason="hitl_token_verified",
                    ledger_id=lid,
                    receipt_hash=rhash,
                )

            # Unapproved destructive ability falls back to safe simulation
            lid, rhash = self.ledger.record(
                operation_id, ability_id, technique_id, "simulate", True, "unapproved_destructive_simulated"
            )
            return AbilityDecision(
                operation_id=operation_id,
                ability_id=ability_id,
                technique_id=technique_id,
                technique_name=technique_name,
                disposition="simulate",
                allowed=True,
                requires_hitl=True,
                never_equate_intent_to_approval=True,
                reason="unapproved_destructive_simulated",
                ledger_id=lid,
                receipt_hash=rhash,
            )

        # 5. Standard Non-Destructive Abilities (Discovery / Collection / Baseline)
        lid, rhash = self.ledger.record(
            operation_id, ability_id, technique_id, "allow", True, "safe_emulation_allowed"
        )
        return AbilityDecision(
            operation_id=operation_id,
            ability_id=ability_id,
            technique_id=technique_id,
            technique_name=technique_name,
            disposition="allow",
            allowed=True,
            requires_hitl=False,
            never_equate_intent_to_approval=True,
            reason="safe_emulation_allowed",
            ledger_id=lid,
            receipt_hash=rhash,
        )
