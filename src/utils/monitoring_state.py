"""Atomic persistence for the read-only trading dashboard snapshot."""

import json
import os
import tempfile
from pathlib import Path


HUB_STATE_PATH = Path("logs/hub_state.json")


def write_hub_state(state, path=None):
    """Atomically replace the dashboard snapshot with a complete JSON object."""
    if not isinstance(state, dict):
        raise TypeError("Monitoring state must be a dictionary")

    destination = Path(path) if path is not None else HUB_STATE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(state, temporary_file, indent=2, default=str)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, destination)
        temporary_path = None

        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is not None:
            directory_fd = os.open(destination.parent, os.O_RDONLY | directory_flag)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
