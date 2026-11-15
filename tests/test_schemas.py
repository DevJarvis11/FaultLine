from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from faultline.schemas import (
    AMWebhook,
    Approval,
    EvidenceItem,
    EvidencePacks,
    Hypothesis,
    ProposedAction,
    TriageResult,
    Verdict,
)

HYPS = ["attack", "network_fault", "bad_change", "capacity"]


def _webhook() -> dict[str, Any]:
    return {
        "version": "4",
        "groupKey": '{}:{alertname="EdgeErrorRatio"}',
        "status": "firing",
        "receiver": "faultline",
        "groupLabels": {"alertname": "EdgeErrorRatio"},
        "commonLabels": {"alertname": "EdgeErrorRatio", "severity": "critical"},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "EdgeErrorRatio"},
                "startsAt": "2026-11-15T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "fingerprint": "abc123",
            }
        ],
    }


def _hypotheses() -> list[dict[str, Any]]:
    return [{"type": t, "score": 0.25} for t in HYPS]


def _verdict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "hypotheses": _hypotheses(),
        "verdict": "bad_change",
        "explanation": "error ratio rose after the deploy",
        "flip_conditions": ["errors persist after revert"],
        "confidence": 0.8,
    }
    data.update(overrides)
    return data


def test_webhook_parses() -> None:
    hook = AMWebhook.model_validate(_webhook())
    assert hook.alerts[0].startsAt == datetime(2026, 11, 15, 10, tzinfo=UTC)
    assert hook.alerts[0].annotations == {}


def test_webhook_rejects_bad_status() -> None:
    data = _webhook()
    data["status"] = "pending"
    with pytest.raises(ValidationError):
        AMWebhook.model_validate(data)


@pytest.mark.parametrize("item_id", ["chg-1", "trf-10", "sec-0", "hlt-42"])
def test_evidence_id_valid(item_id: str) -> None:
    EvidenceItem(id=item_id, pack="change", summary="s", data={}, source="prometheus")


@pytest.mark.parametrize("item_id", ["chg1", "xyz-1", "chg-", "chg-a", " chg-1"])
def test_evidence_id_invalid(item_id: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(id=item_id, pack="change", summary="s", data={}, source="prometheus")


def test_evidence_summary_max_length() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(id="chg-1", pack="change", summary="x" * 201, data={}, source="git")


def test_evidence_packs_defaults() -> None:
    packs = EvidencePacks()
    assert packs.change == [] and packs.dropped_counts == {}


def test_triage_needs_exactly_four_hypotheses() -> None:
    base = {"severity": "high", "plan": ["get_changes"], "notes": ""}
    TriageResult.model_validate({**base, "ranked_hypotheses": HYPS})
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**base, "ranked_hypotheses": HYPS[:3]})


def test_triage_plan_limits() -> None:
    base = {"severity": "low", "ranked_hypotheses": HYPS, "notes": ""}
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**base, "plan": ["get_changes"] * 7})
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**base, "plan": ["drop_tables"]})


def test_hypothesis_for_alias() -> None:
    hyp = Hypothesis.model_validate({"type": "attack", "score": 0.5, "for": ["trf-1"]})
    assert hyp.for_ == ["trf-1"]
    assert Hypothesis(type="attack", score=0.5, for_=["trf-2"]).for_ == ["trf-2"]
    assert hyp.model_dump(by_alias=True)["for"] == ["trf-1"]


def test_hypothesis_score_range() -> None:
    with pytest.raises(ValidationError):
        Hypothesis(type="attack", score=1.1)


def test_verdict_valid_with_action() -> None:
    action = {"kind": "git_revert", "target": {"commit_sha": "abc"}, "reason": "bad deploy"}
    verdict = Verdict.model_validate(_verdict(action=action))
    assert isinstance(verdict.action, ProposedAction)
    assert verdict.action.target.route is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"confidence": 1.5},
        {"verdict": "aliens"},
        {"hypotheses": _hypotheses()[:3]},
        {"flip_conditions": ["x"] * 6},
        {"explanation": "x" * 801},
        {"contributing_factor": "unknown"},
        {"action": {"kind": "delete_cluster", "target": {}, "reason": "no"}},
    ],
)
def test_verdict_rejects(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Verdict.model_validate(_verdict(**overrides))


def test_verdict_unknown_allowed() -> None:
    assert Verdict.model_validate(_verdict(verdict="unknown")).verdict == "unknown"


def test_approval_decision_values() -> None:
    now = datetime.now(UTC)
    Approval(slack_user_id="U1", decision="approved", decided_at=now)
    with pytest.raises(ValidationError):
        Approval.model_validate({"slack_user_id": "U1", "decision": "maybe", "decided_at": now})


def test_verdict_json_schema_exports() -> None:
    schema = Verdict.model_json_schema()
    assert "hypotheses" in schema["properties"]
