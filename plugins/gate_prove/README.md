# GateProve Plugin for MITRE Caldera

Deterministic Gate/Prove safety boundary and append-only hash-chained Action Ledger for Caldera operations.

## Overview

When running automated adversary emulation against enterprise or staging infrastructure, executing high-blast abilities (such as `T1562` Impair Defenses or `T1485` Data Destruction) without strict safety controls creates severe operational risk.

**GateProve** introduces a zero-trust safety boundary:
1. **`never_equate_intent_to_approval: true`**: High planner confidence or automated execution does not authorize destructive techniques.
2. **Simulation Fallback**: Unapproved destructive abilities automatically default to safe simulation mode without mutating underlying systems.
3. **Scoped Authorization Lease**: High-blast execution requires an integrity-protected, expiring lease bound to the operation, ability, exact command digest, target, approver, and execution budget.
4. **Append-Only Action Ledger**: Every ability evaluation, receipt, and hash is recorded into an append-only JSONL ledger with SHA-256 chain verification for audit compliance (SOC 2, ISO 27001, NIST CSF).
5. **Atomic Kill-Switch**: Immediate freeze of operation ability dispatch via environment variable (`CALDERA_KILL_SWITCH=1`) or file sentinel (`artifacts/KILL`).
6. **Generated Ability Manifest**: AI-generated abilities fail closed unless they include parsers, cleanup, bounded scope, generator/model provenance, and a matching digest over execution-relevant fields.
7. **Central Dispatch Enforcement**: `Operation.apply()` evaluates every planner, REST, scheduled, and direct link through GateProve. Denied or simulation-only links are retained as discarded audit records but never become executable agent instructions.
8. **Operation Attestation**: Completed operations can emit an integrity-protected evidence bundle containing input provenance, target and command digests, gate decisions, cleanup state, detection outcomes, and the ledger root without disclosing command contents.
9. **Operation Trust Contract**: A versioned, digest-protected operation intent binds an autonomous planner's inputs and objective to the exact ability manifests, range targets, privilege ceiling, and expiration accepted for dispatch.

## AI-generated ability contract

Call `evaluate_ability` with both `ability_manifest` and `provenance` before dispatching content produced by an LLM ability factory. GateProve validates that the ability is attributable, bounded, reversible, and unchanged since review. A missing manifest field or mismatched digest produces a `deny` decision and a ledger receipt containing the validation errors.

Required scope fields are `targets`, `expires_at`, and a positive `max_executions`. Required provenance fields are `generator`, `model`, `created_at`, and `content_hash`; compute the latter with `ability_content_hash` from `app/ability_manifest.py`.

Machine-readable contracts are published in `schemas/ability-manifest.schema.json` and `schemas/operation-intent.schema.json`.

## MITRE MCP trust contract

An autonomous planner must submit an `OperationIntent` through `register_operation_intent()` before dispatch. GateProve validates and records the intent, then revalidates it at every link dispatch. Each link must target an asset named by the intent and carry an `AbilityManifest` whose deterministic digest exactly matches the intent's ability reference. Expired, modified, unlisted, or out-of-range work fails closed and receives a hash-chained denial receipt.

The boundary is intentionally transport-neutral: an MCP server, REST client, or CALDERA plugin can build the same version `1.0.0` contract with `OperationIntentValidator.build()`. This keeps model planning outside the trusted computing base; only deterministic schema, digest, scope, and policy checks authorize execution. After CALDERA finishes, `attest_operation()` provides the evidence-bearing terminal result that an MCP workflow can return to its caller.

## Configuration

In `conf/default.yml`:

```yaml
name: GateProve
enabled: true
prove_token: "your-hitl-secret-token"
authorization_key: "use-CALDERA_AUTHORIZATION_KEY-in-production"
ledger_path: "artifacts/caldera_action_ledger.jsonl"
kill_switch: false
```

At plugin enablement, `CALDERA_PROVE_TOKEN` and `CALDERA_GATE_PROVE_LEDGER` configure the registered `gate_prove_svc` service. The kill switch is evaluated for every decision, so it can freeze dispatch without restarting Caldera.

Set `CALDERA_AUTHORIZATION_KEY` to a high-entropy server-side key used to issue and verify scoped authorization leases. Lease IDs and consumption counts are stored in the action ledger, so a one-execution approval cannot be replayed after a service restart. `CALDERA_PROVE_TOKEN` remains accepted as configuration for migration but no longer authorizes high-blast execution.

Set a separate `CALDERA_ATTESTATION_KEY` to protect portable operation evidence. `attest_operation()` classifies results as `completed_verified`, `completed_with_detection_gaps`, `cleanup_incomplete`, `evidence_incomplete`, `evidence_invalid`, or `in_progress`. Detection evidence distinguishes execution failure, missing telemetry, missing detection, and failed SOC correlation.

Cleanup is context, not authority: setting CALDERA's cleanup flag never bypasses manifest, technique, target, lease, budget, or kill-switch enforcement. High-blast cleanup requires its own scoped authorization lease.

## Running Tests

```bash
python3 -m unittest plugins/gate_prove/tests/test_gate_prove.py
```

## License

Apache-2.0
