"""Append-only, hash-chained JSONL ledger for PPO shadow events."""
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class PPOShadowLedger:
    """Serialize readers/writers with an advisory lock; corruption is never auto-repaired."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise ValueError("Shadow ledger and lock paths must not be symlinks")

    def _locked(self, mode):
        if self.lock_path.is_symlink():
            raise ValueError("Shadow ledger lock path must not be a symlink")
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        return os.fdopen(fd, "a+b"), mode

    def _read_unlocked(self):
        if not self.path.exists():
            return []
        if self.path.is_symlink() or not stat.S_ISREG(self.path.stat().st_mode):
            raise ValueError("Shadow ledger must be a regular non-symlink file")
        result, previous = [], "0" * 64
        with self.path.open("rb") as stream:
            for sequence, line in enumerate(stream, 1):
                if not line.endswith(b"\n"):
                    raise ValueError("Incomplete trailing ledger record; preserve original and recover manually")
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

    def read(self):
        lock, mode = self._locked(fcntl.LOCK_SH)
        with lock:
            fcntl.flock(lock.fileno(), mode)
            return self._read_unlocked()

    def append(self, payload):
        if not isinstance(payload, dict):
            raise TypeError("Ledger payload must be a dictionary")
        _canonical(payload)
        lock, mode = self._locked(fcntl.LOCK_EX)
        with lock:
            fcntl.flock(lock.fileno(), mode)
            records = self._read_unlocked()
            previous = records[-1]["hash"] if records else "0" * 64
            digest_input = {"sequence": len(records) + 1, "previous_hash": previous, "payload": payload}
            record = {**digest_input, "hash": hashlib.sha256(_canonical(digest_input)).hexdigest()}
            line = _canonical(record) + b"\\n"
            flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            ledger_fd = os.open(self.path, flags, 0o600)
            try:
                offset = 0
                while offset < len(line):
                    written = os.write(ledger_fd, line[offset:])
                    if written <= 0:
                        raise OSError("Short append to shadow ledger")
                    offset += written
                os.fsync(ledger_fd)
            finally:
                os.close(ledger_fd)
            return record
