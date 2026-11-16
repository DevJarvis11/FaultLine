"""Bolt handlers: ack first, then approve / reject modal / view submission with fake client."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from faultline.slack import blocks as b
from faultline.slack.app import SlackHandlers, build_app
from faultline.slack.approvals import ApprovalService, PendingApproval
from tests.slack_helpers import (
    ACTION_ID,
    APPROVER,
    APPROVERS,
    EXPIRES,
    NOW,
    STRANGER,
    FakeOpa,
    FakeResumer,
    FakeStore,
    make_pending,
)


class Order:
    def __init__(self) -> None:
        self.events: list[str] = []


class OrderedStore(FakeStore):
    def __init__(self, order: Order, *pending: PendingApproval) -> None:
        super().__init__(*pending)
        self.order = order

    async def get(self, action_id: UUID) -> PendingApproval | None:
        self.order.events.append("store.get")
        return await super().get(action_id)


class FakeAck:
    def __init__(self, order: Order) -> None:
        self.order = order
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> None:
        self.order.events.append("ack")
        self.calls.append(kwargs)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def views_open(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("views_open", kwargs))
        return {"ok": True}

    async def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat_update", kwargs))
        return {"ok": True}

    async def chat_postEphemeral(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.calls.append(("chat_postEphemeral", kwargs))
        return {"ok": True}

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def _click(action_id: str, user: str = APPROVER, value: str = str(ACTION_ID)) -> dict[str, Any]:
    # Shape per docs.slack.dev block_actions payload reference.
    return {
        "type": "block_actions",
        "user": {"id": user},
        "trigger_id": "12345.98765.abcd",
        "channel": {"id": "C0INCIDENTS"},
        "message": {"ts": "1700000000.000100"},
        "actions": [{"action_id": action_id, "block_id": "decision", "value": value}],
    }


def _submission(reason: str, user: str = APPROVER, meta: str = str(ACTION_ID)) -> dict[str, Any]:
    # Shape per docs.slack.dev view_submission payload reference.
    return {
        "type": "view_submission",
        "user": {"id": user},
        "view": {
            "callback_id": b.REJECT_CALLBACK_ID,
            "private_metadata": meta,
            "state": {
                "values": {
                    b.REJECT_REASON_BLOCK_ID: {
                        b.REJECT_REASON_ACTION_ID: {"type": "plain_text_input", "value": reason}
                    }
                }
            },
        },
    }


def _setup(at: object = NOW) -> tuple[SlackHandlers, Order, FakeResumer, FakeStore]:
    order = Order()
    store = OrderedStore(order, make_pending())
    resumer = FakeResumer()
    service = ApprovalService(
        store=store, resumer=resumer, policy=FakeOpa(), approvers=APPROVERS, now=lambda: at
    )
    return SlackHandlers(service), order, resumer, store


async def test_approve_acks_first_then_updates_brief_and_confirms() -> None:
    handlers, order, resumer, _ = _setup()
    ack, client = FakeAck(order), FakeClient()
    await handlers.on_approve(ack, _click(b.APPROVE_ACTION_ID), client)
    assert order.events[0] == "ack" and len(ack.calls) == 1
    assert client.names() == ["chat_update", "chat_postEphemeral"]
    update = client.calls[0][1]
    assert update["channel"] == "C0INCIDENTS" and update["ts"] == "1700000000.000100"
    assert "actions" not in [blk["type"] for blk in update["blocks"]]
    ephemeral = client.calls[1][1]
    assert ephemeral["user"] == APPROVER and ephemeral["channel"] == "C0INCIDENTS"
    assert len(resumer.calls) == 1


async def test_approve_by_stranger_posts_ephemeral_refusal_only() -> None:
    handlers, order, resumer, _ = _setup()
    client = FakeClient()
    await handlers.on_approve(FakeAck(order), _click(b.APPROVE_ACTION_ID, STRANGER), client)
    assert client.names() == ["chat_postEphemeral"]
    assert "not an approver" in client.calls[0][1]["text"]
    assert resumer.calls == []


async def test_double_click_second_is_ignored_with_ephemeral() -> None:
    handlers, order, resumer, _ = _setup()
    client = FakeClient()
    await handlers.on_approve(FakeAck(order), _click(b.APPROVE_ACTION_ID), client)
    await handlers.on_approve(FakeAck(order), _click(b.APPROVE_ACTION_ID), client)
    assert client.names() == ["chat_update", "chat_postEphemeral", "chat_postEphemeral"]
    assert len(resumer.calls) == 1


async def test_expired_click_updates_brief_to_expired() -> None:
    handlers, order, resumer, _ = _setup(at=EXPIRES + timedelta(minutes=1))
    client = FakeClient()
    await handlers.on_approve(FakeAck(order), _click(b.APPROVE_ACTION_ID), client)
    assert client.names() == ["chat_update", "chat_postEphemeral"]
    assert "expired" in str(client.calls[0][1]["blocks"]).lower()
    assert "expired" in client.calls[1][1]["text"].lower()
    assert resumer.calls == []


async def test_reject_click_opens_modal_with_trigger_id() -> None:
    handlers, order, resumer, _ = _setup()
    ack, client = FakeAck(order), FakeClient()
    await handlers.on_reject(ack, _click(b.REJECT_ACTION_ID), client)
    assert order.events[0] == "ack"
    assert client.names() == ["views_open"]
    call = client.calls[0][1]
    assert call["trigger_id"] == "12345.98765.abcd"
    assert call["view"]["callback_id"] == b.REJECT_CALLBACK_ID
    assert call["view"]["private_metadata"] == str(ACTION_ID)
    assert resumer.calls == []


async def test_reject_click_by_stranger_does_not_open_modal() -> None:
    handlers, order, _, _ = _setup()
    client = FakeClient()
    await handlers.on_reject(FakeAck(order), _click(b.REJECT_ACTION_ID, STRANGER), client)
    assert client.names() == ["chat_postEphemeral"]


async def test_reject_submission_records_reason_updates_brief() -> None:
    handlers, order, resumer, store = _setup()
    ack, client = FakeAck(order), FakeClient()
    await handlers.on_reject_submit(ack, _submission("wrong commit"), client)
    assert ack.calls == [{}]  # plain ack closes the modal
    assert client.names() == ["chat_update", "chat_postEphemeral"]
    assert "wrong commit" in str(client.calls[0][1]["blocks"])
    assert store.decisions[ACTION_ID].reason == "wrong commit"
    assert len(resumer.calls) == 1


async def test_reject_submission_with_blank_reason_returns_modal_error() -> None:
    handlers, order, resumer, store = _setup()
    ack, client = FakeAck(order), FakeClient()
    await handlers.on_reject_submit(ack, _submission("   "), client)
    assert ack.calls[0]["response_action"] == "errors"
    assert b.REJECT_REASON_BLOCK_ID in ack.calls[0]["errors"]
    assert client.calls == [] and resumer.calls == [] and store.decisions == {}


async def test_reject_submission_after_decision_is_ignored() -> None:
    handlers, order, resumer, _ = _setup()
    client = FakeClient()
    await handlers.on_approve(FakeAck(order), _click(b.APPROVE_ACTION_ID), client)
    await handlers.on_reject_submit(FakeAck(order), _submission("late"), client)
    assert client.names() == ["chat_update", "chat_postEphemeral", "chat_postEphemeral"]
    assert len(resumer.calls) == 1


def test_build_app_registers_listeners_without_network() -> None:
    handlers, _, _, _ = _setup()
    client = AsyncWebClient(token="xoxb-test")  # never used: no request is dispatched
    app = build_app(handlers, client=client)
    assert isinstance(app, AsyncApp)
    # Three listeners: approve, reject, reject_reason view.
    assert len(app._async_listeners) == 3
