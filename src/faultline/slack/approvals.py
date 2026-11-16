"""Approval decisions behind the Slack buttons (DESIGN: Approval flow; BUILD_SPEC: Button handling).

No Slack calls here. `ApprovalService` resolves the opaque button value to a stored pending
approval, checks status, expiry and the approver (catalog roles, then OPA `approver_allowed`),
records the decision once, and resumes the workflow. The store and resumer are Protocols:
Sessions 8-9 plug in PostgreSQL and LangGraph.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from faultline.config import ApprovalSettings
from faultline.policy.opa import OpaDecision, OpaError, PolicyInput
from faultline.schemas import Approval
from faultline.slack import blocks
from faultline.slack.idempotency import decision_key
from faultline.slack.models import Approved, Expired, IncidentBrief, Rejected

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "superseded"]


class PendingApproval(BaseModel):
    """What the store holds for one proposed action awaiting a human."""

    model_config = ConfigDict(frozen=True)

    incident_id: UUID
    action_id: UUID
    policy_input: PolicyInput
    expires_at: datetime
    status: ApprovalStatus
    channel: str
    message_ts: str
    brief: IncidentBrief


class ApprovalStore(Protocol):
    async def get(self, action_id: UUID) -> PendingApproval | None: ...

    async def record_decision(self, action_id: UUID, approval: Approval) -> bool:
        """Record the one decision for an action; False when one already exists."""
        ...

    async def mark_expired(self, action_id: UUID) -> None: ...


class WorkflowResumer(Protocol):
    async def resume(
        self, *, incident_id: UUID, action_id: UUID, approval: Approval, idempotency_key: str
    ) -> None: ...


class ApproverPolicy(Protocol):
    async def evaluate(self, policy_input: PolicyInput) -> OpaDecision: ...


class Outcome(BaseModel):
    """`user_message` goes to the clicker (ephemeral); `update` replaces the brief if set."""

    accepted: bool
    user_message: str
    update: blocks.Message | None = None
    pending: PendingApproval | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def expiry_deadline(created_at: datetime, settings: ApprovalSettings) -> datetime:
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    return created_at + timedelta(minutes=settings.expiry_minutes)


def _refuse(message: str, pending: PendingApproval | None = None) -> Outcome:
    return Outcome(accepted=False, user_message=message, pending=pending)


class ApprovalService:
    def __init__(
        self,
        *,
        store: ApprovalStore,
        resumer: WorkflowResumer,
        policy: ApproverPolicy,
        approvers: dict[str, list[str]],
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._store = store
        self._resumer = resumer
        self._policy = policy
        self._approvers = approvers
        self._now = now

    async def approve(self, action_value: str, user_id: str) -> Outcome:
        gate = await self._gate(action_value, user_id)
        if gate.pending is None or not gate.accepted:
            return gate
        approval = Approval(slack_user_id=user_id, decision="approved", decided_at=self._now())
        return await self._commit(gate.pending, approval)

    async def can_reject(self, action_value: str, user_id: str) -> Outcome:
        """Checked before opening the reason modal, so a stranger never sees it."""
        return await self._gate(action_value, user_id)

    async def reject(self, action_value: str, user_id: str, reason: str) -> Outcome:
        reason = reason.strip()
        if not reason:
            return _refuse("A reason is required to reject.")
        gate = await self._gate(action_value, user_id)
        if gate.pending is None or not gate.accepted:
            return gate
        approval = Approval(
            slack_user_id=user_id, decision="rejected", reason=reason, decided_at=self._now()
        )
        return await self._commit(gate.pending, approval)

    # ---- checks ----

    async def _gate(self, action_value: str, user_id: str) -> Outcome:
        pending = await self._load(action_value)
        if pending is None:
            return _refuse("This action is unknown or no longer exists.")
        if pending.status != "pending":
            return _refuse(f"This action was already {pending.status}; nothing changed.", pending)
        if pending.brief.mode == "shadow":
            return _refuse("Shadow mode: this brief takes no action.", pending)
        if self._now() > pending.expires_at:
            return await self._expire(pending)
        return await self._check_approver(pending, user_id)

    async def _load(self, action_value: str) -> PendingApproval | None:
        try:
            action_id = UUID(action_value)
        except ValueError:
            return None
        return await self._store.get(action_id)

    async def _expire(self, pending: PendingApproval) -> Outcome:
        await self._store.mark_expired(pending.action_id)
        update = blocks.build_decided_brief(pending.brief, Expired(at=self._now()))
        return Outcome(
            accepted=False,
            user_message="This approval has expired; no action was taken.",
            update=update,
            pending=pending,
        )

    async def _check_approver(self, pending: PendingApproval, user_id: str) -> Outcome:
        roles = self._approvers.get(user_id, [])
        if not roles:
            return _refuse("You are not an approver for Faultline actions.", pending)
        try:
            decision = await self._policy.evaluate(pending.policy_input.with_approver(roles))
        except OpaError:
            return _refuse("Policy check unavailable; the action was not approved.", pending)
        if not decision.approver_allowed:
            return _refuse("Your role may not approve this kind of action.", pending)
        return Outcome(accepted=True, user_message="", pending=pending)

    # ---- commit ----

    async def _commit(self, pending: PendingApproval, approval: Approval) -> Outcome:
        if not await self._store.record_decision(pending.action_id, approval):
            return _refuse("This action was already decided; nothing changed.", pending)
        await self._resumer.resume(
            incident_id=pending.incident_id,
            action_id=pending.action_id,
            approval=approval,
            idempotency_key=decision_key(pending.action_id),
        )
        if approval.decision == "approved":
            state: Approved | Rejected = Approved(by=approval.slack_user_id, at=approval.decided_at)
            message = "Approved. Faultline is applying the action."
        else:
            state = Rejected(
                by=approval.slack_user_id, reason=approval.reason or "", at=approval.decided_at
            )
            message = "Rejected. The reason is recorded."
        update = blocks.build_decided_brief(pending.brief, state)
        return Outcome(accepted=True, user_message=message, update=update, pending=pending)
