from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Add plugins path to sys.path
_plugins_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(_plugins_dir) not in sys.path:
    sys.path.insert(0, str(_plugins_dir))

from plugins.gate_prove.app.gate_prove_svc import GateProveService
from plugins.gate_prove.app.ledger import OperationLedger


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


if __name__ == "__main__":
    unittest.main()
