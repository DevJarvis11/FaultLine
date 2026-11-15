from pathlib import Path

import pytest

from faultline.config import ConfigError, EnvSettings, ModelsConfig, load_env, load_models

REPO_MODELS = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> EnvSettings:
    for key in ("LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return load_env(env_file=None)


def test_repo_models_yaml_matches_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_models(REPO_MODELS, _env(monkeypatch))
    assert isinstance(cfg, ModelsConfig)
    assert cfg.roles.embedding.dimensions == 2560
    assert cfg.roles.triage.reasoning_effort == "low"
    assert cfg.roles.investigator.reasoning_effort == "medium"
    assert cfg.roles.report.reasoning_effort == "low"
    assert cfg.fallbacks == {}
    assert cfg.provider.timeout_s == 120
    assert cfg.provider.max_retries == 2


def test_placeholders_filled_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_models(REPO_MODELS, _env(monkeypatch, LLM_BASE_URL="http://gw:4000",
                                         LLM_API_KEY="k-123"))
    url, key = cfg.provider.require()
    assert url == "http://gw:4000"
    assert key == "k-123"
    assert "k-123" not in repr(cfg)


def test_missing_provider_env_fails_only_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_models(REPO_MODELS, _env(monkeypatch))  # migrate needs only the dimension
    assert cfg.provider.base_url is None
    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        cfg.provider.require()


def test_chat_roles_lists_three_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_models(REPO_MODELS, _env(monkeypatch))
    assert set(cfg.roles.chat_roles()) == {"triage", "investigator", "report"}


def test_unknown_reasoning_effort_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = REPO_MODELS.read_text().replace("reasoning_effort: medium", "reasoning_effort: max")
    bad = tmp_path / "models.yaml"
    bad.write_text(text)
    with pytest.raises(ValueError):
        load_models(bad, _env(monkeypatch))
