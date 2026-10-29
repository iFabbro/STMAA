from decimal import Decimal
from types import SimpleNamespace

from scripts.alpaca_paper_btc_readonly_reconcile import (
    infer_unlinked_commission_from_position,
)


def make_order(order_id="order-1"):
    return SimpleNamespace(
        id=order_id,
        symbol="BTC/USD",
        status="filled",
        filled_qty="0.000121728",
        filled_avg_price="80587.953",
    )


def make_position():
    return SimpleNamespace(
        symbol="BTCUSD",
        qty="0.000121423",
        cost_basis="9.785231",
    )


def make_fee():
    return {
        "activity_type": "CFEE",
        "symbol": "BTCUSD",
        "qty": "-0.000000305",
        "price": "80587.95",
        "order_id": None,
    }


def test_infers_fee_only_when_unique_order_position_and_accounting_deltas_match():
    order = make_order()
    fee_usd, detail = infer_unlinked_commission_from_position(
        order=order,
        fees=[make_fee()],
        btc_positions=[make_position()],
        all_orders=[order],
    )

    assert fee_usd == Decimal("0.02457932475")
    assert "RECONCILED_BY_UNIQUE_ACCOUNTING_MATCH" in detail


def test_rejects_unlinked_fee_when_multiple_filled_btc_orders_exist():
    order = make_order()
    other_order = make_order("order-2")
    fee_usd, detail = infer_unlinked_commission_from_position(
        order=order,
        fees=[make_fee()],
        btc_positions=[make_position()],
        all_orders=[order, other_order],
    )

    assert fee_usd is None
    assert "exactly one matching filled BTC order" in detail


def test_rejects_unlinked_fee_when_quantity_delta_does_not_match():
    order = make_order()
    position = SimpleNamespace(
        symbol="BTCUSD",
        qty="0.000120000",
        cost_basis="9.785231",
    )
    fee_usd, detail = infer_unlinked_commission_from_position(
        order=order,
        fees=[make_fee()],
        btc_positions=[position],
        all_orders=[order],
    )

    assert fee_usd is None
    assert "quantity does not explain" in detail


def test_rejects_unlinked_fee_when_multiple_fee_candidates_exist():
    order = make_order()
    fee_usd, detail = infer_unlinked_commission_from_position(
        order=order,
        fees=[make_fee(), make_fee()],
        btc_positions=[make_position()],
        all_orders=[order],
    )

    assert fee_usd is None
    assert "exactly one candidate activity" in detail
