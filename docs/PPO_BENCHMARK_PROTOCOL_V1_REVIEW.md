# PPO Benchmark Protocol v1 — review notes

**Protocol:** `ppo-benchmark`  
**Version:** `1.0.0`  
**Status:** `draft` — not released and not eligible for an ACCEPTED governance decision.

The machine-readable draft is `research/protocols/ppo-benchmark/1.0.0.json`. It uses the Research Protocol schema introduced by PR #373.

## Evidence reviewed

Repository sources inspected on `main` at `9a2c604d81ea6c4fc0a61d261b038329968e9d79`:

- `docs/PPO_WALK_FORWARD.md`
- `docs/PPO_BENCHMARK.md`
- `docs/PPO_BENCHMARK_METHODOLOGY_AUDIT.md`
- `walk_forward.py`
- `benchmark_walk_forward.py`
- `src/backtest/walk_forward.py`
- `src/backtest/benchmark_methodology.py`
- `src/agents/ppo_agent.py`
- `schemas/research-protocol.schema.json`

The `assistant-exchange` manifest published at `2026-11-17T00:20:00.495358Z` declares these two inputs:

| Evidence | Bytes | Manifest-declared SHA-256 |
|---|---:|---|
| `ppo_benchmark_multiseed_20261110_42_7_21.json` | 19,042,863 | `1de9cf019d13f4572c63b64c48876c6a3a46e05b3ad808aa4bf92aab487833ce` |
| `ppo_walk_forward_multiseed_20261110_42_7_21.json` | 6,009,264 | `ab5de9bb36c53f9c27f1c720864bbd5de30bfdce5d44527c8099515b6881f9e7` |

The manifest identifies source commit `a885c425e8442f539408dfb41bf2cd24f2e60e07`. The manifest itself was retrieved; the large report payloads were not returned by the GitHub file-content interface in this review. Therefore their declared hashes are recorded, but **the report bytes have not been independently re-hashed here**. This is explicitly reflected in the protocol status fields.

## Parameters represented in the draft

The draft records the existing research summary: seeds 42, 7 and 21; 11 expanding-window folds; seven symbols (AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META); reported coverage 2018-01-30 through 2026-11-06 (2,206 observations); initial capital USD 100,000; transaction cost 0.001; slippage 0.0005; maximum position weight 0.20; maximum gross exposure 0.50; 50,000 requested timesteps per fold (reported runtime 51,200); learning rate 0.0003; n_steps 2048; batch size 64; n_epochs 10; gamma 0.99.

These historical report values are marked `REPORTED_NOT_BYTE_REVERIFIED`, not as independently verified run facts. CLI and implementation defaults are separately marked `CODE_DEFAULT_ONLY` where appropriate.

## Methodology fixed by inspected code/documentation

- Fresh PPO policy per expanding-window fold; chronological, non-overlapping test windows.
- Independent all-cash start per fold; no fold-to-fold portfolio carry.
- Completed bar observation followed by execution at the next bar Open.
- Long-only target weights; maximum per-name weight and gross exposure; unused allocation remains cash.
- Equal-weight buy-and-hold benchmark uses the PPO universe and caps, buys once, and does not rebalance.
- SPY 50% and 100% fixed-exposure buy-and-hold references; SPY remains outside the PPO training universe.
- Exposure-matched equal-weight comparator uses the same seed's PPO gross-exposure trace and daily rebalancing. It must never borrow another seed's exposure path.
- Execution-data identity uses a SHA-256 fingerprint over ordered timestamps and Open/Close values.
- Fold metrics remain independent descriptive observations; no synthetic compounded OOS curve.

## Release blockers / explicit decisions

1. Independently read and re-hash both report files; verify every seed, fold, date, ticker, parameter and matched-exposure result against raw JSON.
2. Attach a verifiable immutable market-data snapshot and dataset fingerprint; document feed, adjustment policy, exchange calendar, timezone/session labeling and missing-data treatment.
3. Document the ex-ante universe-selection rule and point-in-time membership limitations. The current seven-symbol universe does not establish protection from survivorship or selection bias.
4. Capture the complete PPO training/policy configuration, exact model artifact and manifest hashes, Python/runtime and dependency-lock fingerprint.
5. Decide whether v1 is descriptive only or includes formal statistical inference. No PSR/DSR, bootstrap confidence interval, multiple-testing correction or numeric acceptance threshold is inferred.
6. Explicitly approve seed handling and per-seed exposure matching.
7. Resolve each `UNKNOWN` or explicitly accept it as a documented limitation before any transition to `released`.

The protocol remains `draft` until these items have been reviewed and the user explicitly approves release. A schema-valid document and passing CI do not constitute scientific acceptance, evidence verification, profitability, or permission for broker activity.

## Scope of this change

This PR adds the draft protocol and review notes only. It does not retrain PPO, regenerate benchmarks, modify historical reports, access Alpaca/broker APIs, or change trading configuration.
