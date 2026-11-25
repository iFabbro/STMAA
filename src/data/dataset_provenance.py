"""Immutable local snapshots and manifests for market-data lineage."""
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from src.data.market_data_contract import (
        DATA_ADJUSTMENT,
        DATA_FEATURE_VERSION,
        DATA_FEED,
        DATA_PROVIDER,
        DATA_TIMEFRAME,
        market_data_contract,
    )
except ModuleNotFoundError:
    from data.market_data_contract import (
        DATA_ADJUSTMENT,
        DATA_FEATURE_VERSION,
        DATA_FEED,
        DATA_PROVIDER,
        DATA_TIMEFRAME,
        market_data_contract,
    )


PROVENANCE_SCHEMA_VERSION = 1


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for content identifiers."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_frame_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize a complete provider/processed frame deterministically."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("Dataset snapshot requires a non-empty DataFrame")
    csv_text = frame.to_csv(
        index=True,
        date_format="%Y-%m-%dT%H:%M:%S.%f",
        float_format="%.17g",
        lineterminator="\n",
    )
    return csv_text.encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_data_request(
    tickers: list[str], start_date: str, end_date: str
) -> dict[str, Any]:
    """Capture the exact logical request and active versioned market-data contract."""
    if not tickers or len(set(tickers)) != len(tickers):
        raise ValueError("Dataset provenance requires unique, non-empty tickers")
    contract = market_data_contract()
    return {
        "provider": DATA_PROVIDER,
        "feed": DATA_FEED,
        "timeframe": DATA_TIMEFRAME,
        "adjustment": DATA_ADJUSTMENT,
        "feature_version": DATA_FEATURE_VERSION,
        "tickers": list(tickers),
        "start_date_inclusive": str(start_date),
        "end_date_exclusive": str(end_date),
        "market_data_contract": contract,
    }


def _write_immutable(path: Path, payload: bytes) -> None:
    """Create an immutable content-addressed file; never replace conflicting bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = sha256_bytes(payload)
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlink dataset artifact: {path}")
    if path.exists():
        if not path.is_file() or sha256_bytes(path.read_bytes()) != expected:
            raise RuntimeError(f"Existing content-addressed artifact is corrupt: {path}")
        return
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Conflicting dataset artifact path: {path}") from exc
        if sha256_bytes(path.read_bytes()) != expected:
            raise RuntimeError(f"Existing content-addressed artifact is corrupt: {path}") from exc


def persist_frame_snapshot(
    data_dir: Path, kind: str, request: dict[str, Any], frame: pd.DataFrame
) -> dict[str, Any]:
    """Persist raw or processed frame under request- and content-addressed identity."""
    if kind not in {"raw", "processed"}:
        raise ValueError("Dataset snapshot kind must be raw or processed")
    payload = canonical_frame_bytes(frame)
    content_sha = sha256_bytes(payload)
    request_sha = sha256_bytes(canonical_json_bytes(request))
    filename = f"{request_sha[:16]}_{content_sha}.csv"
    relative_path = Path(kind) / filename
    target = Path(data_dir) / relative_path
    _write_immutable(target, payload)
    return {
        "path": relative_path.as_posix(),
        "sha256": content_sha,
        "size_bytes": len(payload),
        "rows": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "dtypes": [str(dtype) for dtype in frame.dtypes],
        "index_dtype": str(frame.index.dtype),
        "index_name": frame.index.name,
    }


def persist_data_manifest(
    data_dir: Path,
    request: dict[str, Any],
    raw_snapshot: dict[str, Any],
    processed_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Persist a self-identifying manifest that binds request, raw and processed data."""
    body = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "request": request,
        "request_sha256": sha256_bytes(canonical_json_bytes(request)),
        "raw": raw_snapshot,
        "processed": processed_snapshot,
    }
    manifest_sha = sha256_bytes(canonical_json_bytes(body))
    manifest = {**body, "manifest_sha256": manifest_sha}
    payload = canonical_json_bytes(manifest) + b"\n"
    relative_path = Path("manifests") / f"{manifest_sha}.json"
    _write_immutable(Path(data_dir) / relative_path, payload)
    return {
        **manifest,
        "manifest_path": relative_path.as_posix(),
    }
