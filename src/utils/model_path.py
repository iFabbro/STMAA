"""Shared resolution for PPO model artifact paths."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


DEFAULT_MODEL_PATH = Path("models/ppo_trading_model.zip")
MODEL_PATH_ENV = "STMAA_MODEL_PATH"


def repository_root() -> Path:
    """Return the checkout root without resolving symlinks in artifact paths."""
    return Path(__file__).absolute().parents[2]


def resolve_model_path(
    model_path: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve an explicit, environment, or default model path against the checkout.

    Explicit arguments take precedence over STMAA_MODEL_PATH. Relative paths are
    anchored to the repository root, never to the caller current directory.
    The returned path is normalized but symlinks are deliberately not resolved:
    PPOAgent O_NOFOLLOW checks must remain effective at artifact access time.
    """
    env = os.environ if environ is None else environ
    if model_path is not None:
        raw_path = os.fspath(model_path)
        if not raw_path.strip():
            raise ValueError("model_path must not be empty")
    elif MODEL_PATH_ENV in env:
        raw_path = env[MODEL_PATH_ENV]
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"{MODEL_PATH_ENV} must not be empty")
    else:
        raw_path = os.fspath(DEFAULT_MODEL_PATH)

    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = repository_root() / candidate
    return Path(os.path.abspath(os.fspath(candidate)))
