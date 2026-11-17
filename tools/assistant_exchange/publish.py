#!/usr/bin/env python3
"""Publish explicitly selected, screened diagnostic files to assistant-exchange."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EXCHANGE_BRANCH = "assistant-exchange"
EXCHANGE_DIR = Path("reports/assistant_exchange")
ALLOWED_SUFFIXES = {".txt", ".json", ".md", ".csv"}
MAX_FILE_BYTES = 30 * 1024 * 1024

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|password)\s*[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{12,}"),
    re.compile(r"\bPK[A-Z0-9]{18}\b"),
]


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False, capture_output=True, text=True,
    )
    if proc.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def inspect_source(path: Path) -> tuple[bytes, str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Not a regular file: {path}")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"Rejected extension {path.suffix!r}: allowed {sorted(ALLOWED_SUFFIXES)}"
        )
    size = path.stat().st_size
    if size <= 0 or size > MAX_FILE_BYTES:
        raise ValueError(f"Rejected size for {path.name}: {size} bytes")
    data = path.read_bytes()
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not UTF-8 text: {path}") from exc

    hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(content)]
    if hits:
        raise ValueError(
            f"Potential secret detected in {path.name}; publication blocked. "
            "Review and create a sanitized diagnostic extract."
        )
    return data, hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Screen and publish selected STMAA diagnostic files."
    )
    parser.add_argument("--repo-path", required=True, type=Path,
                        help="Path to the normal STMAA checkout, used for source metadata.")
    parser.add_argument("--label", required=True,
                        help="Short non-sensitive description of this diagnostic run.")
    parser.add_argument("files", nargs="+", type=Path,
                        help="Explicit files to publish (.txt/.json/.md/.csv only).")
    args = parser.parse_args()
    if not args.label.strip() or len(args.label) > 160:
        raise ValueError("Label must contain 1-160 non-sensitive characters.")
    if any(pattern.search(args.label) for pattern in SECRET_PATTERNS):
        raise ValueError("Potential secret detected in --label; publication blocked.")

    exchange_repo = Path(__file__).resolve().parents[2]
    source_repo = args.repo_path.expanduser().resolve()
    if not (exchange_repo / ".git").exists() and not (exchange_repo / ".git").is_file():
        raise RuntimeError(f"Not a Git worktree: {exchange_repo}")

    branch = git(exchange_repo, "branch", "--show-current")
    if branch != EXCHANGE_BRANCH:
        raise RuntimeError(
            f"Refusing to publish from branch {branch!r}; expected {EXCHANGE_BRANCH!r}."
        )
    if git(exchange_repo, "remote", "get-url", "origin") == "":
        raise RuntimeError("Remote origin is not configured.")
    if git(exchange_repo, "status", "--porcelain"):
        raise RuntimeError(
            "Exchange worktree is not clean. Commit or discard unrelated changes first."
        )

    source_commit = git(source_repo, "rev-parse", "HEAD")
    source_branch = git(source_repo, "branch", "--show-current") or "detached"
    sources = [p.expanduser().absolute() for p in args.files]
    for source in sources:
        if source.is_symlink():
            raise ValueError(f"Symlink source rejected: {source}")
    sources = [p.resolve(strict=True) for p in sources]
    names = [p.name for p in sources]
    if len(names) != len(set(names)):
        raise ValueError("Input files must have unique basenames.")

    screened = [(p, *inspect_source(p)) for p in sources]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = exchange_repo / EXCHANGE_DIR
    latest = base / "latest"
    history = base / "history" / timestamp
    latest.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=False)
    for old_file in latest.iterdir():
        if old_file.is_file() and not old_file.is_symlink():
            old_file.unlink()
        else:
            raise RuntimeError(f"Unexpected directory or symlink in latest/: {old_file.name}")

    manifest_files = []
    for source, data, digest in screened:
        (latest / source.name).write_bytes(data)
        (history / source.name).write_bytes(data)
        manifest_files.append({
            "filename": source.name,
            "bytes": len(data),
            "sha256": digest,
        })

    manifest = {
        "schema_version": 1,
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "source_repository": "iFabbro/STMAA",
        "source_branch": source_branch,
        "source_commit": source_commit,
        "exchange_branch": EXCHANGE_BRANCH,
        "files": manifest_files,
        "security_note": "Explicit files only; pattern screening is not a guarantee.",
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (history / "manifest.json").write_text(manifest_text, encoding="utf-8")
    (latest / "manifest.json").write_text(manifest_text, encoding="utf-8")

    # reports/ is ignored by the main repository policy; force-add only this
    # dedicated exchange subtree. The staged-path boundary check below remains mandatory.
    git(exchange_repo, "add", "-f", str(EXCHANGE_DIR))
    staged = git(exchange_repo, "diff", "--cached", "--name-only").splitlines()
    if not staged or any(
        not (name == str(EXCHANGE_DIR) or name.startswith(str(EXCHANGE_DIR) + "/"))
        for name in staged
    ):
        raise RuntimeError("Staging boundary check failed; refusing to commit.")

    git(exchange_repo, "commit", "-m", f"chore: publish diagnostics {timestamp}")
    git(exchange_repo, "push", "origin", EXCHANGE_BRANCH)
    print(f"Published {len(screened)} file(s) to origin/{EXCHANGE_BRANCH}.")
    print(f"Latest: {EXCHANGE_DIR}/latest/")
    print(f"Snapshot: {EXCHANGE_DIR}/history/{timestamp}/")
    print(f"Commit: {git(exchange_repo, 'rev-parse', 'HEAD')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
