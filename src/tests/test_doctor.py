from __future__ import annotations

from doctor import REQUIRED_DISTRIBUTIONS, collect_checks


def _root(tmp_path, *, env_text=None, lock=True):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    if lock:
        (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    return tmp_path


def _versions():
    return {name: "1.0.0" for name in REQUIRED_DISTRIBUTIONS}


def _statuses(checks):
    return {check["name"]: check["status"] for check in checks}


def test_doctor_accepts_locked_python312_paper_setup(tmp_path):
    root = _root(
        tmp_path,
        env_text=(
            "STMAA_TRADING_MODE=paper\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "ALPACA_API_KEY=paper-key\n"
            "ALPACA_API_SECRET=paper-secret\n"
        ),
    )
    checks = collect_checks(
        root, python_version=(3, 12), package_versions=_versions(),
        uv_available=True,
    )
    assert set(_statuses(checks).values()) == {"PASS"}


def test_doctor_fails_closed_for_non_paper_mode_or_endpoint(tmp_path):
    root = _root(
        tmp_path,
        env_text=(
            "STMAA_TRADING_MODE=live\n"
            "ALPACA_BASE_URL=https://api.alpaca.markets\n"
        ),
    )
    statuses = _statuses(collect_checks(
        root, python_version=(3, 12), package_versions=_versions(),
        uv_available=True,
    ))
    assert statuses["Trading mode"] == "FAIL"
    assert statuses["Broker endpoint"] == "FAIL"


def test_doctor_fails_for_unsupported_python_and_missing_lock(tmp_path):
    root = _root(tmp_path, lock=False)
    statuses = _statuses(collect_checks(
        root, python_version=(3, 13), package_versions=_versions(),
        env={}, uv_available=True,
    ))
    assert statuses["Python"] == "FAIL"
    assert statuses["uv.lock"] == "FAIL"


def test_doctor_reports_missing_dependencies(tmp_path):
    root = _root(tmp_path, env={}, lock=True)
    versions = _versions()
    versions["torch"] = None
    statuses = _statuses(collect_checks(
        root, python_version=(3, 12), package_versions=versions,
        uv_available=True,
    ))
    assert statuses["Runtime dependencies"] == "FAIL"


def test_doctor_warns_when_env_or_credentials_are_not_ready(tmp_path):
    root = _root(tmp_path)
    checks = collect_checks(
        root, python_version=(3, 12), package_versions=_versions(),
        uv_available=True,
    )
    assert _statuses(checks)[".env"] == "WARN"

    (root / ".env").write_text(
        "STMAA_TRADING_MODE=paper\n"
        "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n",
        encoding="utf-8",
    )
    checks = collect_checks(
        root, python_version=(3, 12), package_versions=_versions(),
        uv_available=True,
    )
    assert _statuses(checks)["Paper credentials"] == "WARN"
    assert all("paper-key" not in check["detail"] for check in checks)


def test_doctor_fails_when_uv_is_unavailable(tmp_path):
    root = _root(tmp_path, env={}, lock=True)
    statuses = _statuses(collect_checks(
        root, python_version=(3, 12), package_versions=_versions(),
        uv_available=False,
    ))
    assert statuses["uv"] == "FAIL"
