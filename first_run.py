"""Guided, offline creation of a local Alpaca Paper .env file."""
from __future__ import annotations

import argparse
import getpass
import os
import tempfile
from pathlib import Path

from src.utils.env_config import PAPER_BASE_URL, secret_is_configured


def create_env_file(root: Path, api_key: str, api_secret: str) -> Path:
    """Create .env atomically with restrictive permissions; never overwrite an existing file."""
    if not secret_is_configured(api_key) or not secret_is_configured(api_secret):
        raise ValueError("Both Paper credentials must be non-empty and not placeholders")
    if any(char in api_key + api_secret for char in "\r\n"):
        raise ValueError("Credential values must not contain line breaks")

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / ".env"
    content = (
        "# Local credentials for Alpaca Paper Trading. Keep this file private.\n"
        f"ALPACA_API_KEY={api_key}\n"
        f"ALPACA_API_SECRET={api_secret}\n\n"
        "# This entry point intentionally supports Paper mode only.\n"
        "STMAA_TRADING_MODE=paper\n"
        f"ALPACA_BASE_URL={PAPER_BASE_URL}\n"
    )
    fd, temporary_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=root)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link is an atomic create-if-absent operation on the same filesystem.
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise FileExistsError(".env already exists; refusing to overwrite local configuration")
        if os.name == "posix":
            os.chmod(target, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a local Paper .env file; this wizard is offline and never contacts Alpaca."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    if (args.root / ".env").exists():
        parser.error(".env already exists; refusing to overwrite it")
    print("STMAA First-Run Setup — local Paper configuration only")
    print("Credentials are entered through masked prompts and are not displayed.")
    api_key = getpass.getpass("Alpaca Paper API key: ")
    api_secret = getpass.getpass("Alpaca Paper API secret: ")
    try:
        path = create_env_file(args.root, api_key, api_secret)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Setup failed: {exc}\n")
    print(f"Created {path} with restrictive file permissions.")
    print("Run 'uv run python doctor.py' to validate the local setup.")
    print("No network request, account lookup, or broker operation was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
