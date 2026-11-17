"""Tests for deterministic research protocol serialization and identity."""

import hashlib
import math

import pytest

from research_governance.protocol import canonicalize_protocol, protocol_fingerprint


def test_canonicalization_sorts_keys_and_uses_compact_utf8_json():
    left = {"z": 1, "nested": {"b": "è", "a": True}}
    right = {"nested": {"a": True, "b": "è"}, "z": 1}

    expected = '{"nested":{"a":true,"b":"è"},"z":1}'.encode("utf-8")
    assert canonicalize_protocol(left) == expected
    assert canonicalize_protocol(right) == expected
    assert protocol_fingerprint(left) == protocol_fingerprint(right)


def test_fingerprint_is_sha256_of_exact_canonical_bytes():
    protocol = {"protocol_id": "example", "protocol_version": "0.1.0"}
    canonical = canonicalize_protocol(protocol)
    assert protocol_fingerprint(protocol) == hashlib.sha256(canonical).hexdigest()


def test_array_order_is_preserved():
    assert canonicalize_protocol({"seeds": [7, 21]}) != canonicalize_protocol(
        {"seeds": [21, 7]}
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_rejected(value):
    with pytest.raises(ValueError):
        canonicalize_protocol({"threshold": value})


def test_non_string_object_keys_are_rejected_recursively():
    with pytest.raises(TypeError, match="key"):
        canonicalize_protocol({"nested": {1: "not-json-object-key"}})


def test_unsupported_values_are_rejected():
    with pytest.raises(TypeError, match="Unsupported JSON value"):
        canonicalize_protocol({"value": object()})


def test_non_mapping_protocol_is_rejected():
    with pytest.raises(TypeError, match="mapping"):
        canonicalize_protocol(["not", "a", "mapping"])


def test_fingerprint_changes_when_protocol_content_changes():
    original = {"protocol_id": "example", "protocol_version": "1.0.0"}
    changed = {"protocol_id": "example", "protocol_version": "1.0.0", "fee": 0.001}
    assert protocol_fingerprint(original) != protocol_fingerprint(changed)
