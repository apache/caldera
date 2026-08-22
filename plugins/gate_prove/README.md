# GateProve Plugin for MITRE Caldera

Deterministic Gate/Prove safety boundary and append-only hash-chained Action Ledger for Caldera operations.

## Overview

When running automated adversary emulation against enterprise or staging infrastructure, executing high-blast abilities (such as `T1562` Impair Defenses or `T1485` Data Destruction) without strict safety controls creates severe operational risk.

**GateProve** introduces a zero-trust safety boundary:
1. **`never_equate_intent_to_approval: true`**: High planner confidence or automated execution does not authorize destructive techniques.
2. **Simulation Fallback**: Unapproved destructive abilities automatically default to safe simulation mode without mutating underlying systems.
3. **HITL Prove Token**: Destructive execution requires an authorized cryptographic token (`CALDERA_PROVE_TOKEN`).
4. **Append-Only Action Ledger**: Every ability evaluation, receipt, and hash is recorded into an append-only JSONL ledger with SHA-256 chain verification for audit compliance (SOC 2, ISO 27001, NIST CSF).
5. **Atomic Kill-Switch**: Immediate freeze of operation ability dispatch via environment variable (`CALDERA_KILL_SWITCH=1`) or file sentinel (`artifacts/KILL`).
6. **Generated Ability Manifest**: AI-generated abilities fail closed unless they include parsers, cleanup, bounded scope, generator/model provenance, and a matching digest over execution-relevant fields.

## AI-generated ability contract

Call `evaluate_ability` with both `ability_manifest` and `provenance` before dispatching content produced by an LLM ability factory. GateProve validates that the ability is attributable, bounded, reversible, and unchanged since review. A missing manifest field or mismatched digest produces a `deny` decision and a ledger receipt containing the validation errors.

Required scope fields are `targets`, `expires_at`, and a positive `max_executions`. Required provenance fields are `generator`, `model`, `created_at`, and `content_hash`; compute the latter with `ability_content_hash` from `app/ability_manifest.py`.

## Configuration

In `conf/default.yml`:

```yaml
name: GateProve
enabled: true
prove_token: "your-hitl-secret-token"
ledger_path: "artifacts/caldera_action_ledger.jsonl"
kill_switch: false
```

At plugin enablement, `CALDERA_PROVE_TOKEN` and `CALDERA_GATE_PROVE_LEDGER` configure the registered `gate_prove_svc` service. The kill switch is evaluated for every decision, so it can freeze dispatch without restarting Caldera.

## Running Tests

```bash
python3 -m unittest plugins/gate_prove/tests/test_gate_prove.py
```

## License

Apache-2.0
