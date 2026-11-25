import signal
import threading

import pytest

from src.execution.broker_executor import BrokerExecutor
from src.execution.paper_smoke_control import install_stop_signal_handlers, submit_paper_smoke_order


def test_stop_event_blocks_mutation_interlock_before_broker_validation():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.stop_event = threading.Event()
    executor.stop_event.set()

    with pytest.raises(RuntimeError, match="shutdown stop signal is active"):
        executor._assert_paper_mutation_allowed()



def test_day_pre_submit_guard_rejection_clears_only_the_unsubmitted_intent():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.stop_event = threading.Event()
    executor.stop_event.set()
    executor.state = {
        "order_submission_unresolved": {
            "AAPL": {"client_order_id": "blocked-aapl"},
            "MSFT": {"client_order_id": "other-intent"},
        }
    }
    executor._save_state = lambda: None

    with pytest.raises(RuntimeError, match="shutdown stop signal is active"):
        executor._assert_day_submission_allowed("AAPL", "blocked-aapl")

    assert "AAPL" not in executor.state["order_submission_unresolved"]
    assert executor.state["order_submission_unresolved"] == {
        "MSFT": {"client_order_id": "other-intent"}
    }


def test_stop_event_unset_does_not_trigger_shutdown_block():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.stop_event = threading.Event()

    # With no stop request, the interlock continues to the normal Paper/client
    # validation path rather than rejecting solely due to the stop latch.
    with pytest.raises(RuntimeError, match="executor is not Paper-only"):
        executor._assert_paper_mutation_allowed()



def test_install_stop_signal_handlers_latches_sigint_and_sigterm(monkeypatch):
    registered = {}
    monkeypatch.setattr(signal, "signal", lambda signum, handler: registered.__setitem__(signum, handler))
    stop_event = threading.Event()

    install_stop_signal_handlers(stop_event)

    assert set(registered) == {signal.SIGINT, signal.SIGTERM}
    registered[signal.SIGINT](signal.SIGINT, None)
    assert stop_event.is_set()


def test_paper_smoke_submission_checks_interlock_before_sdk_call():
    broker = type("Broker", (), {"submit_order": lambda self, **kwargs: "submitted"})()
    calls = []
    executor = type(
        "Executor",
        (),
        {
            "trading_client": broker,
            "_assert_paper_mutation_allowed": lambda self: calls.append("guard"),
        },
    )()

    result = submit_paper_smoke_order(executor, object())

    assert result == "submitted"
    assert calls == ["guard"]


def test_paper_smoke_submission_does_not_call_sdk_when_stop_is_latched():
    broker_calls = []
    broker = type(
        "Broker",
        (),
        {"submit_order": lambda self, **kwargs: broker_calls.append(kwargs)},
    )()
    executor = type(
        "Executor",
        (),
        {
            "trading_client": broker,
            "_assert_paper_mutation_allowed": lambda self: (_ for _ in ()).throw(
                RuntimeError("Broker mutation blocked: shutdown stop signal is active")
            ),
        },
    )()

    with pytest.raises(RuntimeError, match="shutdown stop signal is active"):
        submit_paper_smoke_order(executor, object())

    assert broker_calls == []
