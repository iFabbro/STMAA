# STMAA PPO artifacts and manifest

This document describes the artifact format emitted by `PPOAgent.save()`. The manifest is the authoritative pointer to the active immutable model generation. The repository does not commit a trained PPO model.

## Files and publication model

For a logical base path such as `models/ppo_trading_model.zip`, STMAA writes:

- `models/ppo_trading_model.zip.metadata.json`: active manifest.
- `models/ppo_trading_model.<sha256>.zip`: immutable model generation whose filename contains the SHA-256 of its bytes.
- Temporary staging files in the same directory during publication.
- A host-local artifact lock file to serialize save, load and pruning operations.

The model generation is written and flushed first. Its digest is calculated from the exact bytes. The generation is published under its content-addressed filename, then the manifest is written to a temporary file, flushed and atomically replaced into the active manifest path. A crash before manifest replacement leaves the previous manifest pointing to the previous intact generation. An unreferenced generation may remain and is protected by a recovery grace period during pruning.

The manifest path is therefore not the model ZIP itself. Do not rename, edit, copy independently, or delete either file while STMAA may be loading or saving the model.

## Manifest fields

| Field | Meaning |
|---|---|
| `contract_version` | Model behavior contract. Current value: `7`. |
| `tickers` | Ordered ticker list used to build the observation and action spaces. |
| `state_dim` | Observation length: `1 + 6 × number_of_tickers` (cash, five normalized features and one current-weight value per ticker). |
| `action_contract` | Long-only target-weight action semantics, zero lower bound and capped allocation. |
| `execution_contract` | Signal on completed close (t), target fill at next bar open (t+1). |
| `observation_contract` | Current normalized feature semantics: `dimensionless_ohlc_features_v1`. |
| `transaction_cost`, `slippage` | Training/runtime cost assumptions; load rejects mismatches. |
| `max_position_size`, `max_gross_exposure` | Per-position and portfolio allocation caps; load rejects mismatches. |
| `market_data_contract` | Complete versioned provider, feed, timeframe, adjustment, features, formula parameters, execution fields and data semantics. |
| `model_sha256` | SHA-256 of the exact ZIP bytes; checked before deserialization. |
| `artifact_file` | Content-addressed ZIP filename; must match the digest and remain within the model directory. |
| `training` | Provenance and evaluation metadata supplied by the training entry point. |

The machine-readable schema is [`schemas/ppo-artifact-manifest.schema.json`](schemas/ppo-artifact-manifest.schema.json). The schema documents the current emitted structure; runtime loading additionally validates the active ticker list, state dimension, model/data contracts, runtime parameters, artifact filename and artifact digest.

## Training provenance

The standard `train.py` entry point records:

- data source and the full market-data contract;
- overall historical interval;
- chronological training and holdout-validation intervals;
- holdout fraction;
- requested PPO timesteps and random seed;
- validation metrics returned by the evaluation environment.

The holdout is later in time than the training interval. Its metrics are diagnostic, not an automatic profitability gate or a guarantee of future performance. A manifest written through the lower-level `PPOAgent.save()` API may contain a smaller caller-supplied `training` object.

## Compatibility

Current contracts:

- model contract: `7`;
- observation contract: `dimensionless_ohlc_features_v1`;
- market-data contract: `4`;
- provider/feed/timeframe/adjustment: Alpaca SIP, daily, `adjustment=all`;
- execution price: next-bar `Open`.

Models trained with older observation or execution semantics are not compatible merely because their tensor dimensions match. Load rejects incompatible contracts and runtime assumptions. Retrain and independently evaluate before using a newly generated artifact.

## Maintenance

`PPOAgent.prune_artifacts()` is explicit and defaults to `dry_run=True`. It refuses cleanup when the active manifest is unreadable, incompatible, or points to a missing/corrupt generation. Use the dry-run result for inspection before any deletion. The artifact lock coordinates cooperating processes on one host; it is not a distributed lock.
