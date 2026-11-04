"""Pure integer-share sizing and PPO v7 deviation accounting for RW-08."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping


EXECUTION_SIZING_CONTRACT = "opg_moo_integer_floor_v1"
MODEL_CONTRACT_VERSION = 7


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite numeric value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite numeric value") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite numeric value")
    return result


def _validate_symbol_mapping(
    values: Mapping[str, object], field: str
) -> dict[str, object]:
    if not isinstance(values, Mapping) or not values:
        raise ValueError(f"{field} must be a non-empty symbol mapping")
    result = dict(values)
    if any(
        not isinstance(symbol, str) or not symbol or symbol.strip() != symbol
        for symbol in result
    ):
        raise ValueError(f"{field} symbols must be non-empty trimmed strings")
    return result


@dataclass(frozen=True)
class OpgAssetSizing:
    """Sizing and target-deviation record for one symbol."""

    symbol: str
    ppo_target_weight: float
    close_price: float
    target_notional: float
    target_quantity: int
    integer_notional: float
    integer_weight: float
    deviation_weight: float
    rounding_residual: float
    current_quantity: int
    delta_quantity: int
    side: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "ppo_target_weight": self.ppo_target_weight,
            "close_price": self.close_price,
            "target_notional": self.target_notional,
            "target_quantity": self.target_quantity,
            "integer_sized_notional": self.integer_notional,
            "integer_sized_weight": self.integer_weight,
            "weight_deviation": self.deviation_weight,
            "rounding_residual": self.rounding_residual,
            "current_quantity": self.current_quantity,
            "delta_quantity": self.delta_quantity,
            "side": self.side,
        }


@dataclass(frozen=True)
class OpgSizingPlan:
    """Immutable, serializable result of integer target sizing."""

    equity: float
    assets: dict[str, OpgAssetSizing]
    total_target_notional: float
    total_integer_notional: float
    total_rounding_residual: float
    target_gross_weight: float
    integer_gross_weight: float
    target_cash_weight: float
    execution_sizing_contract: str = EXECUTION_SIZING_CONTRACT
    model_contract_version: int = MODEL_CONTRACT_VERSION

    @property
    def contract(self) -> str:
        """Compatibility alias for the execution sizing contract."""
        return self.execution_sizing_contract

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible record; persistence is owned by RW-08.3."""
        return {
            "model_contract_version": self.model_contract_version,
            "execution_sizing_contract": self.execution_sizing_contract,
            "sizing_basis": (
                "equity_at_signal * ppo_target_weight / "
                "completed_signal_bar_close; floor to whole shares"
            ),
            "equity": self.equity,
            "assets": {
                symbol: asset.to_dict()
                for symbol, asset in self.assets.items()
            },
            "totals": {
                "target_notional": self.total_target_notional,
                "integer_sized_notional": self.total_integer_notional,
                "rounding_residual": self.total_rounding_residual,
                "ppo_target_gross_weight": self.target_gross_weight,
                "integer_sized_gross_weight": self.integer_gross_weight,
                "ppo_target_cash_weight": self.target_cash_weight,
            },
        }


def build_opg_sizing_plan(
    *,
    equity: object,
    target_weights: Mapping[str, object],
    close_prices: Mapping[str, object],
    current_positions: Mapping[str, object],
) -> OpgSizingPlan:
    """Build an offline integer-share target plan without broker interaction.

    The sizing basis is equity and completed signal-bar Close, not next-session
    Open. Existing positions must be non-negative whole-share quantities.
    """
    equity_value = _decimal(equity, "equity")
    if equity_value <= 0:
        raise ValueError("equity must be strictly positive")

    weights_input = _validate_symbol_mapping(target_weights, "target_weights")
    prices_input = _validate_symbol_mapping(close_prices, "close_prices")
    positions_input = _validate_symbol_mapping(current_positions, "current_positions")
    symbols = set(weights_input)
    if symbols != set(prices_input):
        raise ValueError("target_weights and close_prices must contain the same symbols")
    if not symbols.issubset(positions_input):
        raise ValueError("current_positions must include every target symbol")

    weights: dict[str, Decimal] = {}
    prices: dict[str, Decimal] = {}
    positions: dict[str, int] = {}
    for symbol in sorted(positions_input):
        position = _decimal(positions_input[symbol], f"position for {symbol}")
        if position < 0:
            raise ValueError(f"short position for {symbol} is not supported")
        if position != position.to_integral_value():
            raise ValueError(f"fractional position for {symbol} blocks the sizing batch")
        positions[symbol] = int(position)

    for symbol in sorted(symbols):
        weight = _decimal(weights_input[symbol], f"target weight for {symbol}")
        if weight < 0 or weight > 1:
            raise ValueError(f"target weight for {symbol} must be in [0, 1]")
        weights[symbol] = weight

        price = _decimal(prices_input[symbol], f"close price for {symbol}")
        if price <= 0:
            raise ValueError(f"close price for {symbol} must be strictly positive")
        prices[symbol] = price

    target_gross = sum(weights.values(), Decimal("0"))
    if target_gross > 1:
        raise ValueError("gross target weight must not exceed 1")

    assets: dict[str, OpgAssetSizing] = {}
    total_target = Decimal("0")
    total_integer = Decimal("0")
    total_residual = Decimal("0")

    for symbol in sorted(symbols):
        target_notional = equity_value * weights[symbol]
        target_quantity = int(target_notional // prices[symbol])
        integer_notional = Decimal(target_quantity) * prices[symbol]
        residual = target_notional - integer_notional
        integer_weight = integer_notional / equity_value
        deviation_weight = integer_weight - weights[symbol]
        delta_quantity = target_quantity - positions[symbol]
        side = "buy" if delta_quantity > 0 else "sell" if delta_quantity < 0 else None

        if residual < 0 or residual >= prices[symbol]:
            raise ArithmeticError(f"invalid floor-rounding residual for {symbol}")

        assets[symbol] = OpgAssetSizing(
            symbol=symbol,
            ppo_target_weight=float(weights[symbol]),
            close_price=float(prices[symbol]),
            target_notional=float(target_notional),
            target_quantity=target_quantity,
            integer_notional=float(integer_notional),
            integer_weight=float(integer_weight),
            deviation_weight=float(deviation_weight),
            rounding_residual=float(residual),
            current_quantity=positions[symbol],
            delta_quantity=delta_quantity,
            side=side,
        )
        total_target += target_notional
        total_integer += integer_notional
        total_residual += residual

    return OpgSizingPlan(
        equity=float(equity_value),
        assets=assets,
        total_target_notional=float(total_target),
        total_integer_notional=float(total_integer),
        total_rounding_residual=float(total_residual),
        target_gross_weight=float(target_gross),
        integer_gross_weight=float(total_integer / equity_value),
        target_cash_weight=float(Decimal("1") - target_gross),
    )
