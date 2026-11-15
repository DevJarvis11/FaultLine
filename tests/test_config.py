from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from faultline.config import (
    AppSettings,
    EnvSettings,
    ServiceCatalog,
    load_env,
    load_service_catalog,
    load_settings,
)

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> EnvSettings:
    for key in ("FAULTLINE_MODE", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return load_env(env_file=None)


def test_repo_settings_load(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(REPO_CONFIG / "settings.yaml", _env(monkeypatch))
    assert isinstance(settings, AppSettings)
    assert settings.mode == "shadow"
    assert settings.evidence.max_tokens_total == 6000
    assert settings.verifier.min_confidence == 0.6
    assert settings.actions.block_min_prefix_v4 == 24


def test_env_mode_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(monkeypatch, FAULTLINE_MODE="normal")
    assert load_settings(REPO_CONFIG / "settings.yaml", env).mode == "normal"


def test_invalid_env_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _env(monkeypatch, FAULTLINE_MODE="yolo")


def test_secrets_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(monkeypatch, LLM_API_KEY="sk-test-value")
    assert env.llm_api_key is not None
    assert "sk-test-value" not in repr(env)
    assert env.llm_api_key.get_secret_value() == "sk-test-value"


def test_unknown_settings_key_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = (REPO_CONFIG / "settings.yaml").read_text() + "\nunexpected: 1\n"
    path = tmp_path / "settings.yaml"
    path.write_text(text)
    with pytest.raises(ValidationError):
        load_settings(path, _env(monkeypatch))


def test_confidence_out_of_range_rejected() -> None:
    data = AppSettings.model_validate(_settings_dict()).model_dump()
    data["verifier"]["min_confidence"] = 1.5
    with pytest.raises(ValidationError):
        AppSettings.model_validate(data)


def test_repo_service_catalog_loads() -> None:
    catalog = load_service_catalog(REPO_CONFIG / "service_catalog.yaml")
    assert isinstance(catalog, ServiceCatalog)
    assert len(catalog.services) == 11
    assert catalog.services["frontend"].public is True
    assert catalog.services["cartservice"].public is False
    assert catalog.action_roles["block_source"] == ["soc"]


def test_catalog_rejects_unknown_team() -> None:
    data = _catalog_dict()
    data["services"]["frontend"]["team"] = "nobody"
    with pytest.raises(ValidationError):
        ServiceCatalog.model_validate(data)


def test_catalog_rejects_unknown_approver_role() -> None:
    data = _catalog_dict()
    data["approvers"] = {"U1": ["admin"]}
    with pytest.raises(ValidationError):
        ServiceCatalog.model_validate(data)


def _settings_dict() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((REPO_CONFIG / "settings.yaml").read_text())
    return data


def _catalog_dict() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((REPO_CONFIG / "service_catalog.yaml").read_text())
    return data
