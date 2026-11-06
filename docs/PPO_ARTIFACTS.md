# STMAA PPO artifacts and manifest

This document describes the artifact format emitted by `PPOAgent.save()`. The manifest is the authoritative pointer to the active immutable model generation. The repository does not commit a trained PPO model. A checkout without a local artifact is expected to block normal operation until the documented training workflow has produced and validated one.

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
- holdout fraction and requested PPO timesteps;
- effective PPO hyperparameters: `learning_rate`, `n_steps`, `batch_size`, `n_epochs` and `gamma`;
- initial capital and random seed;
- SHA-256 fingerprint of the processed feature DataFrame passed to PPO, including its index, column names and values;
- source Git commit and whether the Git working tree is clean;
- validation metrics returned by the evaluation environment.

The dataset fingerprint is calculated from a deterministic CSV representation of the processed frame after cleaning and feature calculation. It identifies the exact in-memory training/validation input but does not archive the underlying dataset. `git_commit` and `git_worktree_clean` are `null` when Git provenance is unavailable (for example, when running from a source archive). A non-clean working tree is explicitly identified; the recorded commit alone does not capture uncommitted modifications. Lower-level `PPOAgent.save()` calls emit the hyperparameter and capital fields; dataset fingerprint and Git provenance are `null` unless supplied by the caller.

The holdout is later in time than the training interval. Its metrics are diagnostic, not an automatic profitability gate or a guarantee of future performance. Lower-level `PPOAgent.save()` calls include the standard provenance defaults and merge any caller-supplied `training` metadata.

## Runtime lifecycle and readiness

The runtime reports five separate states; none is implied by another:

1. ppo_initialized: the PPO agent object was constructed.
2. model_discovered: an artifact entry point (manifest or legacy base path) exists.
3. model_loaded: the active manifest generation passed digest verification and was deserialized.
4. model_validated: the loaded Stable-Baselines3 observation/action spaces, action bounds and training-timestep metadata match the active STMAA contract.
5. inference_available: a deterministic finite-output probe completed with the expected action shape.

Normal Paper operation requires all five states to be true. Missing, corrupt, untrained or incompatible artifacts stop at startup before the broker preflight and before the trading loop. The --preflight-only mode remains a broker/configuration diagnostic and intentionally does not assert PPO readiness.

The deterministic probe checks technical inference availability only. It does not establish strategy quality, profitability, robustness or suitability for deployment.

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
