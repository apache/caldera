import os

from plugins.gate_prove.app.gate_prove_svc import GateProveService


name = 'GateProve'
description = 'Deterministic Gate/Prove safety boundary and hash-chained action ledger for Caldera operations.'
address = '/plugin/gate_prove/gui'


async def enable(services):
    """Enable GateProve safety hook and ledger service in Caldera server."""
    service = GateProveService(
        prove_token=os.environ.get('CALDERA_PROVE_TOKEN', ''),
        authorization_key=os.environ.get('CALDERA_AUTHORIZATION_KEY', ''),
        attestation_key=os.environ.get('CALDERA_ATTESTATION_KEY', ''),
        ledger_path=os.environ.get('CALDERA_GATE_PROVE_LEDGER', 'artifacts/caldera_action_ledger.jsonl'),
    )
    services['gate_prove_svc'] = service
