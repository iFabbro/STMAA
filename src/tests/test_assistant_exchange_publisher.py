from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISHER_PATH = REPO_ROOT / "tools" / "assistant_exchange" / "publish.py"
SPEC = importlib.util.spec_from_file_location("assistant_exchange_publish", PUBLISHER_PATH)
assert SPEC is not None
assert SPEC.loader is not None
PUBLISHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISHER)


def test_publisher_file_limit_is_15_mib() -> None:
    assert PUBLISHER.MAX_FILE_BYTES == 15 * 1024 * 1024


def test_publisher_accepts_file_at_15_mib(tmp_path: Path) -> None:
    source = tmp_path / "at-limit.json"
    source.write_bytes(b"x" * (15 * 1024 * 1024))

    data, digest = PUBLISHER.inspect_source(source)

    assert len(data) == 15 * 1024 * 1024
    assert len(digest) == 64


def test_publisher_rejects_file_above_15_mib(tmp_path: Path) -> None:
    source = tmp_path / "over-limit.json"
    source.write_bytes(b"x" * ((15 * 1024 * 1024) + 1))

    with pytest.raises(ValueError, match="Rejected size"):
        PUBLISHER.inspect_source(source)


def test_publisher_keeps_secret_screening_for_large_files(tmp_path: Path) -> None:
    source = tmp_path / "secret.json"
    source.write_text('{"api_key": "abcdefghijklmnop"}', encoding="utf-8")

    with pytest.raises(ValueError, match="Potential secret detected"):
        PUBLISHER.inspect_source(source)
