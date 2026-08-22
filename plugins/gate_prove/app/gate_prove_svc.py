from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from plugins.gate_prove.app.ability_manifest import AbilityManifestValidator
from plugins.gate_prove.app.authorization_lease import AuthorizationLeaseIssuer, command_digest
from plugins.gate_prove.app.attestation import OperationAttestor
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
        authorization_key: str = "",
        attestation_key: str = "",
    ) -> None:
        self.prove_token = prove_token or os.environ.get("CALDERA_PROVE_TOKEN", "")
        self.ledger = ledger if ledger is not None else OperationLedger(Path(ledger_path))
        self.manifest_validator = manifest_validator or AbilityManifestValidator()
        lease_key = authorization_key or os.environ.get("CALDERA_AUTHORIZATION_KEY", "")
        self.lease_issuer = AuthorizationLeaseIssuer(lease_key)
        evidence_key = attestation_key or os.environ.get("CALDERA_ATTESTATION_KEY", "")
        self.attestor = OperationAttestor(evidence_key, self.ledger)

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
        cleanup: bool = False,
        authorization_lease: str = "",
        ability_digest: str = "",
        target: str = "",
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
            lease = self.lease_issuer.verify(
                authorization_lease,
                operation_id=operation_id,
                ability_id=ability_id,
                technique_id=technique_id,
                ability_digest=ability_digest,
                target=target,
            )
            executions = self._lease_execution_count(lease.claims.get("jti", ""))
            if lease.valid and executions < lease.claims["max_executions"]:
                lid, rhash = self.ledger.record(
                    operation_id,
                    ability_id,
                    technique_id,
                    "allow",
                    True,
                    "authorization_lease_verified",
                    metadata={
                        "lease_id": lease.claims["jti"],
                        "approver": lease.claims["approver"],
                        "ability_digest": ability_digest,
                        "target": target,
                        "execution": executions + 1,
                        "max_executions": lease.claims["max_executions"],
                    },
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
                    reason="authorization_lease_verified",
                    ledger_id=lid,
                    receipt_hash=rhash,
                )

            reason = lease.reason
            if lease.valid:
                reason = "authorization_lease_budget_exhausted"
            # Unapproved or invalid high-blast activity falls back to safe simulation.
            lid, rhash = self.ledger.record(
                operation_id, ability_id, technique_id, "simulate", True, reason
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
                reason=reason,
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

    def issue_authorization_lease(self, **claims: Any) -> str:
        """Create a lease for an approval UI or other trusted control-plane caller."""
        return self.lease_issuer.issue(**claims)

    def attest_operation(self, operation: Any, **evidence: Any) -> dict[str, Any]:
        """Build an integrity-protected operation evidence bundle."""
        return self.attestor.build(operation, **evidence)

    def verify_attestation(self, bundle: dict[str, Any]) -> bool:
        return self.attestor.verify(bundle)

    def _lease_execution_count(self, lease_id: str) -> int:
        if not lease_id:
            return 0
        return sum(
            1
            for entry in self.ledger.entries
            if entry.get("reason") == "authorization_lease_verified"
            and entry.get("metadata", {}).get("lease_id") == lease_id
        )

    def evaluate_link(self, operation: Any, link: Any) -> AbilityDecision:
        """Evaluate a Caldera link immediately before it enters the chain."""
        ability = link.ability
        digest = command_digest(getattr(link, "command", ""))
        target = str(getattr(link, "paw", "") or getattr(link, "host", ""))
        return self.evaluate_ability(
            operation_id=operation.id,
            ability_id=ability.ability_id,
            technique_id=ability.technique_id,
            technique_name=ability.technique_name,
            approved=bool(getattr(link, "gate_prove_approved", False)),
            offered_token=str(getattr(link, "gate_prove_token", "")),
            simulate=bool(getattr(link, "gate_prove_simulate", False)),
            ability_manifest=getattr(ability, "gate_prove_manifest", None),
            provenance=getattr(ability, "gate_prove_provenance", None),
            cleanup=bool(getattr(link, "cleanup", False)),
            authorization_lease=str(getattr(link, "gate_prove_authorization_lease", "")),
            ability_digest=digest,
            target=target,
        )

    def govern_link(self, operation: Any, link: Any) -> AbilityDecision:
        """Apply a gate decision to a link before Caldera queues it for an agent."""
        decision = self.evaluate_link(operation, link)
        link.gate_prove_decision = decision.to_dict()
        if decision.disposition != "allow":
            link.status = link.states["DISCARD"]
        return decision
