from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, List, Literal

GateDisposition = Literal["allow", "simulate", "deny"]
TechniqueTier = Literal["discovery", "collection", "lateral_movement", "persistence", "destructive", "defense_evasion"]

DESTRUCTIVE_ATTACK_PATTERNS = {
    "T1562",  # Impair Defenses
    "T1070",  # Indicator Removal
    "T1485",  # Data Destruction
    "T1486",  # Data Encrypted for Impact
    "T1489",  # Service Stop
    "T1529",  # System Shutdown/Reboot
    "T1561",  # Disk Wipe
}

INSTANT_AUDIT_CTA = "https://a2zsoc.com/productized-services#caldera-arsenal-tripwire"
CONSULTATION_CTA = "https://a2zsoc.com/consultation"


@dataclass(frozen=True)
class AbilityDecision:
    """Evaluation result for an adversary ability in a Caldera operation."""

    operation_id: str
    ability_id: str
    technique_id: str
    technique_name: str
    disposition: GateDisposition
    allowed: bool
    requires_hitl: bool
    never_equate_intent_to_approval: bool
    reason: str
    ledger_id: str
    receipt_hash: str
    kill_switch: bool = False
    compliance_mapping: list[str] = field(
        default_factory=lambda: ["NIST_CSF_DE.CM", "SOC2_CC7.2", "ISO_27001_A.12.6.1"]
    )
    instant_audit: str = INSTANT_AUDIT_CTA
    consultation: str = CONSULTATION_CTA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
