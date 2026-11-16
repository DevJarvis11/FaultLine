"""Block Kit builders: golden structure, phone-first ordering, Slack limits and truncation."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from faultline.schemas import Report
from faultline.slack import blocks as b
from faultline.slack.models import (
    ActionResult,
    Approved,
    Expired,
    Rejected,
    Superseded,
)
from tests.slack_helpers import ACTION_ID, EXPIRES, NOW, make_brief, make_evidence

Block = dict[str, Any]


def _types(blocks: list[Block]) -> list[str]:
    return [blk["type"] for blk in blocks]


def _buttons(blocks: list[Block]) -> list[Block]:
    return [el for blk in blocks if blk["type"] == "actions" for el in blk["elements"]]


def _all_text(blocks: list[Block]) -> str:
    return json.dumps(blocks)


# ---- Brief with live buttons (normal mode) ----


def test_brief_puts_verdict_and_decision_first() -> None:
    msg = b.build_brief(make_brief())
    types = _types(msg.blocks)
    assert types[0] == "header"
    header = msg.blocks[0]["text"]
    assert header["type"] == "plain_text"
    assert "INC-0042" in header["text"] and "Bad change" in header["text"]
    assert "82%" in header["text"]
    # Decision section, then the buttons, before any evidence detail.
    assert types[1] == "section" and "Decision needed" in msg.blocks[1]["text"]["text"]
    assert types[2] == "actions"
    assert "Bad change" in msg.text and "INC-0042" in msg.text  # notification fallback


def test_brief_buttons_carry_only_opaque_action_id() -> None:
    msg = b.build_brief(make_brief())
    buttons = _buttons(msg.blocks)
    assert [btn["action_id"] for btn in buttons] == [b.APPROVE_ACTION_ID, b.REJECT_ACTION_ID]
    assert all(btn["value"] == str(ACTION_ID) for btn in buttons)
    assert all(btn["text"]["type"] == "plain_text" for btn in buttons)
    approve, reject = buttons
    assert approve["style"] == "primary" and reject["style"] == "danger"
    assert "confirm" in approve  # a second tap guards against a 3 a.m. mis-tap
    # The round-tripped `value` carries no action parameters (the confirm dialog text is
    # display-only and is not returned in the interaction payload).
    assert all("9f3c2a1" not in btn["value"] and "boutique" not in btn["value"] for btn in buttons)
    assert "9f3c2a1" in approve["confirm"]["text"]["text"]  # the tap shows what it approves


def test_brief_shows_action_risk_ttl_pr_and_expiry() -> None:
    brief = make_brief()
    brief = brief.model_copy(
        update={"actions": [brief.actions[0].model_copy(update={"ttl_minutes": 60})]}
    )
    decision = b.build_brief(brief).blocks[1]["text"]["text"]
    assert "9f3c2a1" in decision and "risk: medium" in decision and "TTL: 60 min" in decision
    assert "<https://github.com/example/faultline-gitops/pull/12|PR>" in decision
    assert f"<!date^{int(EXPIRES.timestamp())}^{{time}}|" in decision


def test_brief_lists_hypotheses_with_evidence_ids_and_flip_conditions() -> None:
    text = _all_text(b.build_brief(make_brief()).blocks)
    for token in ("bad_change", "0.82", "chg-2", "hlt-4", "against", "trf-1", "sec-0"):
        assert token in text
    assert "Attack alerts on checkout appear" in text
    assert "HighErrorRate" in text and "checkoutservice" in text and "boutique" in text


def test_brief_points_to_evidence_in_thread() -> None:
    msg = b.build_brief(make_brief())
    assert msg.blocks[-1]["type"] == "context"
    assert "thread" in msg.blocks[-1]["elements"][0]["text"].lower()


# ---- Brief only: shadow mode, page_team, no action ----


def test_shadow_mode_has_no_buttons_and_is_marked() -> None:
    msg = b.build_brief(make_brief(mode="shadow"))
    assert "actions" not in _types(msg.blocks)
    decision = msg.blocks[1]["text"]["text"]
    assert "Shadow mode" in decision and "no action will be taken" in decision
    assert "9f3c2a1" in decision  # the proposal is still visible
    assert "<!subteam^S0SRE>" in _all_text(msg.blocks)
    assert "shadow" in msg.text.lower()


def test_no_action_brief_mentions_team_and_has_no_buttons() -> None:
    msg = b.build_brief(make_brief(with_action=False))
    assert "actions" not in _types(msg.blocks)
    assert "No automated action" in msg.blocks[1]["text"]["text"]
    assert "<!subteam^S0SRE>" in msg.blocks[1]["text"]["text"]


# ---- Decided states ----


@pytest.mark.parametrize(
    ("state", "needle", "word"),
    [
        (Approved(by="U0APPROVER", at=NOW), "Approved by <@U0APPROVER>", "approved"),
        (Rejected(by="U0APPROVER", reason="wrong commit", at=NOW), "wrong commit", "rejected"),
        (Expired(at=NOW), "expired", "expired"),
        (Superseded(note="a newer brief replaced this one", at=NOW), "Superseded", "superseded"),
        (ActionResult(ok=True, summary="PR #12 merged; synced", at=NOW), "merged", "applied"),
        (ActionResult(ok=False, summary="sync failed", at=NOW), "failed", "failed"),
    ],
)
def test_decided_brief_removes_buttons_and_states_decision(
    state: object, needle: str, word: str
) -> None:
    msg = b.build_decided_brief(make_brief(), state)  # type: ignore[arg-type]
    assert "actions" not in _types(msg.blocks)
    assert msg.blocks[0]["type"] == "header"
    assert needle in msg.blocks[1]["text"]["text"]
    assert word in msg.text.lower()  # the phone notification carries the outcome
    # Evidence and hypotheses remain readable after the decision.
    assert "chg-2" in _all_text(msg.blocks)


def test_rejected_state_escapes_reason() -> None:
    msg = b.build_decided_brief(make_brief(), Rejected(by="U1", reason="<b>&", at=NOW))
    assert "&lt;b&gt;&amp;" in msg.blocks[1]["text"]["text"]


# ---- Evidence thread ----


def test_evidence_thread_lists_every_item_escaped() -> None:
    msg = b.build_evidence_thread(make_brief())
    text = _all_text(msg.blocks)
    for item in make_evidence():
        assert item.id in text
    assert "&lt;summary &amp; detail&gt;" in text
    assert "<summary" not in text


def test_evidence_thread_respects_block_limit() -> None:
    brief = make_brief().model_copy(update={"evidence": make_evidence(5000)})
    msg = b.build_evidence_thread(brief)
    b.check_message_limits(msg.blocks)
    assert len(msg.blocks) <= b.MAX_BLOCKS_PER_MESSAGE
    assert "not shown" in _all_text(msg.blocks)


# ---- Reject modal ----


def test_reject_modal_structure() -> None:
    view = b.build_reject_modal(ACTION_ID, "INC-0042: revert 9f3c2a1")
    assert view["type"] == "modal"
    assert view["callback_id"] == b.REJECT_CALLBACK_ID
    assert view["private_metadata"] == str(ACTION_ID)
    assert len(view["title"]["text"]) <= 24 and len(view["submit"]["text"]) <= 24
    assert len(view["close"]["text"]) <= 24
    inputs = [blk for blk in view["blocks"] if blk["type"] == "input"]
    assert len(inputs) == 1
    assert inputs[0]["block_id"] == b.REJECT_REASON_BLOCK_ID
    assert inputs[0]["optional"] is False
    element = inputs[0]["element"]
    assert element["type"] == "plain_text_input"
    assert element["action_id"] == b.REJECT_REASON_ACTION_ID and element["multiline"] is True
    assert len(view["blocks"]) <= b.MAX_BLOCKS_PER_MODAL


# ---- Report and investigating messages ----


def test_report_message() -> None:
    report = Report(
        summary="Bad config change caused checkout errors.",
        timeline=["03:00 alert fired", "03:02 brief posted", "03:05 revert approved"],
        root_cause="Commit 9f3c2a1 changed the timeout.",
        impact="Checkout 5xx for 7 minutes.",
        action_taken="git_revert of 9f3c2a1.",
        follow_ups=["Add a canary", "Alert on config diffs"],
    )
    msg = b.build_report_message("INC-0042", report)
    assert msg.blocks[0]["type"] == "header" and "INC-0042" in msg.blocks[0]["text"]["text"]
    text = _all_text(msg.blocks)
    for needle in ("Root cause", "Impact", "Action taken", "Timeline", "Follow-ups", "canary"):
        assert needle in text
    b.check_message_limits(msg.blocks)


def test_investigating_message() -> None:
    msg = b.build_investigating("INC-0042", "HighErrorRate", "checkoutservice", "pending")
    text = _all_text(msg.blocks)
    assert "INC-0042" in text and "HighErrorRate" in text and "pending" in text
    assert "INC-0042" in msg.text


# ---- Limits and truncation ----


def test_clip_adds_ellipsis_within_limit() -> None:
    assert b.clip("short", 10) == "short"
    out = b.clip("x" * 100, 10)
    assert len(out) == 10 and out.endswith("…")


def test_escape_only_three_characters() -> None:
    assert b.escape("a & b < c > d 'e'") == "a &amp; b &lt; c &gt; d 'e'"


def test_long_fields_are_truncated_to_slack_limits() -> None:
    brief = make_brief()
    long_verdict = brief.verdict.model_copy(
        update={
            "explanation": "e" * 800,
            "flip_conditions": ["f" * 300] * 5,
        }
    )
    long_actions = [brief.actions[0].model_copy(update={"reason": "r" * 300})]
    brief = brief.model_copy(
        update={
            "verdict": long_verdict,
            "actions": long_actions,
            "display_id": "I" * 200,
            "alert_name": "A" * 500,
        }
    )
    msg = b.build_brief(brief)
    b.check_message_limits(msg.blocks)
    assert len(msg.blocks[0]["text"]["text"]) <= b.HEADER_TEXT_MAX
    for blk in msg.blocks:
        if blk["type"] == "section" and "text" in blk:
            assert len(blk["text"]["text"]) <= b.SECTION_TEXT_MAX
        if blk["type"] == "context":
            assert len(blk["elements"]) <= b.CONTEXT_ELEMENTS_MAX
            for el in blk["elements"]:
                assert len(el["text"]) <= b.CONTEXT_TEXT_MAX


def test_check_message_limits_rejects_too_many_blocks() -> None:
    with pytest.raises(ValueError, match="blocks"):
        b.check_message_limits([{"type": "divider"}] * 51)


def test_check_message_limits_rejects_long_button_value() -> None:
    bad = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "ok"},
                "action_id": "x",
                "value": "v" * 2001,
            }
        ],
    }
    with pytest.raises(ValueError, match="value"):
        b.check_message_limits([bad])


def test_slack_date_uses_epoch_and_fallback() -> None:
    dt = datetime(2026, 11, 15, 3, 17, tzinfo=UTC)
    out = b.slack_date(dt)
    assert out.startswith(f"<!date^{int(dt.timestamp())}^{{time}}|") and out.endswith("UTC>")
