"""Pure builders: Faultline messages -> Block Kit JSON (BUILD_SPEC: Messages).

Layout is for an on-call engineer on a phone: header carries the verdict, the next block
says what decision is needed and by when, then the buttons; evidence detail goes to the
thread. Limits below come from docs.slack.dev (blocks: 50 per message, 100 per modal;
button text 75 / value 2000) and the installed slack_sdk block models (header 150,
section text 3000, fields 10 x 2000, context 10 elements).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from faultline.schemas import ActionTarget, Report
from faultline.slack.models import (
    ActionResult,
    Approved,
    BriefAction,
    DecisionState,
    Expired,
    IncidentBrief,
    Rejected,
    Superseded,
)

Block = dict[str, Any]

MAX_BLOCKS_PER_MESSAGE = 50
MAX_BLOCKS_PER_MODAL = 100
HEADER_TEXT_MAX = 150
SECTION_TEXT_MAX = 3000
FIELD_TEXT_MAX = 2000
FIELDS_MAX = 10
CONTEXT_ELEMENTS_MAX = 10
CONTEXT_TEXT_MAX = 3000
BUTTON_TEXT_MAX = 75
BUTTON_VALUE_MAX = 2000
MODAL_TITLE_MAX = 24
INPUT_LABEL_MAX = 2000

APPROVE_ACTION_ID = "approve_action"
REJECT_ACTION_ID = "reject_action"
REJECT_CALLBACK_ID = "reject_reason"
REJECT_REASON_BLOCK_ID = "reject_reason_block"
REJECT_REASON_ACTION_ID = "reject_reason_input"
DECISION_BLOCK_ID = "decision"

VERDICT_LABEL = {
    "attack": "Attack",
    "network_fault": "Network fault",
    "bad_change": "Bad change",
    "capacity": "Capacity",
    "unknown": "Unknown",
}


class Message(BaseModel):
    """What to send: `text` is the notification fallback, `blocks` the layout."""

    text: str
    blocks: list[Block]


# ---- text helpers ----


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def escape(text: str) -> str:
    """Only `&`, `<` and `>` need escaping in Slack text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_date(at: datetime) -> str:
    """Local time for every viewer, with a UTC fallback."""
    ts = int(at.timestamp())
    fallback = at.astimezone(UTC).strftime("%H:%M UTC")
    return f"<!date^{ts}^{{time}}|{fallback}>"


def _plain(text: str, limit: int) -> dict[str, Any]:
    return {"type": "plain_text", "text": clip(text, limit)}


def _mrkdwn(text: str, limit: int = SECTION_TEXT_MAX) -> dict[str, Any]:
    return {"type": "mrkdwn", "text": clip(text, limit)}


def _header(text: str) -> Block:
    return {"type": "header", "text": _plain(text, HEADER_TEXT_MAX)}


def _section(text: str, block_id: str | None = None) -> Block:
    block: Block = {"type": "section", "text": _mrkdwn(text)}
    if block_id:
        block["block_id"] = block_id
    return block


def _fields(pairs: list[tuple[str, str]]) -> Block:
    fields = [_mrkdwn(f"*{k}*\n{escape(v)}", FIELD_TEXT_MAX) for k, v in pairs[:FIELDS_MAX]]
    return {"type": "section", "fields": fields}


def _context(*texts: str) -> Block:
    elements = [_mrkdwn(t, CONTEXT_TEXT_MAX) for t in texts[:CONTEXT_ELEMENTS_MAX]]
    return {"type": "context", "elements": elements}


def _button(text: str, action_id: str, value: str, style: str, confirm: Block | None) -> Block:
    button: Block = {
        "type": "button",
        "text": _plain(text, BUTTON_TEXT_MAX),
        "action_id": action_id,
        "value": value,
        "style": style,
    }
    if confirm:
        button["confirm"] = confirm
    return button


# ---- action wording ----


def describe_target(kind: str, target: ActionTarget) -> str:
    """One short clause saying what the action does to what."""
    ns = f" in `{target.namespace}`" if target.namespace else ""
    if kind == "git_revert":
        return f"Revert commit `{target.commit_sha}`{ns}"
    if kind == "rate_limit":
        return f"Rate-limit route `{target.route}`{ns}"
    if kind == "block_source":
        return f"Block source range `{target.cidr}`{ns}"
    if kind == "quarantine_pod":
        return f"Quarantine workload `{target.workload}`{ns}"
    if kind == "scale_out":
        return f"Scale `{target.workload}` to {target.replicas} replicas{ns}"
    return f"Page team `{target.team}`"


def _action_line(action: BriefAction) -> str:
    parts = [describe_target(action.kind, action.target)]
    if action.pr_url:
        parts.append(f"<{action.pr_url}|PR>")
    parts.append(f"risk: {action.risk}")
    parts.append(f"TTL: {action.ttl_minutes} min" if action.ttl_minutes else "TTL: none")
    return " · ".join(parts)


def _decision_text(brief: IncidentBrief) -> str:
    team = brief.team_mention or "owning team"
    if not brief.actions:
        return f"*No automated action.* {team} please take over."
    lines = [_action_line(a) + f"\n_{escape(a.reason)}_" for a in brief.actions]
    if brief.mode == "shadow":
        head = "*Shadow mode: no action will be taken.* Faultline would propose:"
        return "\n".join([head, *lines, f"{team} for awareness."])
    if not brief.approval_open:
        return "\n".join(["*No approval requested.*", *lines, f"{team} please take over."])
    deadline = f" by {slack_date(brief.expires_at)}" if brief.expires_at else ""
    return "\n".join([f"*Decision needed{deadline}.* Approve to:", *lines])


def _approve_confirm(brief: IncidentBrief) -> Block:
    what = "; ".join(describe_target(a.kind, a.target) for a in brief.actions)
    return {
        "title": _plain("Approve this action?", 100),
        "text": _mrkdwn(what, 300),
        "confirm": _plain("Approve", 30),
        "deny": _plain("Back", 30),
        "style": "primary",
    }


def _buttons(brief: IncidentBrief) -> Block:
    value = str(brief.actions[0].action_id)
    return {
        "type": "actions",
        "block_id": DECISION_BLOCK_ID,
        "elements": [
            _button("Approve", APPROVE_ACTION_ID, value, "primary", _approve_confirm(brief)),
            _button("Reject", REJECT_ACTION_ID, value, "danger", None),
        ],
    }


# ---- evidence wording ----


def _hypotheses_text(brief: IncidentBrief) -> str:
    rows = []
    for h in sorted(brief.verdict.hypotheses, key=lambda h: h.score, reverse=True):
        row = f"• `{h.type}` {h.score:.2f}"
        if h.for_:
            row += " · for: " + ", ".join(h.for_)
        if h.against:
            row += " · against: " + ", ".join(h.against)
        rows.append(row)
    if brief.verdict.contributing_factor:
        rows.append(f"Contributing factor: `{brief.verdict.contributing_factor}`")
    return "*Hypotheses*\n" + "\n".join(rows)


def _body_blocks(brief: IncidentBrief) -> list[Block]:
    """Everything below the decision: explanation, facts, hypotheses, flip conditions."""
    blocks = [
        _section(escape(brief.verdict.explanation)),
        _fields(
            [
                ("Alert", brief.alert_name),
                ("Service", f"{brief.service} ({brief.namespace})"),
                ("Severity", brief.severity),
                ("Mode", brief.mode),
            ]
        ),
        _section(_hypotheses_text(brief)),
    ]
    if brief.verdict.flip_conditions:
        flips = "; ".join(escape(f) for f in brief.verdict.flip_conditions)
        blocks.append(_context(f"Would flip if: {flips}"))
    blocks.append(_context(f"Evidence details ({len(brief.evidence)} items) are in this thread."))
    return blocks


def _headline(brief: IncidentBrief) -> str:
    label = VERDICT_LABEL[brief.verdict.verdict]
    return f"{brief.display_id}: {label}, {brief.verdict.confidence:.0%} confidence"


# ---- public builders ----


def build_investigating(display_id: str, alert_name: str, service: str, severity: str) -> Message:
    text = f"{display_id}: investigating {alert_name} on {service}"
    blocks = [
        _header(f"{display_id}: Investigating"),
        _fields([("Alert", alert_name), ("Service", service), ("Severity", severity)]),
    ]
    check_message_limits(blocks)
    return Message(text=text, blocks=blocks)


def build_brief(brief: IncidentBrief) -> Message:
    blocks = [_header(_headline(brief)), _section(_decision_text(brief), "decision_text")]
    if brief.approval_open:
        blocks.append(_buttons(brief))
    blocks.extend(_body_blocks(brief))
    check_message_limits(blocks)
    if brief.approval_open:
        tail = "decision needed"
    else:
        tail = "shadow mode" if brief.mode == "shadow" else "no action"
    return Message(text=f"{_headline(brief)} ({tail})", blocks=blocks)


def _state_text(state: DecisionState) -> tuple[str, str]:
    """(mrkdwn decision line, short fallback word)."""
    if isinstance(state, Approved):
        return f"*Approved by <@{state.by}>* at {slack_date(state.at)}.", "approved"
    if isinstance(state, Rejected):
        line = f"*Rejected by <@{state.by}>* at {slack_date(state.at)}: {escape(state.reason)}"
        return line, "rejected"
    if isinstance(state, Expired):
        return f"*Approval expired* at {slack_date(state.at)}; no action was taken.", "expired"
    if isinstance(state, Superseded):
        return f"*Superseded* at {slack_date(state.at)}: {escape(state.note)}", "superseded"
    if isinstance(state, ActionResult):
        word = "Action applied" if state.ok else "Action failed"
        return f"*{word}* at {slack_date(state.at)}: {escape(state.summary)}", word.lower()
    raise TypeError(f"unknown decision state: {state!r}")


def build_decided_brief(brief: IncidentBrief, state: DecisionState) -> Message:
    """The brief after a decision: buttons removed, the outcome where the decision was."""
    line, word = _state_text(state)
    proposal = "\n".join(_action_line(a) for a in brief.actions)
    decision = f"{line}\n{proposal}" if proposal else line
    blocks = [_header(_headline(brief)), _section(decision, "decision_text"), *_body_blocks(brief)]
    check_message_limits(blocks)
    return Message(text=f"{_headline(brief)} ({word})", blocks=blocks)


def _chunk_lines(lines: list[str], limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = clip(line, limit)
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def build_evidence_thread(brief: IncidentBrief) -> Message:
    """Thread reply listing every evidence item by ID; capped at the message block limit."""
    blocks: list[Block] = [_header(f"{brief.display_id}: Evidence")]
    budget = MAX_BLOCKS_PER_MESSAGE - 2  # header + a possible "not shown" context
    shown = 0
    for pack in ("change", "traffic", "security", "health"):
        items = [i for i in brief.evidence if i.pack == pack]
        if not items or len(blocks) - 1 >= budget:
            continue
        lines = [f"`{i.id}` {escape(i.summary)}" for i in items]
        for chunk in _chunk_lines([f"*{pack.title()}*", *lines], SECTION_TEXT_MAX):
            if len(blocks) - 1 >= budget:
                break
            blocks.append(_section(chunk))
            shown += sum(1 for line in chunk.split("\n") if line.startswith("`"))
    missing = len(brief.evidence) - shown
    if missing > 0:
        blocks.append(_context(f"{missing} more items not shown; see the evidence pack."))
    check_message_limits(blocks)
    return Message(
        text=f"{brief.display_id}: evidence ({len(brief.evidence)} items)", blocks=blocks
    )


def build_reject_modal(action_id: UUID, summary: str) -> dict[str, Any]:
    """`views.open` payload; `private_metadata` carries the opaque action id only."""
    view: dict[str, Any] = {
        "type": "modal",
        "callback_id": REJECT_CALLBACK_ID,
        "private_metadata": str(action_id),
        "title": _plain("Reject action", MODAL_TITLE_MAX),
        "submit": _plain("Reject", MODAL_TITLE_MAX),
        "close": _plain("Cancel", MODAL_TITLE_MAX),
        "blocks": [
            _section(escape(summary)),
            {
                "type": "input",
                "block_id": REJECT_REASON_BLOCK_ID,
                "optional": False,
                "label": _plain("Why is this action wrong?", INPUT_LABEL_MAX),
                "element": {
                    "type": "plain_text_input",
                    "action_id": REJECT_REASON_ACTION_ID,
                    "multiline": True,
                    "max_length": 300,
                    "placeholder": _plain("Required; recorded in the audit log", 150),
                },
            },
        ],
    }
    return view


def build_report_message(display_id: str, report: Report) -> Message:
    timeline = "\n".join(f"• {escape(t)}" for t in report.timeline) or "—"
    follow_ups = "\n".join(f"• {escape(f)}" for f in report.follow_ups) or "—"
    blocks = [
        _header(f"{display_id}: Postmortem draft"),
        _section(escape(report.summary)),
        _section(f"*Root cause*\n{escape(report.root_cause)}"),
        _section(f"*Impact*\n{escape(report.impact)}"),
        _section(f"*Action taken*\n{escape(report.action_taken)}"),
        _section(f"*Timeline*\n{timeline}"),
        _section(f"*Follow-ups*\n{follow_ups}"),
    ]
    check_message_limits(blocks)
    return Message(text=f"{display_id}: postmortem draft", blocks=blocks)


# ---- limits ----


def _check_text(obj: dict[str, Any], limit: int, what: str) -> None:
    if len(obj.get("text", "")) > limit:
        raise ValueError(f"{what} text exceeds {limit} characters")


def _check_block(block: Block) -> None:
    kind = block["type"]
    if kind == "header":
        _check_text(block["text"], HEADER_TEXT_MAX, "header")
    elif kind == "section":
        if "text" in block:
            _check_text(block["text"], SECTION_TEXT_MAX, "section")
        fields = block.get("fields", [])
        if len(fields) > FIELDS_MAX:
            raise ValueError(f"section has more than {FIELDS_MAX} fields")
        for f in fields:
            _check_text(f, FIELD_TEXT_MAX, "field")
    elif kind == "context":
        if len(block["elements"]) > CONTEXT_ELEMENTS_MAX:
            raise ValueError(f"context has more than {CONTEXT_ELEMENTS_MAX} elements")
    elif kind == "actions":
        for el in block["elements"]:
            _check_text(el["text"], BUTTON_TEXT_MAX, "button")
            if len(el.get("value", "")) > BUTTON_VALUE_MAX:
                raise ValueError(f"button value exceeds {BUTTON_VALUE_MAX} characters")


def check_message_limits(blocks: list[Block], max_blocks: int = MAX_BLOCKS_PER_MESSAGE) -> None:
    """Raise ValueError when a payload would be rejected by Slack."""
    if len(blocks) > max_blocks:
        raise ValueError(f"too many blocks: {len(blocks)} > {max_blocks}")
    for block in blocks:
        _check_block(block)
