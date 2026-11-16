"""Idempotency keys for actions and decisions (DESIGN: Persistence, idempotency and concurrency)."""

from uuid import UUID, uuid4

from faultline.schemas import ActionTarget
from faultline.slack.idempotency import action_key, decision_key

INCIDENT = UUID("11111111-1111-4111-8111-111111111111")


def test_action_key_is_deterministic_and_hex() -> None:
    target = ActionTarget(commit_sha="9f3c2a1", namespace="boutique")
    first = action_key(INCIDENT, "git_revert", target)
    second = action_key(
        INCIDENT, "git_revert", ActionTarget(namespace="boutique", commit_sha="9f3c2a1")
    )
    assert first == second
    assert len(first) == 64 and int(first, 16) >= 0


def test_action_key_changes_with_incident_kind_or_target() -> None:
    target = ActionTarget(cidr="172.18.5.0/24")
    base = action_key(INCIDENT, "block_source", target)
    assert action_key(uuid4(), "block_source", target) != base
    assert action_key(INCIDENT, "rate_limit", target) != base
    assert action_key(INCIDENT, "block_source", ActionTarget(cidr="172.18.6.0/24")) != base


def test_action_key_ignores_unset_target_fields() -> None:
    # None and "not provided" are the same target.
    a = action_key(INCIDENT, "scale_out", ActionTarget(workload="frontend", replicas=3))
    b = action_key(INCIDENT, "scale_out", ActionTarget(workload="frontend", replicas=3, team=None))
    assert a == b


def test_decision_key_is_one_per_action() -> None:
    action_id = uuid4()
    assert decision_key(action_id) == decision_key(action_id)
    assert decision_key(action_id) != decision_key(uuid4())
    assert str(action_id) in decision_key(action_id)
