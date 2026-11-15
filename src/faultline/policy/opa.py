"""Ask OPA whether an action, and an approver, are allowed.

`POST {OPA_URL}/v1/data/faultline/actions` with `{"input": {...}}`. The result holds
`allow`, `deny` and `approver_allowed` (only when true). Any failure to get a decision
raises `OpaError`: callers must treat that as a denial (fail closed).
"""

import ipaddress
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from faultline.config import ActionSettings
from faultline.schemas import (
    ActionKind,
    HypothesisType,
    PolicyDecision,
    ProposedAction,
    VerdictType,
)

DECISION_PATH = "/v1/data/faultline/actions"


class OpaError(RuntimeError):
    """No usable decision from OPA; treat as deny."""


class _In(BaseModel):
    model_config = ConfigDict(frozen=True)


class PolicyTarget(_In):
    commit_sha: str | None = None
    route: str | None = None
    cidr: str | None = None
    prefix_length: int | None = None
    namespace: str | None = None
    workload: str | None = None
    replicas: int | None = None
    team: str | None = None


class PolicyAction(_In):
    kind: ActionKind
    target: PolicyTarget


class PolicyVerdict(_In):
    verdict: VerdictType
    contributing_factor: HypothesisType | None
    confidence: float = Field(ge=0, le=1)


class PolicyIncident(_In):
    actions_executed: int = Field(ge=0)
    change_on_path: bool


class PolicyLimits(_In):
    max_per_incident: int
    block_min_confidence: float
    block_min_prefix_v4: int
    scale_max_replicas: int


class PolicyApprover(_In):
    roles: list[str]


class PolicyInput(_In):
    action: PolicyAction
    verdict: PolicyVerdict
    incident: PolicyIncident
    limits: PolicyLimits
    approver: PolicyApprover | None = None

    def with_approver(self, roles: list[str]) -> Self:
        return self.model_copy(update={"approver": PolicyApprover(roles=roles)})

    def to_body(self) -> dict[str, object]:
        # Keep explicit nulls inside `verdict` (the policy reads contributing_factor);
        # drop unset target fields and a missing approver.
        body = self.model_dump(mode="json", exclude={"approver"})
        body["action"]["target"] = self.action.target.model_dump(mode="json", exclude_none=True)
        if self.approver is not None:
            body["approver"] = self.approver.model_dump(mode="json")
        return {"input": body}


class OpaDecision(BaseModel):
    allow: bool
    deny: list[str] = []
    approver_allowed: bool = False

    def to_policy_decision(self) -> PolicyDecision:
        return PolicyDecision(allow=self.allow, deny_reasons=list(self.deny))


def prefix_length_v4(cidr: str) -> int:
    """Prefix length of an IPv4 address or network; the block rule covers IPv4 only."""
    net = ipaddress.ip_network(cidr, strict=False)
    if net.version != 4:
        raise ValueError(f"only IPv4 is supported: {cidr}")
    return net.prefixlen


def limits_from_settings(actions: ActionSettings) -> PolicyLimits:
    return PolicyLimits(
        max_per_incident=actions.max_per_incident,
        block_min_confidence=actions.block_min_confidence,
        block_min_prefix_v4=actions.block_min_prefix_v4,
        scale_max_replicas=actions.scale_max_replicas,
    )


def build_policy_input(
    *,
    action: ProposedAction,
    verdict: VerdictType,
    contributing_factor: HypothesisType | None,
    confidence: float,
    actions_executed: int,
    change_on_path: bool,
    limits: ActionSettings,
) -> PolicyInput:
    """Assemble the OPA input; `prefix_length` is computed here from the CIDR."""
    target = action.target.model_dump()
    if action.target.cidr is not None:
        target["prefix_length"] = prefix_length_v4(action.target.cidr)
    return PolicyInput(
        action=PolicyAction(kind=action.kind, target=PolicyTarget(**target)),
        verdict=PolicyVerdict(
            verdict=verdict, contributing_factor=contributing_factor, confidence=confidence
        ),
        incident=PolicyIncident(actions_executed=actions_executed, change_on_path=change_on_path),
        limits=limits_from_settings(limits),
    )


class OpaClient:
    def __init__(self, base_url: str, timeout_s: float = 5.0) -> None:
        self._url = base_url.rstrip("/") + DECISION_PATH
        self._timeout = timeout_s

    async def evaluate(self, policy_input: PolicyInput) -> OpaDecision:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=policy_input.to_body())
            resp.raise_for_status()
            result = resp.json().get("result")
        except (httpx.HTTPError, ValueError) as exc:
            raise OpaError(f"OPA request failed: {exc}") from exc
        if not isinstance(result, dict):
            raise OpaError("OPA returned no result (is the policy loaded?)")
        try:
            return OpaDecision.model_validate(result)
        except ValidationError as exc:
            raise OpaError(f"OPA result has an unexpected shape: {exc}") from exc
