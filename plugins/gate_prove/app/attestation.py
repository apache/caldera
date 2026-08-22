from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from plugins.gate_prove.app.authorization_lease import command_digest
from plugins.gate_prove.app.ledger import OperationLedger


DETECTION_OUTCOMES = {
    "detected",
    "visibility_gap",
    "detection_gap",
    "soc_workflow_gap",
    "invalid_test",
}


class OperationAttestor:
    """Build and verify portable evidence for a governed Caldera operation."""

    SCHEMA_VERSION = "1.0.0"

    def __init__(self, secret: str, ledger: OperationLedger) -> None:
        self._secret = secret.encode("utf-8")
        self.ledger = ledger

    def build(
        self,
        operation: Any,
        *,
        detection_results: Iterable[dict[str, Any]] = (),
        input_provenance: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        if not self._secret:
            raise ValueError("attestation key is not configured")

        links = [self._link_evidence(link) for link in getattr(operation, "chain", [])]
        link_ids = {item["link_id"] for item in links}
        detections = [self._normalize_detection(item, link_ids) for item in detection_results]
        ledger_valid = self.ledger.verify_chain()
        bundle = {
            "schema_version": self.SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "operation": {
                "id": str(operation.id),
                "name": str(getattr(operation, "name", "")),
                "state": str(getattr(operation, "state", "")),
                "started_at": self._timestamp(getattr(operation, "start", None)),
                "finished_at": self._timestamp(getattr(operation, "finish", None)),
            },
            "input_provenance": list(input_provenance),
            "links": links,
            "detection_results": detections,
            "ledger": {
                "valid": ledger_valid,
                "entries": len(self.ledger.entries),
                "root_hash": self.ledger.entries[-1]["receipt_hash"] if self.ledger.entries else "0" * 64,
            },
        }
        bundle["summary"] = self._summary(operation, links, detections, ledger_valid)
        canonical = self._canonical(bundle)
        bundle["bundle_hash"] = hashlib.sha256(canonical).hexdigest()
        bundle["signature"] = self._encode(hmac.new(self._secret, canonical, hashlib.sha256).digest())
        return bundle

    def verify(self, bundle: dict[str, Any]) -> bool:
        if not self._secret:
            return False
        supplied_hash = bundle.get("bundle_hash", "")
        supplied_signature = bundle.get("signature", "")
        unsigned = {key: value for key, value in bundle.items() if key not in {"bundle_hash", "signature"}}
        canonical = self._canonical(unsigned)
        expected_hash = hashlib.sha256(canonical).hexdigest()
        expected_signature = self._encode(hmac.new(self._secret, canonical, hashlib.sha256).digest())
        return hmac.compare_digest(supplied_hash, expected_hash) and hmac.compare_digest(
            supplied_signature, expected_signature
        )

    @staticmethod
    def _link_evidence(link: Any) -> dict[str, Any]:
        ability = link.ability
        decision = getattr(link, "gate_prove_decision", None)
        return {
            "link_id": str(link.id),
            "target": str(getattr(link, "paw", "") or getattr(link, "host", "")),
            "ability_id": str(ability.ability_id),
            "technique_id": str(ability.technique_id),
            "command_digest": command_digest(getattr(link, "command", "")),
            "cleanup": bool(getattr(link, "cleanup", False)),
            "cleanup_state": OperationAttestor._cleanup_state(link, decision),
            "status": getattr(link, "status", None),
            "finished_at": OperationAttestor._timestamp(getattr(link, "finish", None)),
            "gate_decision": decision,
        }

    @staticmethod
    def _cleanup_state(link: Any, decision: dict[str, Any] | None) -> str:
        if not getattr(link, "cleanup", False):
            return "not_applicable"
        if not decision or decision.get("disposition") != "allow":
            return "blocked"
        states = getattr(link, "states", {})
        if getattr(link, "status", None) == states.get("SUCCESS", 0) and getattr(link, "finish", None):
            return "verified"
        if getattr(link, "status", None) in {states.get("ERROR", 1), states.get("TIMEOUT", 124)}:
            return "failed"
        return "pending"

    @staticmethod
    def _normalize_detection(result: dict[str, Any], link_ids: set[str]) -> dict[str, Any]:
        outcome = result.get("outcome")
        if outcome not in DETECTION_OUTCOMES:
            raise ValueError(f"unsupported detection outcome: {outcome}")
        link_id = str(result.get("link_id", ""))
        if link_id not in link_ids:
            raise ValueError(f"detection evidence references unknown link: {link_id}")
        if outcome == "detected" and not all(
            result.get(field_name) for field_name in ("telemetry_source", "detection_id", "evidence_digest")
        ):
            raise ValueError("detected outcome requires telemetry_source, detection_id, and evidence_digest")
        return {
            "link_id": link_id,
            "outcome": outcome,
            "telemetry_source": str(result.get("telemetry_source", "")),
            "detection_id": str(result.get("detection_id", "")),
            "evidence_digest": str(result.get("evidence_digest", "")),
        }

    @staticmethod
    def _summary(
        operation: Any,
        links: list[dict[str, Any]],
        detections: list[dict[str, Any]],
        ledger_valid: bool,
    ) -> dict[str, Any]:
        cleanup_states = [item["cleanup_state"] for item in links if item["cleanup"]]
        missing_decisions = sum(1 for item in links if not item["gate_decision"])
        expected_detection_links = {
            item["link_id"]
            for item in links
            if not item["cleanup"]
            and item["gate_decision"]
            and item["gate_decision"].get("disposition") == "allow"
        }
        classified_detection_links = {result["link_id"] for result in detections}
        missing_detection_links = sorted(expected_detection_links - classified_detection_links)
        if not ledger_valid:
            disposition = "evidence_invalid"
        elif not getattr(operation, "finish", None):
            disposition = "in_progress"
        elif missing_decisions or missing_detection_links:
            disposition = "evidence_incomplete"
        elif any(state in {"pending", "failed", "blocked"} for state in cleanup_states):
            disposition = "cleanup_incomplete"
        elif any(result["outcome"] != "detected" for result in detections):
            disposition = "completed_with_detection_gaps"
        else:
            disposition = "completed_verified"
        return {
            "disposition": disposition,
            "links": len(links),
            "missing_gate_decisions": missing_decisions,
            "missing_detection_links": missing_detection_links,
            "cleanup": {state: cleanup_states.count(state) for state in sorted(set(cleanup_states))},
            "detections": {
                outcome: sum(1 for result in detections if result["outcome"] == outcome)
                for outcome in sorted(DETECTION_OUTCOMES)
            },
        }

    @staticmethod
    def _canonical(value: dict[str, Any]) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    @staticmethod
    def _timestamp(value: Any) -> str | None:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
