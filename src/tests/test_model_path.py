import pytest

from src.utils.model_path import (
    DEFAULT_MODEL_PATH,
    MODEL_PATH_ENV,
    repository_root,
    resolve_model_path,
)


def test_default_model_path_resolves_to_repository_models_directory():
    assert resolve_model_path() == repository_root() / DEFAULT_MODEL_PATH


def test_relative_model_path_resolves_against_repository_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = resolve_model_path("artifacts/custom.zip")
    assert resolved == repository_root() / "artifacts/custom.zip"


def test_absolute_model_path_is_preserved(tmp_path):
    target = tmp_path / "custom.zip"
    assert resolve_model_path(target) == target


def test_explicit_model_path_overrides_environment(tmp_path):
    explicit = tmp_path / "explicit.zip"
    environment = {MODEL_PATH_ENV: str(tmp_path / "environment.zip")}
    assert resolve_model_path(explicit, environ=environment) == explicit


def test_environment_model_path_overrides_default(tmp_path):
    target = tmp_path / "environment.zip"
    assert resolve_model_path(environ={MODEL_PATH_ENV: str(target)}) == target


def test_home_directory_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_model_path("~/models/custom.zip") == tmp_path / "models/custom.zip"


def test_empty_environment_model_path_is_rejected():
    with pytest.raises(ValueError, match="STMAA_MODEL_PATH must not be empty"):
        resolve_model_path(environ={MODEL_PATH_ENV: ""})


def test_explicit_empty_model_path_is_rejected():
    with pytest.raises(ValueError, match="model_path must not be empty"):
        resolve_model_path("   ")
