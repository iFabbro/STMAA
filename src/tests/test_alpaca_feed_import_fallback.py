"""Regression coverage for AlpacaFeed's top-level import fallback."""

import os
import subprocess
import sys
from pathlib import Path


def test_alpaca_feed_fallback_import_exposes_market_data_fingerprint():
    source_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import data.alpaca_feed as module; "
                "assert callable(module.market_data_fingerprint)"
            ),
        ],
        cwd=source_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
