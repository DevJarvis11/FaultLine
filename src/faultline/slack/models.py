"""Inputs to the Slack brief builders and the states a posted brief can move to."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from faultline.config import ActionSettings, Mode
from faultline.schemas import (
    ActionKind,
    ActionTarget,
    EvidenceItem,
    PolicyDecision,
    ProposedAction,
    Severity,
    Verdict,
)

Risk = Literal["low", "medium", "high"]

# Risk shown next to each proposed action; blocks and quarantines affect real traffic/pods.
RISK_BY_KIND: dict[ActionKind, Risk] = {
    "git_revert": "medium",
    "rate_limit": "low",
    "block_source": "high",
    "quarantine_pod": "high",
    "scale_out": "low",
    "page_team": "low",
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class BriefAction(_Frozen):
    """One proposed action as shown in the brief; `action_id` is the stored action's UUID."""

    action_id: UUID
    kind: ActionKind
    target: ActionTarget
    reason: str = Field(max_length=300)
    risk: Risk
    ttl_minutes: int | None = None
    pr_url: str | None = None
    policy: PolicyDecision | None = None


def ttl_minutes_for(kind: ActionKind, actions: ActionSettings) -> int | None:
    if kind == "block_source":
        return actions.block_ttl_minutes
    if kind == "rate_limit":
        return actions.rate_limit_ttl_minutes
    return None


def brief_action_from(
    action: ProposedAction,
    action_id: UUID,
    actions: ActionSettings,
    *,
    pr_url: str | None = None,
    policy: PolicyDecision | None = None,
) -> BriefAction:
    return BriefAction(
        action_id=action_id,
        kind=action.kind,
        target=action.target,
        reason=action.reason,
        risk=RISK_BY_KIND[action.kind],
        ttl_minutes=ttl_minutes_for(action.kind, actions),
        pr_url=pr_url,
        policy=policy,
    )


class IncidentBrief(_Frozen):
    incident_id: UUID
    display_id: str
    alert_name: str
    service: str
    namespace: str
    severity: Severity
    mode: Mode
    verdict: Verdict
    evidence: list[EvidenceItem] = []
    actions: list[BriefAction] = []
    team_mention: str | None = None
    expires_at: datetime | None = None

    @property
    def approval_open(self) -> bool:
        """Live buttons only in normal mode for an approvable action (page_team needs none)."""
        return (
            self.mode == "normal"
            and bool(self.actions)
            and all(a.kind != "page_team" for a in self.actions)
            and all(a.policy is None or a.policy.allow for a in self.actions)
        )


# ---- States a brief moves to after it was posted ----


class Approved(_Frozen):
    state: Literal["approved"] = "approved"
    by: str
    at: datetime


class Rejected(_Frozen):
    state: Literal["rejected"] = "rejected"
    by: str
    reason: str
    at: datetime


class Expired(_Frozen):
    state: Literal["expired"] = "expired"
    at: datetime


class Superseded(_Frozen):
    state: Literal["superseded"] = "superseded"
    note: str
    at: datetime


class ActionResult(_Frozen):
    state: Literal["result"] = "result"
    ok: bool
    summary: str
    at: datetime


DecisionState = Approved | Rejected | Expired | Superseded | ActionResult
