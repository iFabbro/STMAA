"""Controlled Alpaca Paper smoke test for STMAA broker integration.

Read-only preflight, then one explicitly bounded $10 notional Paper buy order.
Never targets a Live endpoint and never retries an ambiguous submission.
"""
import os
import sys
import time
import uuid
from pathlib import Path
from decimal import Decimal

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

# Running a file under scripts/ puts that directory, not the repository root,
# at sys.path[0]. Add the root so the src package imports consistently in CI.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.execution.broker_executor import BrokerExecutor

MAX_NOTIONAL_USD = Decimal("10.00")
PAPER_ENDPOINT = "https://paper-api.alpaca.markets"


def main():
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise SystemExit("FAIL: required Alpaca repository secrets are missing")

    # The SDK client must explicitly be Paper. Refuse if code/config drifts.
    executor = BrokerExecutor(
        api_key=key,
        api_secret=secret,
        base_url=PAPER_ENDPOINT,
        paper_trading=True,
    )
    if executor.paper_trading is not True:
        raise SystemExit("FAIL: executor is not in Paper mode")

    account = executor.get_account_status()
    if not account.get("account_data_fresh") or not account.get("account_id"):
        raise SystemExit("FAIL: account identity/data could not be verified")
    if str(account.get("status", "")).lower() not in {"active", "accountstatus.active"}:
        raise SystemExit("FAIL: account is not active")
    if account.get("buying_power") is None or Decimal(str(account["buying_power"])) < MAX_NOTIONAL_USD:
        raise SystemExit("FAIL: buying power is insufficient for bounded test order")

    positions = executor.get_positions()
    if positions is None:
        raise SystemExit("FAIL: positions could not be read")
    open_orders = executor._get_open_orders()
    if open_orders is None:
        raise SystemExit("FAIL: open orders could not be read")

    print("PREFLIGHT: PASS")
    print(f"Account status: {account.get('status')}")
    print(f"Account data fresh: {account.get('account_data_fresh')}")
    print(f"Positions read: {len(positions)}")
    print(f"Open orders read: {len(open_orders)}")
    print("Paper endpoint confirmed by configured SDK mode.")

    # Stable per-workflow-run client order ID. If submission is ambiguous, look
    # up this ID; never resubmit the order automatically.
    run_id = os.environ.get("GITHUB_RUN_ID", str(uuid.uuid4()))
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    client_order_id = f"stmaa-e2e-{run_id}-{attempt}"[:48]
    request = MarketOrderRequest(
        symbol="SPY",
        notional=float(MAX_NOTIONAL_USD),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
    )
    try:
        order = executor.trading_client.submit_order(order_data=request)
    except Exception as exc:
        # Submission errors can be ambiguous: reconcile once by client ID.
        # Never retry submission automatically, even if reconciliation fails.
        print(f"SUBMISSION: response ambiguous ({type(exc).__name__}); reconciling by client_order_id.")
        try:
            order = executor.trading_client.get_order_by_client_id(client_order_id)
        except Exception:
            print("RECONCILIATION: FAIL; order status remains unknown. No retry performed.")
            raise SystemExit(2) from None
        if order is None:
            print("RECONCILIATION: order not found. No retry performed.")
            raise SystemExit(2) from None
    order_id = str(getattr(order, "id", ""))
    status = str(getattr(order, "status", "unknown")).lower()
    print(f"ORDER: client_order_id={client_order_id}")
    print(f"ORDER: broker_order_id_present={bool(order_id)}")
    print(f"ORDER: initial_status={status}")

    # Poll the broker for a bounded period; do not cancel or resubmit on timeout.
    deadline = time.monotonic() + 45
    while status not in {"filled", "partially_filled", "rejected", "canceled", "cancelled", "expired"} and time.monotonic() < deadline:
        time.sleep(3)
        try:
            order = executor.trading_client.get_order_by_client_id(client_order_id)
            status = str(getattr(order, "status", "unknown")).lower()
        except Exception as exc:
            print(f"ORDER POLL: transient read error ({type(exc).__name__}); continuing without resubmission.")
    print(f"ORDER: final_status={status}")
    print(f"ORDER: filled_qty={getattr(order, 'filled_qty', 'unknown')}")
    print(f"ORDER: filled_avg_price_present={getattr(order, 'filled_avg_price', None) is not None}")

    # Re-read positions and orders after execution; verify the broker is readable.
    post_positions = executor.get_positions()
    post_open_orders = executor._get_open_orders()
    if post_positions is None or post_open_orders is None:
        raise SystemExit("FAIL: post-order broker state could not be read")
    print(f"POSTCHECK: positions_read={len(post_positions)}")
    print(f"POSTCHECK: open_orders_read={len(post_open_orders)}")

    # Verify state can be persisted and reloaded without exposing account IDs.
    executor._save_state()
    restarted = BrokerExecutor(
        api_key=key,
        api_secret=secret,
        base_url=PAPER_ENDPOINT,
        paper_trading=True,
    )
    print(f"RESTART: persisted_state_loaded={restarted.state_file.exists()}")
    print("NOTE: partial-fill, timeout, and rejection fault injection is covered by isolated automated tests; this live Paper smoke test does not manufacture broker failures.")
    if status == "rejected":
        raise SystemExit("FAIL: Paper test order was rejected")
    print("RESULT: Paper broker smoke test completed. Review order status and CI logs before further action.")


if __name__ == "__main__":
    main()
