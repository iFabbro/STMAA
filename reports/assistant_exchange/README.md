# STMAA Assistant Exchange

Dedicated diagnostic-output channel for ChatGPT-assisted STMAA work.

## Isolation guarantees

- This directory is maintained on the `assistant-exchange` branch only.
- Do not merge this branch into `main`: it is an operational exchange channel, not a product/code branch.
- Use a separate Git worktree (recommended path: `~/Projects/STMAA-assistant-exchange`) so the normal STMAA checkout can remain on its current branch.
- The publisher refuses to run unless its worktree is on `assistant-exchange`.
- The publisher accepts only explicitly named `.txt`, `.json`, `.md`, and `.csv` files, applies size limits and secret-pattern checks, and does not inspect or upload the repository wholesale.
- Each publication updates `latest/` and preserves a timestamped copy under `history/`.

## Initial setup on macOS

From the existing STMAA checkout:

```bash
cd ~/Projects/STMAA
git fetch origin
git worktree add ~/Projects/STMAA-assistant-exchange assistant-exchange
```

The publisher is available in that worktree at:

```
tools/assistant_exchange/publish.py
```

## Publish selected diagnostic files

Example:

```bash
cd ~/Projects/STMAA-assistant-exchange
.venv/bin/python tools/assistant_exchange/publish.py \
  --repo-path ~/Projects/STMAA \
  --label "benchmark report schema inspection" \
  ~/Desktop/STMAA_REPORT_SCHEMA.txt
```

The source files can be generated in the main checkout or on the Desktop. Only the paths explicitly supplied are considered.

The script stages only `reports/assistant_exchange/`, commits on `assistant-exchange`, and pushes that branch to `origin`. It never checks out, commits to, or pushes `main`.

## Reading published outputs

- Latest files: `reports/assistant_exchange/latest/`
- Immutable publication snapshots: `reports/assistant_exchange/history/<UTC timestamp>/`
- Publication metadata and SHA-256 digests: `manifest.json` in each snapshot

ChatGPT can retrieve files from this branch using the GitHub connection by specifying the branch ref.

## Security and operating rules

1. Publish diagnostic extracts, not raw environment files, credentials, account exports, broker payloads, or unrestricted application logs.
2. Never pass `.env`, key files, private keys, database files, model artifacts, or archives.
3. Review the publisher's rejection output. A clean scan is a guardrail, not proof that a file contains no sensitive information.
4. Keep output files concise. Split oversized reports into focused extracts rather than raising the size limit.
5. Do not put secrets in the `--label` value.
6. Treat this branch as private operational data even if repository visibility changes.

The publisher intentionally does not delete old history. Periodic retention cleanup can be added separately if needed.
