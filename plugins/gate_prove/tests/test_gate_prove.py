from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

# Add plugins path to sys.path
_plugins_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(_plugins_dir) not in sys.path:
    sys.path.insert(0, str(_plugins_dir))

from plugins.gate_prove.app.gate_prove_svc import GateProveService
from plugins.gate_prove.app.ability_manifest import ability_content_hash
from plugins.gate_prove.app.authorization_lease import AuthorizationLeaseIssuer, command_digest
from plugins.gate_prove.app.ledger import OperationLedger
from plugins.gate_prove.hook import enable


class TestGateProve(unittest.TestCase):
    def setUp(self) -> None:
        self.service = GateProveService(
            authorization_key="lease-signing-key", ledger=OperationLedger()
        )

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
        self.assertEqual(decision.reason, "authorization_lease_missing")
        self.assertTrue(decision.never_equate_intent_to_approval)

    def test_destructive_ability_allowed_with_scoped_lease(self) -> None:
        digest = command_digest("disable-range-control")
        lease = self.service.issue_authorization_lease(
            operation_id="op_1",
            ability_id="ab_impair",
            technique_id="T1562.001",
            ability_digest=digest,
            target="range-host-1",
            approver="security-lead@example.test",
        )
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id="ab_impair",
            technique_id="T1562.001",
            authorization_lease=lease,
            ability_digest=digest,
            target="range-host-1",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.disposition, "allow")
        self.assertEqual(decision.reason, "authorization_lease_verified")

    def test_authorization_lease_cannot_move_to_another_target(self) -> None:
        digest = command_digest("disable-range-control")
        lease = self.service.issue_authorization_lease(
            operation_id="op_1",
            ability_id="ab_impair",
            technique_id="T1562.001",
            ability_digest=digest,
            target="range-host-1",
            approver="security-lead@example.test",
        )
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id="ab_impair",
            technique_id="T1562.001",
            authorization_lease=lease,
            ability_digest=digest,
            target="range-host-2",
        )
        self.assertEqual(decision.disposition, "simulate")
        self.assertEqual(decision.reason, "authorization_lease_target_mismatch")

    def test_authorization_lease_execution_budget_is_enforced(self) -> None:
        digest = command_digest("disable-range-control")
        lease = self.service.issue_authorization_lease(
            operation_id="op_1",
            ability_id="ab_impair",
            technique_id="T1562.001",
            ability_digest=digest,
            target="range-host-1",
            approver="security-lead@example.test",
            max_executions=1,
        )
        request = {
            "operation_id": "op_1",
            "ability_id": "ab_impair",
            "technique_id": "T1562.001",
            "authorization_lease": lease,
            "ability_digest": digest,
            "target": "range-host-1",
        }
        first = self.service.evaluate_ability(**request)
        second = self.service.evaluate_ability(**request)
        self.assertEqual(first.disposition, "allow")
        self.assertEqual(second.disposition, "simulate")
        self.assertEqual(second.reason, "authorization_lease_budget_exhausted")

    def test_kill_switch_also_blocks_cleanup_link(self) -> None:
        with patch.dict("os.environ", {"CALDERA_KILL_SWITCH": "1"}, clear=False):
            decision = self.service.evaluate_ability(
                operation_id="op_1",
                ability_id="cleanup-1",
                technique_id="T1562.001",
                cleanup=True,
            )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "kill_switch_engaged")
        self.assertTrue(decision.kill_switch)

    def test_cleanup_flag_does_not_bypass_high_blast_authorization(self) -> None:
        decision = self.service.evaluate_ability(
            operation_id="op_1",
            ability_id="cleanup-1",
            technique_id="T1562.001",
            cleanup=True,
        )
        self.assertEqual(decision.disposition, "simulate")
        self.assertEqual(decision.reason, "authorization_lease_missing")

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


class TestAuthorizationLease(unittest.TestCase):
    def setUp(self) -> None:
        self.issuer = AuthorizationLeaseIssuer("test-signing-key")
        self.claims = {
            "operation_id": "op-1",
            "ability_id": "ability-1",
            "technique_id": "T1562.001",
            "ability_digest": command_digest("range-command"),
            "target": "range-host-1",
            "approver": "security-lead@example.test",
        }

    def test_expired_lease_fails_closed(self) -> None:
        token = self.issuer.issue(**self.claims, ttl_seconds=30, now=100)
        result = self.issuer.verify(token, **{k: v for k, v in self.claims.items() if k != "approver"}, now=131)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "authorization_lease_expired")

    def test_tampered_lease_fails_closed(self) -> None:
        token = self.issuer.issue(**self.claims)
        payload, signature = token.split(".", 1)
        replacement = "A" if payload[-1] != "A" else "B"
        result = self.issuer.verify(
            f"{payload[:-1]}{replacement}.{signature}",
            **{k: v for k, v in self.claims.items() if k != "approver"},
        )
        self.assertFalse(result.valid)


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


class TestDispatchBoundary(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = GateProveService(authorization_key="dispatch-key", ledger=OperationLedger())
        self.operation = SimpleNamespace(id="governed-operation")

    @staticmethod
    def link(technique_id: str, cleanup: int = 0) -> SimpleNamespace:
        return SimpleNamespace(
            id=f"link-{technique_id}",
            ability=SimpleNamespace(
                ability_id=f"ability-{technique_id}",
                technique_id=technique_id,
                technique_name="Test technique",
            ),
            cleanup=cleanup,
            command=f"command-{technique_id}",
            paw="range-host-1",
            status=-3,
            states={"DISCARD": -2},
        )

    async def test_governor_discards_unapproved_high_blast_link(self) -> None:
        link = self.link("T1562.001")

        decision = self.service.govern_link(self.operation, link)

        self.assertEqual(link.status, link.states["DISCARD"])
        self.assertEqual(decision.disposition, "simulate")
        self.assertEqual(link.gate_prove_decision["disposition"], "simulate")

    async def test_governor_allows_standard_link(self) -> None:
        link = self.link("T1082")

        decision = self.service.govern_link(self.operation, link)

        self.assertEqual(link.status, -3)
        self.assertEqual(decision.disposition, "allow")
        self.assertEqual(link.gate_prove_decision["disposition"], "allow")

    async def test_governor_discards_cleanup_during_kill_switch(self) -> None:
        link = self.link("T1562.001", cleanup=1)
        with patch.dict("os.environ", {"CALDERA_KILL_SWITCH": "1"}, clear=False):
            decision = self.service.govern_link(self.operation, link)

        self.assertEqual(link.status, link.states["DISCARD"])
        self.assertEqual(decision.disposition, "deny")
        self.assertEqual(link.gate_prove_decision["reason"], "kill_switch_engaged")

    async def test_cleanup_flag_cannot_bypass_high_blast_policy(self) -> None:
        link = self.link("T1562.001", cleanup=1)

        decision = self.service.govern_link(self.operation, link)

        self.assertEqual(link.status, link.states["DISCARD"])
        self.assertEqual(decision.reason, "authorization_lease_missing")


if __name__ == "__main__":
    unittest.main()
