from pathlib import Path

from src.execution import broker_executor


def test_default_execution_log_path_is_anchored_to_repository_root():
    expected_dir = (
        Path(broker_executor.__file__).resolve().parents[2]
        / "logs"
        / "execution"
    )

    assert broker_executor.EXECUTION_LOG_DIR.is_absolute()
    assert broker_executor.EXECUTION_LOG_DIR == expected_dir
    assert broker_executor.EXECUTION_LOG_PATH == (
        expected_dir / "broker_executor.log"
    )
