"""Shared builders and in-memory fakes for the Slack layer tests (no network, no Slack)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from faultline.policy.opa import OpaDecision, OpaError, PolicyInput, build_policy_input
from faultline.schemas import (
    ActionTarget,
    Approval,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    Verdict,
)
from faultline.slack.approvals import PendingApproval
from faultline.slack.models import BriefAction, IncidentBrief
from tests.test_opa_client import LIMITS

INCIDENT_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTION_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 11, 15, 3, 2, 0, tzinfo=UTC)
EXPIRES = NOW + timedelta(minutes=15)
APPROVER = "U0APPROVER"
STRANGER = "U0STRANGER"
APPROVERS = {APPROVER: ["sre", "soc"], "U0NETONLY": ["network"]}


def make_verdict(kind: str = "git_revert") -> Verdict:
    action = ProposedAction(
        kind="git_revert",
        target=ActionTarget(commit_sha="9f3c2a1", namespace="boutique"),
        reason="Revert the change that preceded the errors",
    )
    if kind == "block_source":
        action = ProposedAction(
            kind="block_source",
            target=ActionTarget(cidr="172.18.5.0/24", namespace="ingress"),
            reason="Flood from one range",
        )
    return Verdict(
        hypotheses=[
            Hypothesis(type="bad_change", score=0.82, **{"for": ["chg-2", "hlt-4"]}),
            Hypothesis(type="attack", score=0.09, against=["trf-1", "sec-0"]),
            Hypothesis(type="network_fault", score=0.05, against=["hlt-7"]),
            Hypothesis(type="capacity", score=0.04, against=["trf-1"]),
        ],
        verdict="bad_change",
        explanation=(
            "Config commit chg-2 preceded errors by 4 min; failures limited to new pods (hlt-4); "
            "traffic shape normal (trf-1)."
        ),
        flip_conditions=["Attack alerts on checkout appear", "Client distribution shifts"],
        action=action,
        confidence=0.82,
    )


def make_evidence(n: int = 4) -> list[EvidenceItem]:
    packs = ["change", "health", "traffic", "security"]
    prefixes = ["chg", "hlt", "trf", "sec"]
    return [
        EvidenceItem(
            id=f"{prefixes[i % 4]}-{i}",
            pack=packs[i % 4],  # type: ignore[arg-type]
            summary=f"evidence item {i} <summary & detail>",
            data={},
            source="test",
        )
        for i in range(n)
    ]


def make_brief(
    *, mode: str = "normal", kind: str = "git_revert", with_action: bool = True
) -> IncidentBrief:
    verdict = make_verdict(kind)
    actions: list[BriefAction] = []
    if with_action and verdict.action is not None:
        actions = [
            BriefAction(
                action_id=ACTION_ID,
                kind=verdict.action.kind,
                target=verdict.action.target,
                reason=verdict.action.reason,
                risk="medium",
                ttl_minutes=None,
                pr_url="https://github.com/example/faultline-gitops/pull/12",
            )
        ]
    return IncidentBrief(
        incident_id=INCIDENT_ID,
        display_id="INC-0042",
        alert_name="HighErrorRate",
        service="checkoutservice",
        namespace="boutique",
        severity="high",
        mode=mode,  # type: ignore[arg-type]
        verdict=verdict,
        evidence=make_evidence(),
        actions=actions,
        team_mention="<!subteam^S0SRE>",
        expires_at=EXPIRES,
    )


def make_policy_input(kind: str = "git_revert") -> PolicyInput:
    verdict = make_verdict(kind)
    assert verdict.action is not None
    return build_policy_input(
        action=verdict.action,
        verdict=verdict.verdict,
        contributing_factor=None,
        confidence=verdict.confidence,
        actions_executed=0,
        change_on_path=True,
        limits=LIMITS,
    )


def make_pending(*, mode: str = "normal", kind: str = "git_revert") -> PendingApproval:
    return PendingApproval(
        incident_id=INCIDENT_ID,
        action_id=ACTION_ID,
        policy_input=make_policy_input(kind),
        expires_at=EXPIRES,
        status="pending",
        channel="C0INCIDENTS",
        message_ts="1700000000.000100",
        brief=make_brief(mode=mode, kind=kind),
    )


class FakeStore:
    def __init__(self, *pending: PendingApproval) -> None:
        self.items = {p.action_id: p for p in pending}
        self.decisions: dict[UUID, Approval] = {}

    async def get(self, action_id: UUID) -> PendingApproval | None:
        return self.items.get(action_id)

    async def record_decision(self, action_id: UUID, approval: Approval) -> bool:
        pending = self.items[action_id]
        if pending.status != "pending":
            return False
        self.decisions[action_id] = approval
        self.items[action_id] = pending.model_copy(update={"status": approval.decision})
        return True

    async def mark_expired(self, action_id: UUID) -> None:
        self.items[action_id] = self.items[action_id].model_copy(update={"status": "expired"})


class FakeResumer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def resume(
        self, *, incident_id: UUID, action_id: UUID, approval: Approval, idempotency_key: str
    ) -> None:
        self.calls.append(
            {
                "incident_id": incident_id,
                "action_id": action_id,
                "approval": approval,
                "idempotency_key": idempotency_key,
            }
        )


class FakeOpa:
    """Fake OPA: `approver_allowed` when any role in the input matches `allowed_roles`."""

    def __init__(self, allowed_roles: set[str] | None = None, fail: bool = False) -> None:
        self.allowed_roles = allowed_roles if allowed_roles is not None else {"sre"}
        self.fail = fail
        self.inputs: list[PolicyInput] = []

    async def evaluate(self, policy_input: PolicyInput) -> OpaDecision:
        self.inputs.append(policy_input)
        if self.fail:
            raise OpaError("OPA down")
        roles = policy_input.approver.roles if policy_input.approver else []
        allowed = any(r in self.allowed_roles for r in roles)
        # Mirror the real response: approver_allowed is present only when true.
        return OpaDecision.model_validate(
            {"allow": True, "deny": [], **({"approver_allowed": True} if allowed else {})}
        )
