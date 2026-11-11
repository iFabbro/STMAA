"""Immutable content-addressed market-data snapshots for shadow inference evidence."""
import hashlib
import json
import os
import stat
from pathlib import Path

import pandas as pd


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class PPOShadowSnapshotStore:
    """Persist exact DataFrame inputs as immutable SHA-256-addressed JSON artifacts."""

    def __init__(self, ledger_path):
        ledger = Path(ledger_path)
        self.root = ledger.with_name(ledger.name + ".snapshots")
        if self.root.is_symlink():
            raise ValueError("Snapshot directory must not be a symlink")
        self.root.mkdir(mode=0o700, parents=False, exist_ok=True)
        if not stat.S_ISDIR(self.root.stat().st_mode):
            raise ValueError("Snapshot path must be a directory")

    @staticmethod
    def _payload(frame, role):
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError("Only non-empty DataFrames can be snapshotted")
        contract = frame.attrs.get("market_data_contract")
        fingerprint = frame.attrs.get("market_data_fingerprint")
        if not isinstance(contract, dict) or not isinstance(fingerprint, str):
            raise ValueError("Snapshot requires market-data contract and fingerprint")
        table = frame.to_json(orient="table", date_format="iso", date_unit="ns",
                              double_precision=15)
        return {
            "schema": "stmaa_ppo_shadow_input_v1",
            "role": role,
            "market_data_contract": contract,
            "market_data_fingerprint": fingerprint,
            "dataframe_orient": "table",
            "dataframe_json": table,
        }

    def put(self, frame, role):
        if role not in {"model_inference", "benchmark"}:
            raise ValueError("Unsupported market-data snapshot role")
        payload = self._payload(frame, role)
        raw = _canonical(payload)
        digest = hashlib.sha256(raw).hexdigest()
        filename = f"{digest}.json"
        path = self.root / filename
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o400)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Existing snapshot artifact is unsafe")
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest or existing != raw:
                raise ValueError("Content-addressed snapshot collision or tampering")
        else:
            try:
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        raise OSError("Short write to immutable snapshot")
                    offset += written
                os.fsync(fd)
            finally:
                os.close(fd)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return {
            "schema": "stmaa_ppo_shadow_snapshot_ref_v1",
            "role": role,
            "sha256": digest,
            "path": f"{self.root.name}/{filename}",
            "size_bytes": len(raw),
        }

    def verify(self, reference):
        if not isinstance(reference, dict) or reference.get("schema") != "stmaa_ppo_shadow_snapshot_ref_v1":
            raise ValueError("Missing or malformed immutable input snapshot reference")
        if reference.get("role") not in {"model_inference", "benchmark"}:
            raise ValueError("Invalid input snapshot role")
        digest = reference.get("sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ValueError("Invalid input snapshot SHA-256")
        expected = f"{self.root.name}/{digest}.json"
        if reference.get("path") != expected:
            raise ValueError("Snapshot reference path is not canonical")
        path = self.root / f"{digest}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("Referenced immutable input snapshot is missing or unsafe")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest or len(raw) != reference.get("size_bytes"):
            raise ValueError("Referenced immutable input snapshot failed integrity verification")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Input snapshot is not valid JSON") from exc
        if (payload.get("schema") != "stmaa_ppo_shadow_input_v1"
                or payload.get("role") != reference["role"]
                or payload.get("dataframe_orient") != "table"
                or not isinstance(payload.get("dataframe_json"), str)):
            raise ValueError("Input snapshot payload contract is invalid")
        return payload
