# PPO Benchmark Protocol v1 — review notes

**Protocol:** `ppo-benchmark`  
**Version:** `1.0.0`  
**Status:** `draft` — not released and not eligible for an ACCEPTED governance decision.

The machine-readable draft is `research/protocols/ppo-benchmark/1.0.0.json`. This update records independent verification of the two original multi-seed report payloads. It does not release the protocol or convert unresolved methodology into accepted facts.

## Source report verification

The source manifest is `reports/assistant_exchange/latest/manifest.json` on branch `assistant-exchange`, published at `2026-11-17T00:20:00.495358Z`. Both raw report blobs were retrieved by immutable Git blob SHA, parsed as JSON, and independently SHA-256 hashed over their exact UTF-8 bytes.

| Report | Bytes | Git blob SHA | Independently computed SHA-256 | Manifest match |
|---|---:|---|---|---|
| `ppo_benchmark_multiseed_20261110_42_7_21.json` | 19,042,863 | `7ee96cea2b9892c74ff8b7f320a335561914328a` | `1de9cf019d13f4572c63b64c48876c6a3a46e05b3ad808aa4bf92aab487833ce` | PASS |
| `ppo_walk_forward_multiseed_20261110_42_7_21.json` | 6,009,264 | `fb7236f6b71542b16ed87d152fc9bdb2dca36944` | `ab5de9bb36c53f9c27f1c720864bbd5de30bfdce5d44527c8099515b6881f9e7` | PASS |

Both computed hashes and byte lengths match the manifest declarations exactly. The manifest declares source commit `a885c425e8442f539408dfb41bf2cd24f2e60e07`.

## Report facts now confirmed

- Seeds 42, 7 and 21.
- Expanding-window design: 11 folds per seed, 33 seed/fold evaluations.
- Universe: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA and META; SPY is the benchmark ticker, not part of the PPO universe.
- Data coverage: `2018-01-30 05:00:00` through `2026-11-09 05:00:00`, 2,207 observations.
- Fold layout: minimum training window 756 observations, test window 126, step 126.
- Requested timesteps: 50,000 per fold per seed.
- Training parameters: learning rate 0.0003, n_steps 2048, batch size 64, n_epochs 10, gamma 0.99.
- Portfolio/cost parameters: USD 100,000 initial capital, 0.20 maximum position weight, 0.50 maximum gross exposure, transaction cost 0.001 and slippage 0.0005.

The earlier draft summary of 2,206 observations ending 2026-11-06 was incorrect for these report payloads and is replaced with the raw report values. The previously reported runtime-aligned timestep count of 51,200 is not present in the raw report; it remains `UNKNOWN`, while the requested 50,000 is confirmed.

## Independent cross-report consistency checks

The reports were compared by seed and fold:

- 33/33 seed/fold pairs found in both reports.
- Zero differences across total return, final value, drawdown, Sharpe, Sortino, transaction costs, turnover, observations and steps.
- Zero seed-comparison mirror mismatches.
- Zero fold-date or observation-count mismatches.
- All 33 walk-forward execution-data fingerprints match the corresponding benchmark fold fingerprint.
- All 11 benchmark fold execution-identity statuses are `PASS`.

These checks establish report integrity and internal/cross-report consistency. They do not prove the underlying market-data snapshot's provenance or correctness, and they do not replace execution of the repository's required auditor scripts.

## Material unresolved result: exposure-matched equal-weight

The benchmark report marks `ppo_exposure_matched_equal_weight_status` as `INCONCLUSIVE` in all 11 folds. The corresponding comparator result payload is absent in those folds, even though the walk-forward report contains PPO daily traces.

Therefore:
- Do not describe the historical exposure-matched equal-weight comparison as completed or validated.
- Do not infer that the comparator passed.
- Investigate why the benchmark producer emitted `INCONCLUSIVE` and whether the required seed-specific daily exposure trace was rejected or not consumed.
- Do not regenerate the benchmark until explicitly authorized; this review only inspects existing bytes.

Other benchmark summaries remain descriptive. They are not evidence of statistical significance, generalization, profitability, or future performance.

## Remaining blockers and UNKNOWN fields

1. Establish immutable market-data snapshot identity and provider/feed revision lineage.
2. Specify corporate-action adjustment, timezone, exchange calendar/session labels and missing-data treatment.
3. Document ex-ante universe selection and point-in-time membership limitations; survivorship/selection bias remain unresolved.
4. Capture exact PPO artifact and manifest identities, complete policy/optimizer configuration, and environment/dependency-lock fingerprints.
5. Establish whether 51,200 runtime timesteps were used; the original multi-seed report only confirms 50,000 requested timesteps.
6. Resolve the 11/11 INCONCLUSIVE exposure-matched benchmark outputs.
7. Decide whether v1 is descriptive only or define approved statistical inference, multiple-testing treatment and acceptance criteria.
8. Explicitly approve seed handling and per-seed exposure matching.

No field is promoted solely because it is plausible or matches a code default. Unverified fields remain `UNKNOWN`, `CODE_DEFAULT_ONLY`, `REPORTED_NOT_BYTE_REVERIFIED`, or `APPROVAL_REQUIRED` as appropriate.

## Scope and safety

This update changes protocol evidence and review notes only. It does not retrain PPO, regenerate benchmarks, mutate historical report files, access Alpaca/broker APIs, change trading configuration, or authorize trading. The protocol remains `draft`; schema validity and CI success do not constitute scientific acceptance or release.
