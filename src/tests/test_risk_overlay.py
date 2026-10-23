import numpy as np
import pytest
from src.strategy.risk_overlay import RiskOverlayModule


def test_risk_overlay_creation():
    overlay = RiskOverlayModule(max_position_size=0.2, vix_threshold_high=30,
                                vix_threshold_low=15)
    assert overlay.max_position_size == 0.2
    assert overlay.max_gross_exposure == 1.0


def test_vix_scaling():
    overlay = RiskOverlayModule()
    assert overlay._calculate_vix_scaling(12) == 1.0
    assert overlay._calculate_vix_scaling(35) == 0.5
    assert 0.5 < overlay._calculate_vix_scaling(22) < 1.0


def test_position_cap_survives_vix_scaling_without_renormalization():
    overlay = RiskOverlayModule(max_position_size=0.2)
    result = overlay.apply(None, np.array([0.8, 0.8, -0.7]), ["A", "B", "C"], vix=35)
    np.testing.assert_allclose(result, np.array([0.1, 0.1, -0.1]))
    assert np.max(np.abs(result)) <= 0.2
    assert np.abs(result).sum() == pytest.approx(0.3)


def test_gross_exposure_is_capped_after_individual_position_limits():
    overlay = RiskOverlayModule(max_position_size=0.2, max_gross_exposure=0.5)
    result = overlay.apply(None, np.array([0.2, 0.2, -0.2, 0.2]), ["A", "B", "C", "D"])
    np.testing.assert_allclose(result, np.array([0.125, 0.125, -0.125, 0.125]))
    assert np.abs(result).sum() == pytest.approx(0.5)
    assert np.max(np.abs(result)) <= 0.2


def test_low_vix_keeps_caps_and_drawdown_circuit_breaker():
    overlay = RiskOverlayModule(max_position_size=0.2, max_drawdown_threshold=0.15)
    result = overlay.apply(None, np.array([0.8, 0.1]), ["A", "B"], vix=12)
    np.testing.assert_allclose(result, np.array([0.2, 0.1]))
    stopped = overlay.apply(None, np.array([0.1, 0.1]), ["A", "B"], vix=12,
                            portfolio_value=80000, peak_portfolio_value=100000)
    np.testing.assert_array_equal(stopped, np.zeros(2))


def test_invalid_weights_and_vix_fail_explicitly():
    overlay = RiskOverlayModule(max_position_size=0.2)
    with pytest.raises(ValueError, match="finite"):
        overlay.apply(None, np.array([np.nan]), ["A"])
    with pytest.raises(ValueError, match="vix"):
        overlay.apply(None, np.array([0.1]), ["A"], vix=float("nan"))
    with pytest.raises(ValueError, match="Expected 1 weights"):
        overlay.apply(None, np.array([0.1, 0.2]), ["A"])


def test_adaptive_sizing_uses_normalized_atr_and_preserves_signs():
    overlay = RiskOverlayModule(max_position_size=0.2)
    result = overlay.adaptive_position_sizing(np.array([0.1, 0.1, -0.1]),
                                              np.array([0.01, 0.02, 0.04]))
    np.testing.assert_allclose(result, np.array([0.2, 0.1, -0.05]))
    assert np.max(np.abs(result)) <= overlay.max_position_size
    assert result[0] > 0 and result[1] > 0 and result[2] < 0


def test_adaptive_sizing_respects_cap_and_keeps_zero_weights():
    overlay = RiskOverlayModule(max_position_size=0.15)
    result = overlay.adaptive_position_sizing(np.array([0.1, 0.0, -0.1]),
                                              np.array([0.01, 0.02, 0.04]))
    assert np.max(np.abs(result)) <= 0.15
    assert result[1] == 0.0
    assert result[0] > 0 and result[2] < 0


@pytest.mark.parametrize(("weights", "atr_values", "message"), [
    ([0.1, 0.2], [0.01], "equal-length"),
    ([0.1], [0.0], "strictly positive"),
    ([0.1], [np.nan], "finite"),
    ([np.inf], [0.01], "finite"),
])
def test_adaptive_sizing_rejects_invalid_inputs(weights, atr_values, message):
    overlay = RiskOverlayModule()
    with pytest.raises(ValueError, match=message):
        overlay.adaptive_position_sizing(np.asarray(weights), np.asarray(atr_values))


def test_adaptive_sizing_accepts_empty_vectors():
    overlay = RiskOverlayModule()
    result = overlay.adaptive_position_sizing(np.array([]), np.array([]))
    assert result.shape == (0,)


def test_invalid_gross_exposure_limit_is_rejected():
    with pytest.raises(ValueError, match="max_gross_exposure"):
        RiskOverlayModule(max_gross_exposure=0)



def test_first_verified_account_value_establishes_drawdown_baseline():
    overlay = RiskOverlayModule(initial_capital=500000.0, max_drawdown_threshold=0.15)
    result = overlay.apply(
        None, np.array([0.2, 0.1]), ["A", "B"], portfolio_value=100000.0
    )
    np.testing.assert_allclose(result, np.array([0.2, 0.1]))
    assert overlay.peak_portfolio_value == pytest.approx(100000.0)


def test_persisted_drawdown_peak_trips_circuit_breaker_despite_capital_mismatch():
    overlay = RiskOverlayModule(initial_capital=500000.0, max_drawdown_threshold=0.15)
    result = overlay.apply(
        None, np.array([0.2, 0.1]), ["A", "B"],
        portfolio_value=80000.0, peak_portfolio_value=100000.0,
    )
    np.testing.assert_array_equal(result, np.zeros(2))
    assert overlay.peak_portfolio_value == pytest.approx(100000.0)


def test_drawdown_peak_advances_only_when_account_makes_new_high():
    overlay = RiskOverlayModule(initial_capital=500000.0, max_drawdown_threshold=0.15)
    overlay.apply(None, np.array([0.1]), ["A"], portfolio_value=100000.0)
    overlay.apply(None, np.array([0.1]), ["A"], portfolio_value=110000.0)
    assert overlay.peak_portfolio_value == pytest.approx(110000.0)
    overlay.apply(None, np.array([0.1]), ["A"], portfolio_value=105000.0)
    assert overlay.peak_portfolio_value == pytest.approx(110000.0)


def test_stale_external_peak_cannot_lower_internal_high_water_mark():
    overlay = RiskOverlayModule(max_drawdown_threshold=0.15)
    overlay.apply(None, np.array([0.1]), ["A"], portfolio_value=120000.0)
    result = overlay.apply(
        None, np.array([0.1]), ["A"],
        portfolio_value=100000.0, peak_portfolio_value=105000.0,
    )
    np.testing.assert_array_equal(result, np.zeros(1))
    assert overlay.peak_portfolio_value == pytest.approx(120000.0)


def test_external_peak_higher_than_internal_peak_is_preserved():
    overlay = RiskOverlayModule(max_drawdown_threshold=0.15)
    overlay.apply(None, np.array([0.1]), ["A"], portfolio_value=100000.0)
    result = overlay.apply(
        None, np.array([0.1]), ["A"],
        portfolio_value=110000.0, peak_portfolio_value=120000.0,
    )
    np.testing.assert_array_equal(result, np.zeros(1))
    assert overlay.peak_portfolio_value == pytest.approx(120000.0)


def test_invalid_internal_high_water_mark_fails_closed():
    overlay = RiskOverlayModule()
    overlay.peak_portfolio_value = float("nan")
    with pytest.raises(ValueError, match="Stored peak_portfolio_value"):
        overlay.apply(None, np.array([0.1]), ["A"], portfolio_value=100000.0)
