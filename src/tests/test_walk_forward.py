"""Regression tests for temporal integrity and PPO walk-forward folds."""
import numpy as np
import pandas as pd
import pytest

from src.backtest.walk_forward import (
    expanding_window_folds,
    run_walk_forward_validation,
)
from src.data.feature_engineering import calculate_features


def _ohlcv(rows=100):
    index = pd.date_range("2020-01-01", periods=rows, freq="B")
    close = np.linspace(100.0, 150.0, rows)
    return pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(rows, 1000.0),
        },
        index=index,
    )


def test_features_before_cutoff_are_invariant_to_future_market_data_changes():
    original = _ohlcv()
    cutoff = 70
    mutated = original.copy()
    mutated.iloc[cutoff:, mutated.columns.get_loc("Close")] *= 1.75
    mutated.iloc[cutoff:, mutated.columns.get_loc("High")] *= 1.75
    mutated.iloc[cutoff:, mutated.columns.get_loc("Low")] *= 1.75

    expected = calculate_features(original)
    actual = calculate_features(mutated)

    pd.testing.assert_frame_equal(
        expected.iloc[:cutoff],
        actual.iloc[:cutoff],
        check_exact=True,
    )


def test_expanding_folds_are_chronological_disjoint_and_non_overlapping():
    data = _ohlcv(100)
    folds = expanding_window_folds(
        data, min_train_size=40, test_size=20
    )

    assert len(folds) == 3
    for fold in folds:
        training, testing = fold.split(data)
        assert len(training) >= 40
        assert len(testing) == 20
        assert training.index.max() < testing.index.min()
    assert folds[0].train_end == 40
    assert folds[1].train_end == 60
    assert folds[2].train_end == 80
    assert folds[0].test_end == folds[1].test_start


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_train_size": 21, "test_size": 10}, ">= 22"),
        ({"min_train_size": 30, "test_size": 1}, ">= 2"),
        ({"min_train_size": 30, "test_size": 10, "step_size": 9}, ">= test_size"),
    ],
)
def test_expanding_folds_reject_invalid_window_sizes(kwargs, message):
    with pytest.raises(ValueError, match=message):
        expanding_window_folds(_ohlcv(100), **kwargs)


def test_expanding_folds_reject_unsorted_or_duplicate_timestamps():
    data = _ohlcv(80)
    unsorted = data.iloc[::-1]
    with pytest.raises(ValueError, match="chronological"):
        expanding_window_folds(unsorted, 40, 20)

    duplicate = data.copy()
    duplicate.index = list(duplicate.index[:-1]) + [duplicate.index[-2]]
    with pytest.raises(ValueError, match="unique"):
        expanding_window_folds(duplicate, 40, 20)


def test_walk_forward_runner_trains_only_on_past_and_passes_boundary_close():
    data = _ohlcv(70)
    data["RSI_A"] = 50.0
    data["MACD_A"] = 0.0
    data["BB_middle_A"] = data["Close"]
    data["ATR_A"] = 1.0
    data["Close_A"] = data["Close"]

    calls = []

    class FakeAgent:
        def __init__(self, fold_number):
            self.fold_number = fold_number
            self.training = None

        def train(self, training, total_timesteps):
            self.training = training.copy()
            calls.append(
                {
                    "fold": self.fold_number,
                    "train_end": training.index.max(),
                    "timesteps": total_timesteps,
                }
            )

        def evaluate(self, testing, previous_close):
            calls[-1]["test_start"] = testing.index.min()
            calls[-1]["previous_close"] = previous_close.copy()
            return {"total_return": 0.01}

    def factory(fold_number, _model_path):
        return FakeAgent(fold_number)

    result = run_walk_forward_validation(
        data,
        ["A"],
        min_train_size=40,
        test_size=10,
        total_timesteps=8,
        agent_factory=factory,
    )

    assert result["fold_count"] == 3
    assert len(calls) == 3
    for call in calls:
        assert call["train_end"] < call["test_start"]
        expected = data.loc[call["train_end"], "Close_A"]
        assert call["previous_close"][0] == expected
        assert call["timesteps"] == 8
    assert result["aggregation"]["status"] == "not_computed"


def test_expanding_folds_require_enough_rows_for_one_complete_fold():
    with pytest.raises(ValueError, match="Insufficient observations"):
        expanding_window_folds(_ohlcv(50), min_train_size=40, test_size=20)



def test_multi_seed_walk_forward_reuses_identical_folds_and_reports_dispersion():
    from src.backtest.walk_forward import run_multi_seed_walk_forward_validation

    data = _ohlcv(70)
    data["RSI_A"] = 50.0
    data["MACD_A"] = 0.0
    data["BB_middle_A"] = data["Close"]
    data["ATR_A"] = 1.0
    data["Close_A"] = data["Close"]

    calls = []

    class FakeAgent:
        def __init__(self, fold_number, seed):
            self.fold_number = fold_number
            self.seed = seed

        def train(self, training, total_timesteps):
            calls.append(
                {
                    "fold": self.fold_number,
                    "seed": self.seed,
                    "train_start": training.index.min(),
                    "train_end": training.index.max(),
                    "timesteps": total_timesteps,
                }
            )

        def evaluate(self, testing, previous_close):
            calls[-1]["test_start"] = testing.index.min()
            return {
                "total_return": self.seed / 100.0 + self.fold_number / 1000.0,
                "sharpe_ratio": None if self.seed == 11 else float(self.seed),
                "undefined_ratio": None,
            }

    def factory(fold_number, _model_path, seed):
        return FakeAgent(fold_number, seed)

    result = run_multi_seed_walk_forward_validation(
        data,
        ["A"],
        seeds=[11, 22],
        min_train_size=40,
        test_size=10,
        total_timesteps=8,
        agent_factory=factory,
    )

    assert result["seed_count"] == 2
    assert result["fold_count"] == 3
    assert result["run_count"] == 6
    assert [run["seed"] for run in result["runs"]] == [11, 22]
    assert len(calls) == 6
    for fold_number in (1, 2, 3):
        fold_calls = [call for call in calls if call["fold"] == fold_number]
        assert len(fold_calls) == 2
        assert fold_calls[0]["train_start"] == fold_calls[1]["train_start"]
        assert fold_calls[0]["train_end"] == fold_calls[1]["train_end"]
        assert fold_calls[0]["test_start"] == fold_calls[1]["test_start"]
        assert {call["timesteps"] for call in fold_calls} == {8}

    aggregate = result["aggregation"]["all_seed_fold_metrics"]
    assert aggregate["total_return"]["n"] == 6
    assert aggregate["total_return"]["min"] == pytest.approx(0.111)
    assert aggregate["total_return"]["max"] == pytest.approx(0.223)
    assert aggregate["sharpe_ratio"]["n"] == 3
    assert "undefined_ratio" not in aggregate
    assert "not concatenated" in result["aggregation"]["scope"]


@pytest.mark.parametrize(
    "seeds",
    [[], [1, 1], [1, True], ["1", 2]],
)
def test_multi_seed_walk_forward_rejects_invalid_seed_lists(seeds):
    from src.backtest.walk_forward import run_multi_seed_walk_forward_validation

    with pytest.raises(ValueError, match="seeds"):
        run_multi_seed_walk_forward_validation(
            _ohlcv(70),
            ["A"],
            seeds=seeds,
            min_train_size=40,
            test_size=10,
            agent_factory=lambda *_args: None,
        )
