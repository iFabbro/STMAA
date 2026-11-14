from __future__ import annotations

import os
import stat

import pytest

from first_run import create_env_file
from src.utils.env_config import load_effective_environment, secret_is_configured


def test_effective_environment_prefers_process_values_over_dotenv(tmp_path):
    (tmp_path / ".env").write_text(
        "ALPACA_API_KEY=file-key\nSTMAA_TRADING_MODE=paper\n",
        encoding="utf-8",
    )
    effective = load_effective_environment(
        tmp_path,
        environ={"ALPACA_API_KEY": "exported-key", "EXTRA": "value"},
    )
    assert effective["ALPACA_API_KEY"] == "exported-key"
    assert effective["STMAA_TRADING_MODE"] == "paper"
    assert effective["EXTRA"] == "value"


def test_effective_environment_parses_dotenv_syntax_and_quotes(tmp_path):
    (tmp_path / ".env").write_text(
        "export ALPACA_API_KEY='quoted-key'\n# comment\n",
        encoding="utf-8",
    )
    assert load_effective_environment(tmp_path, environ={})["ALPACA_API_KEY"] == "quoted-key"


def test_secret_placeholder_validation_is_shared():
    assert secret_is_configured("real-paper-key")
    for value in ("", " ", "your_api_key_here", "replace_with_secret"):
        assert not secret_is_configured(value)


def test_first_run_creates_paper_only_env_with_private_permissions(tmp_path):
    target = create_env_file(tmp_path, "paper-key-value", "paper-secret-value")
    content = target.read_text(encoding="utf-8")
    assert "ALPACA_API_KEY=paper-key-value" in content
    assert "ALPACA_API_SECRET=paper-secret-value" in content
    assert "STMAA_TRADING_MODE=paper" in content
    assert "ALPACA_BASE_URL=https://paper-api.alpaca.markets" in content
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_first_run_never_overwrites_existing_env(tmp_path):
    target = tmp_path / ".env"
    target.write_text("KEEP=this-value\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        create_env_file(tmp_path, "paper-key-value", "paper-secret-value")
    assert target.read_text(encoding="utf-8") == "KEEP=this-value\n"


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("your_api_key_here", "valid-secret"),
        ("valid-key", "your_api_secret_here"),
        ("", "valid-secret"),
        ("valid-key", "line\nbreak"),
    ],
)
def test_first_run_rejects_placeholders_and_multiline_secrets(tmp_path, key, secret):
    with pytest.raises(ValueError):
        create_env_file(tmp_path, key, secret)
    assert not (tmp_path / ".env").exists()
