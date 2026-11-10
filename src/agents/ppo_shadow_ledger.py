"""Append-only, hash-chained JSONL ledger for PPO shadow events."""
import fcntl
import hashlib
import json
import os
from pathlib import Path


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class PPOShadowLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def read(self):
        if not self.path.exists():
            return []
        result, previous = [], "0" * 64
        with self.path.open("rb") as stream:
            for sequence, line in enumerate(stream, 1):
                if not line.endswith(b"\n"):
                    raise ValueError("Incomplete trailing ledger record")
                try:
                    record = json.loads(line)
                    payload = record["payload"]
                    digest_input = {"sequence": sequence, "previous_hash": previous, "payload": payload}
                    digest = hashlib.sha256(_canonical(digest_input)).hexdigest()
                    if record["sequence"] != sequence or record["previous_hash"] != previous:
                        raise ValueError("Ledger sequence or chain mismatch")
                    if record["hash"] != digest:
                        raise ValueError("Ledger integrity check failed")
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("Malformed shadow ledger record") from exc
                result.append(record)
                previous = digest
        return result

    def append(self, payload):
        if not isinstance(payload, dict):
            raise TypeError("Ledger payload must be a dictionary")
        _canonical(payload)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            records = self.read()
            previous = records[-1]["hash"] if records else "0" * 64
            digest_input = {"sequence": len(records) + 1, "previous_hash": previous, "payload": payload}
            record = {**digest_input, "hash": hashlib.sha256(_canonical(digest_input)).hexdigest()}
            line = _canonical(record) + b"\n"
            ledger_fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            try:
                if os.write(ledger_fd, line) != len(line):
                    raise OSError("Short append to shadow ledger")
                os.fsync(ledger_fd)
            finally:
                os.close(ledger_fd)
            return record
