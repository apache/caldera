from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple


class OperationLedger:
    """Append-only, SHA-256 hash-chained ledger for Caldera operation ability executions."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = []
        self._last_hash = "0" * 64
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        self.entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    self.entries.append(data)
                    self._last_hash = data.get("receipt_hash", self._last_hash)

    def record(
        self,
        operation_id: str,
        ability_id: str,
        technique_id: str,
        disposition: str,
        allowed: bool,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        ledger_id = str(uuid.uuid4())
        ts = time.time()
        payload = {
            "ledger_id": ledger_id,
            "timestamp": ts,
            "operation_id": operation_id,
            "ability_id": ability_id,
            "technique_id": technique_id,
            "disposition": disposition,
            "allowed": allowed,
            "reason": reason,
            "prev_hash": self._last_hash,
            "metadata": metadata or {},
        }
        raw = json.dumps(payload, sort_keys=True)
        receipt_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        payload["receipt_hash"] = receipt_hash
        self._last_hash = receipt_hash
        self.entries.append(payload)

        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, sort_keys=True) + "\n")

        return ledger_id, receipt_hash

    def verify_chain(self) -> bool:
        prev = "0" * 64
        for entry in self.entries:
            if entry.get("prev_hash") != prev:
                return False
            payload = {k: v for k, v in entry.items() if k != "receipt_hash"}
            raw = json.dumps(payload, sort_keys=True)
            calc_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if calc_hash != entry.get("receipt_hash"):
                return False
            prev = entry["receipt_hash"]
        return True
