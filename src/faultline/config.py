"""Typed configuration loading: secrets from the environment, behaviour from YAML."""

import re
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from faultline.schemas import ActionKind

Mode = Literal["shadow", "normal"]

CONFIG_DIR = Path("config")


class EnvSettings(BaseSettings):
    """Values from `.env` / the process environment (see `.env.example`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    prometheus_url: str | None = None
    loki_url: str | None = None
    opa_url: str | None = None
    mcp_url: str | None = None
    argocd_url: str | None = None
    argocd_token: SecretStr | None = None
    github_app_id: str | None = None
    github_app_private_key_path: Path | None = None
    github_installation_id: str | None = None
    gitops_repo: str | None = None
    slack_bot_token: SecretStr | None = None
    slack_app_token: SecretStr | None = None
    slack_channel_id: str | None = None
    mlflow_tracking_uri: str | None = None
    faultline_mode: Mode | None = None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IncidentSettings(_Strict):
    dedupe_window_minutes: int = Field(gt=0)
    evidence_lookback_minutes: int = Field(gt=0)
    baseline_window_minutes: int = Field(gt=0)


class EvidenceSettings(_Strict):
    max_tokens_total: int = Field(gt=0)
    collector_timeout_seconds: int = Field(gt=0)
    top_n: int = Field(gt=0)


class TriageSettings(_Strict):
    timeout_seconds: int = Field(gt=0)


class InvestigatorSettings(_Strict):
    max_tool_calls: int = Field(gt=0)
    budget_seconds: int = Field(gt=0)


class VerifierSettings(_Strict):
    min_confidence: float = Field(ge=0, le=1)
    max_attempts: int = Field(gt=0)


class ApprovalSettings(_Strict):
    expiry_minutes: int = Field(gt=0)


class ActionSettings(_Strict):
    max_per_incident: int = Field(gt=0)
    block_min_confidence: float = Field(ge=0, le=1)
    block_min_prefix_v4: int = Field(ge=0, le=32)
    block_ttl_minutes: int = Field(gt=0)
    rate_limit_ttl_minutes: int = Field(gt=0)
    scale_max_replicas: int = Field(gt=0)


class RecoverySettings(_Strict):
    watch_minutes: int = Field(gt=0)


class AppSettings(_Strict):
    """`config/settings.yaml`."""

    incident: IncidentSettings
    evidence: EvidenceSettings
    triage: TriageSettings
    investigator: InvestigatorSettings
    verifier: VerifierSettings
    approval: ApprovalSettings
    actions: ActionSettings
    recovery: RecoverySettings
    mode: Mode


class ServiceEntry(_Strict):
    namespace: str
    team: str
    public: bool = False


class Team(_Strict):
    slack_mention: str


class ServiceCatalog(_Strict):
    """`config/service_catalog.yaml`."""

    services: dict[str, ServiceEntry]
    teams: dict[str, Team]
    approvers: dict[str, list[str]]
    action_roles: dict[ActionKind, list[str]]

    @model_validator(mode="after")
    def _roles_are_known_teams(self) -> Self:
        known = set(self.teams)
        used = {s.team for s in self.services.values()}
        used |= {r for roles in self.approvers.values() for r in roles}
        used |= {r for roles in self.action_roles.values() for r in roles}
        unknown = used - known
        if unknown:
            raise ValueError(f"unknown team or role: {sorted(unknown)}")
        return self


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_env(env_file: Path | str | None = ".env") -> EnvSettings:
    """Load environment settings; pass `env_file=None` to use only the process env."""
    return EnvSettings(_env_file=env_file)


def load_settings(
    path: Path = CONFIG_DIR / "settings.yaml", env: EnvSettings | None = None
) -> AppSettings:
    """Load `settings.yaml`; `FAULTLINE_MODE` overrides `mode` when set."""
    settings = AppSettings.model_validate(_read_yaml(path))
    env = env if env is not None else load_env()
    if env.faultline_mode is not None:
        settings = settings.model_copy(update={"mode": env.faultline_mode})
    return settings


def load_service_catalog(path: Path = CONFIG_DIR / "service_catalog.yaml") -> ServiceCatalog:
    return ServiceCatalog.model_validate(_read_yaml(path))


# ---- config/models.yaml (DESIGN: Model configuration) ----

ChatRoleName = Literal["triage", "investigator", "report"]
ReasoningEffort = Literal["low", "medium", "high"]

_PLACEHOLDER = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


class ConfigError(RuntimeError):
    """A required configuration value is missing."""


class ProviderConfig(_Strict):
    base_url: str | None
    api_key: SecretStr | None
    timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)

    def require(self) -> tuple[str, str]:
        """Return (base_url, api_key) or fail if `.env` does not provide them."""
        if not self.base_url:
            raise ConfigError("LLM_BASE_URL is not set")
        if self.api_key is None:
            raise ConfigError("LLM_API_KEY is not set")
        return self.base_url.rstrip("/"), self.api_key.get_secret_value()


class ChatRole(_Strict):
    model: str
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)
    reasoning_effort: ReasoningEffort


class EmbeddingRole(_Strict):
    model: str
    dimensions: int = Field(gt=0)


class Roles(_Strict):
    triage: ChatRole
    investigator: ChatRole
    report: ChatRole
    embedding: EmbeddingRole

    def chat_roles(self) -> dict[ChatRoleName, ChatRole]:
        return {"triage": self.triage, "investigator": self.investigator, "report": self.report}


class ModelsConfig(_Strict):
    provider: ProviderConfig
    roles: Roles
    fallbacks: dict[ChatRoleName, str] = {}


def _fill_placeholders(value: Any, env: dict[str, Any]) -> Any:
    """Replace whole-string `${NAME}` values with env values (None when unset)."""
    if isinstance(value, dict):
        return {k: _fill_placeholders(v, env) for k, v in value.items()}
    if isinstance(value, str) and (m := _PLACEHOLDER.match(value)):
        found = env.get(m.group(1).lower())
        return found.get_secret_value() if isinstance(found, SecretStr) else found
    return value


def load_models(
    path: Path = CONFIG_DIR / "models.yaml", env: EnvSettings | None = None
) -> ModelsConfig:
    env = env if env is not None else load_env()
    raw = _fill_placeholders(_read_yaml(path), dict(env))
    return ModelsConfig.model_validate(raw)
