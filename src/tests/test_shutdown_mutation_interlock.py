import threading

import pytest

from src.execution.broker_executor import BrokerExecutor


def test_stop_event_blocks_mutation_interlock_before_broker_validation():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.stop_event = threading.Event()
    executor.stop_event.set()

    with pytest.raises(RuntimeError, match="shutdown stop signal is active"):
        executor._assert_paper_mutation_allowed()


def test_stop_event_unset_does_not_trigger_shutdown_block():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.stop_event = threading.Event()

    # With no stop request, the interlock continues to the normal Paper/client
    # validation path rather than rejecting solely due to the stop latch.
    with pytest.raises(RuntimeError, match="executor is not Paper-only"):
        executor._assert_paper_mutation_allowed()
