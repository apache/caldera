from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add plugins path to sys.path
_plugins_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(_plugins_dir) not in sys.path:
    sys.path.insert(0, str(_plugins_dir))

from plugins.gate_prove.app.gate_prove_svc import GateProveService
from plugins.gate_prove.app.ability_manifest import ability_content_hash
from plugins.gate_prove.app.ledger import OperationLedger
from plugins.gate_prove.hook import enable


class TestGateProve(unittest.TestCase):
    def setUp(self) -> None:
        self.service = GateProveService(prove_token="secret-caldera-token", ledger=OperationLedger())

    def test_safe_ability_allowed(self) -> None:
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id="ab_discovery",
            technique_id="T1082",
            technique_name="System Information Discovery",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.disposition, "allow")
        self.assertFalse(decision.requires_hitl)

    def test_unapproved_destructive_ability_simulated(self) -> None:
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id="ab_impair",
            technique_id="T1562.001",
            technique_name="Disable Security Tools",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.disposition, "simulate")
        self.assertEqual(decision.reason, "unapproved_destructive_simulated")
        self.assertTrue(decision.never_equate_intent_to_approval)

    def test_destructive_ability_allowed_with_valid_hitl_token(self) -> None:
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id="ab_impair",
            technique_id="T1562.001",
            approved=True,
            offered_token="secret-caldera-token",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.disposition, "allow")
        self.assertEqual(decision.reason, "hitl_token_verified")

    @staticmethod
    def generated_ability() -> dict:
        return {
            "ability_id": "generated-1",
            "name": "Collect range canary",
            "description": "Read a synthetic canary in the authorized range.",
            "tactic": "collection",
            "technique_id": "T1005",
            "technique_name": "Data from Local System",
            "platforms": {"linux": {"sh": {"command": "read-range-canary"}}},
            "privilege": "User",
            "parsers": [{"module": "plugins.stockpile.app.parsers.basic"}],
            "cleanup": ["remove-range-canary-artifact"],
            "scope": {
                "targets": ["range-host-1"],
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "max_executions": 1,
            },
        }

    def test_complete_generated_ability_is_allowed(self) -> None:
        ability = self.generated_ability()
        provenance = {
            "generator": "mitre-mcp",
            "model": "test-model",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": ability_content_hash(ability),
        }
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id=ability["ability_id"],
            technique_id=ability["technique_id"],
            ability_manifest=ability,
            provenance=provenance,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.disposition, "allow")

    def test_generated_ability_without_cleanup_fails_closed(self) -> None:
        ability = self.generated_ability()
        ability["cleanup"] = []
        provenance = {
            "generator": "mitre-mcp",
            "model": "test-model",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": ability_content_hash(ability),
        }
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id=ability["ability_id"],
            technique_id=ability["technique_id"],
            ability_manifest=ability,
            provenance=provenance,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.disposition, "deny")
        self.assertIn("ability.cleanup is required", decision.reason)

    def test_generated_ability_hash_mismatch_fails_closed(self) -> None:
        ability = self.generated_ability()
        provenance = {
            "generator": "mitre-mcp",
            "model": "test-model",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": "0" * 64,
        }
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id=ability["ability_id"],
            technique_id=ability["technique_id"],
            ability_manifest=ability,
            provenance=provenance,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("content_hash does not match", decision.reason)

    def test_generated_ability_cannot_be_replayed_for_another_dispatch(self) -> None:
        ability = self.generated_ability()
        provenance = {
            "generator": "mitre-mcp",
            "model": "test-model",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": ability_content_hash(ability),
        }
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id="different-ability",
            technique_id=ability["technique_id"],
            ability_manifest=ability,
            provenance=provenance,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("does not match the dispatch request", decision.reason)

    def test_expired_generated_ability_scope_fails_closed(self) -> None:
        ability = self.generated_ability()
        ability["scope"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        provenance = {
            "generator": "mitre-mcp",
            "model": "test-model",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": ability_content_hash(ability),
        }
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id=ability["ability_id"],
            technique_id=ability["technique_id"],
            ability_manifest=ability,
            provenance=provenance,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("scope.expires_at must be in the future", decision.reason)


class TestOperationLedger(unittest.TestCase):
    def test_ledger_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "ledger.jsonl"
            ledger = OperationLedger(ledger_file)

            ledger.record("op-1", "ab-1", "T1082", "allow", True, "ok")
            ledger.record("op-1", "ab-2", "T1562", "simulate", True, "simulated")
            ledger.record("op-1", "ab-3", "T1485", "allow", True, "verified")

            self.assertEqual(len(ledger.entries), 3)
            self.assertTrue(ledger.verify_chain())

            # Verify persistent reload
            reloaded = OperationLedger(ledger_file)
            self.assertEqual(len(reloaded.entries), 3)
            self.assertTrue(reloaded.verify_chain())


class TestPluginHook(unittest.IsolatedAsyncioTestCase):
    async def test_enable_registers_configured_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = str(Path(tmp) / "ledger.jsonl")
            services = {}
            with patch.dict(
                "os.environ",
                {
                    "CALDERA_PROVE_TOKEN": "configured-token",
                    "CALDERA_GATE_PROVE_LEDGER": ledger_path,
                },
                clear=False,
            ):
                await enable(services)

            service = services["gate_prove_svc"]
            self.assertEqual(service.prove_token, "configured-token")
            self.assertEqual(service.ledger.path, Path(ledger_path))


if __name__ == "__main__":
    unittest.main()
