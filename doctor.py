"""Local, network-free installation and Paper-configuration diagnostics.

This command never contacts Alpaca, reads broker state, or submits broker actions.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import shutil
import sys
from pathlib import Path
from typing import Mapping

PAPER_API_BASE_URL = "https://paper-api.alpaca.markets"
REQUIRED_DISTRIBUTIONS = (
    "alpaca-py",
    "gymnasium",
    "numpy",
    "pandas",
    "pandas-market-calendars",
    "python-dotenv",
    "stable-baselines3",
    "textual",
    "torch",
)


def _read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE entries without printing or exporting secrets."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def collect_checks(
    root: Path | None = None,
    *,
    python_version: tuple[int, int] | None = None,
    package_versions: Mapping[str, str | None] | None = None,
    env: Mapping[str, str] | None = None,
    uv_available: bool | None = None,
) -> list[dict[str, str]]:
    """Return deterministic local checks; injectable inputs keep tests offline."""
    root = Path(root or Path(__file__).resolve().parent)
    version = python_version or sys.version_info[:2]
    if package_versions is None:
        package_versions = {}
        for distribution in REQUIRED_DISTRIBUTIONS:
            try:
                package_versions[distribution] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                package_versions[distribution] = None
    if uv_available is None:
        uv_available = shutil.which("uv") is not None

    checks: list[dict[str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append({"status": status, "name": name, "detail": detail})

    add(
        "PASS" if version == (3, 12) else "FAIL",
        "Python",
        f"Python {version[0]}.{version[1]} detected; STMAA requires Python 3.12.",
    )
    for filename in ("pyproject.toml", "uv.lock"):
        exists = (root / filename).is_file()
        add("PASS" if exists else "FAIL", filename, "present" if exists else "missing")
    add(
        "PASS" if uv_available else "FAIL",
        "uv",
        "available on PATH" if uv_available else "not found on PATH; install uv before setup",
    )

    missing = [name for name in REQUIRED_DISTRIBUTIONS if not package_versions.get(name)]
    if missing:
        add("FAIL", "Runtime dependencies", "missing: " + ", ".join(missing))
    else:
        add("PASS", "Runtime dependencies", f"{len(REQUIRED_DISTRIBUTIONS)} required distributions installed")

    env_path = root / ".env"
    if env is None:
        if not env_path.is_file():
            add("WARN", ".env", "not found; copy .env.example to .env before Paper workflows")
            return checks
        env = _read_env_file(env_path)
    else:
        env = dict(env)
        add("PASS", ".env", "configuration supplied for validation")

    mode = env.get("STMAA_TRADING_MODE")
    endpoint = env.get("ALPACA_BASE_URL")
    add(
        "PASS" if mode == "paper" else "FAIL",
        "Trading mode",
        "Paper mode explicitly configured" if mode == "paper" else "STMAA_TRADING_MODE must be exactly 'paper'",
    )
    add(
        "PASS" if endpoint == PAPER_API_BASE_URL else "FAIL",
        "Broker endpoint",
        "exact Alpaca Paper endpoint configured"
        if endpoint == PAPER_API_BASE_URL
        else f"ALPACA_BASE_URL must be exactly {PAPER_API_BASE_URL}",
    )

    placeholders = {"", "your_api_key_here", "your_api_secret_here"}
    credentials_ready = (
        env.get("ALPACA_API_KEY", "").strip() not in placeholders
        and env.get("ALPACA_API_SECRET", "").strip() not in placeholders
    )
    add(
        "PASS" if credentials_ready else "WARN",
        "Paper credentials",
        "both credential fields are populated"
        if credentials_ready
        else "credentials are missing or still placeholders; required only for Alpaca-connected workflows",
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local STMAA setup diagnostics (no network or broker calls).")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent,
                        help="repository root (defaults to the directory containing doctor.py)")
    args = parser.parse_args(argv)

    checks = collect_checks(args.root)
    for check in checks:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
    failures = sum(check["status"] == "FAIL" for check in checks)
    warnings = sum(check["status"] == "WARN" for check in checks)
    print(f"\nDoctor summary: {len(checks) - failures - warnings} passed, {warnings} warnings, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
