"""Shared .env/process-environment resolution for local setup diagnostics."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
_PLACEHOLDERS = {
    "", "your_api_key_here", "your_api_secret_here", "changeme",
    "change_me", "placeholder", "replace_me",
}


def load_effective_environment(
    root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve .env first and process environment second, matching load_dotenv(override=False)."""
    file_values = dotenv_values(Path(root) / ".env")
    merged = {key: value for key, value in file_values.items() if value is not None}
    process_values = os.environ if environ is None else environ
    merged.update({key: str(value) for key, value in process_values.items() if value is not None})
    return merged


def secret_is_configured(value: str | None) -> bool:
    """Return whether a credential is non-empty and not a known template placeholder."""
    cleaned = (value or "").strip()
    lowered = cleaned.lower()
    if lowered in _PLACEHOLDERS:
        return False
    return not any(marker in lowered for marker in (
        "your_api_key", "your_api_secret", "replace_with",
    ))
