"""Approval service: approver check via OPA, expiry, double clicks, reject flow (no Slack)."""

from datetime import timedelta
from uuid import uuid4

import pytest

from faultline.config import ApprovalSettings
from faultline.slack.approvals import ApprovalService, expiry_deadline
from faultline.slack.idempotency import decision_key
from tests.slack_helpers import (
    ACTION_ID,
    APPROVER,
    APPROVERS,
    EXPIRES,
    INCIDENT_ID,
    NOW,
    STRANGER,
    FakeOpa,
    FakeResumer,
    FakeStore,
    make_pending,
)


def _service(
    store: FakeStore | None = None,
    opa: FakeOpa | None = None,
    resumer: FakeResumer | None = None,
    at: object = NOW,
) -> tuple[ApprovalService, FakeStore, FakeOpa, FakeResumer]:
    store = store or FakeStore(make_pending())
    opa = opa or FakeOpa()
    resumer = resumer or FakeResumer()
    service = ApprovalService(
        store=store, resumer=resumer, policy=opa, approvers=APPROVERS, now=lambda: at
    )
    return service, store, opa, resumer


async def test_approve_happy_path_resumes_once_with_idempotency_key() -> None:
    service, store, opa, resumer = _service()
    outcome = await service.approve(str(ACTION_ID), APPROVER)
    assert outcome.accepted
    assert outcome.update is not None and "Approved by <@U0APPROVER>" in str(outcome.update.blocks)
    assert outcome.pending is not None and outcome.pending.channel == "C0INCIDENTS"
    assert len(resumer.calls) == 1
    call = resumer.calls[0]
    assert call["incident_id"] == INCIDENT_ID and call["action_id"] == ACTION_ID
    assert call["idempotency_key"] == decision_key(ACTION_ID)
    approval = store.decisions[ACTION_ID]
    assert approval.decision == "approved" and approval.slack_user_id == APPROVER
    assert approval.decided_at == NOW
    # OPA was asked with the clicker's roles attached to the stored policy input.
    assert opa.inputs[-1].approver is not None
    assert opa.inputs[-1].approver.roles == ["sre", "soc"]


async def test_second_click_is_ignored() -> None:
    service, _, _, resumer = _service()
    assert (await service.approve(str(ACTION_ID), APPROVER)).accepted
    again = await service.approve(str(ACTION_ID), APPROVER)
    assert not again.accepted
    assert "already" in again.user_message.lower()
    assert again.update is None
    assert len(resumer.calls) == 1


async def test_unknown_user_is_refused_without_asking_opa() -> None:
    service, store, opa, resumer = _service()
    outcome = await service.approve(str(ACTION_ID), STRANGER)
    assert not outcome.accepted and "not an approver" in outcome.user_message
    assert opa.inputs == [] and resumer.calls == [] and store.decisions == {}


async def test_user_with_wrong_role_is_refused_by_opa() -> None:
    service, store, opa, resumer = _service(opa=FakeOpa(allowed_roles={"sre"}))
    outcome = await service.approve(str(ACTION_ID), "U0NETONLY")
    assert not outcome.accepted and "role" in outcome.user_message.lower()
    assert len(opa.inputs) == 1 and resumer.calls == [] and store.decisions == {}


async def test_opa_failure_fails_closed() -> None:
    service, store, _, resumer = _service(opa=FakeOpa(fail=True))
    outcome = await service.approve(str(ACTION_ID), APPROVER)
    assert not outcome.accepted and "policy" in outcome.user_message.lower()
    assert resumer.calls == [] and store.decisions == {}


async def test_expired_approval_is_refused_and_brief_marked_expired() -> None:
    service, store, _, resumer = _service(at=EXPIRES + timedelta(seconds=1))
    outcome = await service.approve(str(ACTION_ID), APPROVER)
    assert not outcome.accepted and "expired" in outcome.user_message.lower()
    assert outcome.update is not None and "expired" in str(outcome.update.blocks).lower()
    assert store.items[ACTION_ID].status == "expired"
    assert resumer.calls == []


async def test_click_exactly_at_deadline_still_counts() -> None:
    service, _, _, resumer = _service(at=EXPIRES)
    assert (await service.approve(str(ACTION_ID), APPROVER)).accepted
    assert len(resumer.calls) == 1


async def test_unknown_or_malformed_action_value_is_refused() -> None:
    service, _, _, resumer = _service()
    assert not (await service.approve("not-a-uuid", APPROVER)).accepted
    assert not (await service.approve(str(uuid4()), APPROVER)).accepted
    assert resumer.calls == []


async def test_shadow_mode_pending_cannot_be_approved() -> None:
    service, _, _, resumer = _service(store=FakeStore(make_pending(mode="shadow")))
    outcome = await service.approve(str(ACTION_ID), APPROVER)
    assert not outcome.accepted and "shadow" in outcome.user_message.lower()
    assert resumer.calls == []


# ---- Reject flow ----


async def test_reject_gate_checks_approver_before_opening_modal() -> None:
    service, _, _, _ = _service()
    assert (await service.can_reject(str(ACTION_ID), APPROVER)).accepted
    assert not (await service.can_reject(str(ACTION_ID), STRANGER)).accepted


async def test_reject_records_reason_and_resumes() -> None:
    service, store, _, resumer = _service()
    outcome = await service.reject(str(ACTION_ID), APPROVER, "  wrong commit  ")
    assert outcome.accepted
    assert outcome.update is not None and "wrong commit" in str(outcome.update.blocks)
    approval = store.decisions[ACTION_ID]
    assert approval.decision == "rejected" and approval.reason == "wrong commit"
    assert len(resumer.calls) == 1
    assert resumer.calls[0]["idempotency_key"] == decision_key(ACTION_ID)


async def test_reject_requires_reason() -> None:
    service, store, _, resumer = _service()
    outcome = await service.reject(str(ACTION_ID), APPROVER, "   ")
    assert not outcome.accepted and "reason" in outcome.user_message.lower()
    assert store.decisions == {} and resumer.calls == []


async def test_reject_after_approve_is_ignored() -> None:
    service, _, _, resumer = _service()
    await service.approve(str(ACTION_ID), APPROVER)
    outcome = await service.reject(str(ACTION_ID), APPROVER, "changed my mind")
    assert not outcome.accepted and len(resumer.calls) == 1


async def test_concurrent_clicks_resume_once() -> None:
    import asyncio

    service, _, _, resumer = _service()
    results = await asyncio.gather(
        service.approve(str(ACTION_ID), APPROVER),
        service.approve(str(ACTION_ID), APPROVER),
        service.reject(str(ACTION_ID), APPROVER, "no"),
    )
    assert sum(1 for r in results if r.accepted) == 1
    assert len(resumer.calls) == 1


def test_expiry_deadline_uses_settings() -> None:
    assert expiry_deadline(NOW, ApprovalSettings(expiry_minutes=15)) == EXPIRES


def test_expiry_deadline_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="aware"):
        expiry_deadline(NOW.replace(tzinfo=None), ApprovalSettings(expiry_minutes=15))
