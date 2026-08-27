from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


def command_digest(command: Any) -> str:
    """Bind authorization to the exact command bytes queued for dispatch."""
    if isinstance(command, bytes):
        raw = command
    else:
        raw = str(command or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class LeaseValidation:
    valid: bool
    reason: str
    claims: dict[str, Any] = field(default_factory=dict)


class AuthorizationLeaseIssuer:
    """Issue and verify scoped, expiring HMAC authorization leases."""

    def __init__(self, secret: str, issuer: str = "gate-prove") -> None:
        self._secret = secret.encode("utf-8")
        self.issuer = issuer

    @property
    def configured(self) -> bool:
        return bool(self._secret)

    def issue(
        self,
        *,
        operation_id: str,
        ability_id: str,
        technique_id: str,
        ability_digest: str,
        target: str,
        approver: str,
        ttl_seconds: int = 300,
        max_executions: int = 1,
        now: int | None = None,
    ) -> str:
        if not self.configured:
            raise ValueError("authorization lease key is not configured")
        if ttl_seconds < 1 or max_executions < 1:
            raise ValueError("ttl_seconds and max_executions must be positive")
        issued_at = int(time.time() if now is None else now)
        claims = {
            "iss": self.issuer,
            "jti": secrets.token_urlsafe(16),
            "operation_id": operation_id,
            "ability_id": ability_id,
            "technique_id": technique_id,
            "ability_digest": ability_digest,
            "target": target,
            "approver": approver,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
            "max_executions": max_executions,
        }
        payload = self._encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = self._encode(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}"

    def verify(
        self,
        token: str,
        *,
        operation_id: str,
        ability_id: str,
        technique_id: str,
        ability_digest: str,
        target: str,
        now: int | None = None,
    ) -> LeaseValidation:
        if not self.configured:
            return LeaseValidation(False, "authorization_lease_key_unconfigured")
        if not token:
            return LeaseValidation(False, "authorization_lease_missing")
        try:
            payload, supplied_signature = token.split(".", 1)
            expected_signature = self._encode(
                hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(expected_signature, supplied_signature):
                return LeaseValidation(False, "authorization_lease_signature_invalid")
            claims = json.loads(self._decode(payload))
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
            return LeaseValidation(False, "authorization_lease_malformed")
        if not isinstance(claims, dict):
            return LeaseValidation(False, "authorization_lease_malformed")

        expected = {
            "iss": self.issuer,
            "operation_id": operation_id,
            "ability_id": ability_id,
            "technique_id": technique_id,
            "ability_digest": ability_digest,
            "target": target,
        }
        for field_name, expected_value in expected.items():
            if claims.get(field_name) != expected_value:
                return LeaseValidation(False, f"authorization_lease_{field_name}_mismatch", claims)

        current_time = int(time.time() if now is None else now)
        if not isinstance(claims.get("exp"), int) or claims["exp"] <= current_time:
            return LeaseValidation(False, "authorization_lease_expired", claims)
        if not isinstance(claims.get("max_executions"), int) or claims["max_executions"] < 1:
            return LeaseValidation(False, "authorization_lease_budget_invalid", claims)
        if not claims.get("jti") or not claims.get("approver"):
            return LeaseValidation(False, "authorization_lease_claims_incomplete", claims)
        return LeaseValidation(True, "authorization_lease_verified", claims)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> str:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding).decode("utf-8")
