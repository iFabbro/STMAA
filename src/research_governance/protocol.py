"""Deterministic JSON canonicalization and SHA-256 protocol fingerprints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _validate_json_value(value: Any, path: str = "$",) -> None:
    """Reject values that JSON cannot represent consistently."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object key at {path} must be a string")
            _validate_json_value(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_json_value(child, f"{path}[{index}]")
    elif value is None or isinstance(value, (str, bool, int, float)):
        return
    else:
        raise TypeError(f"Unsupported JSON value at {path}: {type(value).__name__}")


def canonicalize_protocol(protocol: Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 JSON bytes for a protocol mapping.

    Canonical form v1 uses lexicographically sorted object keys, UTF-8 text
    (without ASCII escaping), compact separators, and finite JSON numbers only.
    Array order is preserved. This function does not validate a protocol against
    a JSON Schema and does not imply that a protocol is approved or released.
    """
    if not isinstance(protocol, Mapping):
        raise TypeError("protocol must be a mapping")
    _validate_json_value(protocol)
    serialized = json.dumps(
        protocol,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return serialized.encode("utf-8", errors="strict")


def protocol_fingerprint(protocol: Mapping[str, Any]) -> str:
    """Return the lowercase SHA-256 hex digest of canonical protocol bytes."""
    return hashlib.sha256(canonicalize_protocol(protocol)).hexdigest()
