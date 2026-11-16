"""Slack Bolt wiring (async Socket Mode): ack within 3 s, then act through ApprovalService.

Imports verified against the installed slack-bolt 1.30.0 / slack-sdk 3.47.0:
`slack_bolt.async_app.AsyncApp`, `slack_bolt.adapter.socket_mode.async_handler
.AsyncSocketModeHandler`. Payload paths follow the docs.slack.dev block_actions and
view_submission references.
"""

from typing import Any

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient

from faultline.slack import blocks
from faultline.slack.approvals import ApprovalService, Outcome, PendingApproval

Body = dict[str, Any]


def _clicked_value(body: Body) -> str:
    actions = body.get("actions") or [{}]
    return str(actions[0].get("value", ""))


def _user_id(body: Body) -> str:
    return str(body.get("user", {}).get("id", ""))


def _submitted_reason(body: Body) -> str:
    values = body.get("view", {}).get("state", {}).get("values", {})
    field = values.get(blocks.REJECT_REASON_BLOCK_ID, {}).get(blocks.REJECT_REASON_ACTION_ID, {})
    return str(field.get("value") or "")


def _modal_summary(pending: PendingApproval) -> str:
    what = "; ".join(blocks.describe_target(a.kind, a.target) for a in pending.brief.actions)
    return f"{pending.brief.display_id}: {what}".replace("`", "")


class SlackHandlers:
    """Thin listeners; all decisions live in ApprovalService."""

    def __init__(self, service: ApprovalService) -> None:
        self._service = service

    async def on_approve(self, ack: AsyncAck, body: Body, client: AsyncWebClient) -> None:
        await ack()
        outcome = await self._service.approve(_clicked_value(body), _user_id(body))
        await self._apply(client, outcome, _user_id(body), body.get("channel", {}).get("id"))

    async def on_reject(self, ack: AsyncAck, body: Body, client: AsyncWebClient) -> None:
        await ack()
        user = _user_id(body)
        outcome = await self._service.can_reject(_clicked_value(body), user)
        if not outcome.accepted or outcome.pending is None:
            await self._apply(client, outcome, user, body.get("channel", {}).get("id"))
            return
        view = blocks.build_reject_modal(outcome.pending.action_id, _modal_summary(outcome.pending))
        await client.views_open(trigger_id=str(body.get("trigger_id", "")), view=view)

    async def on_reject_submit(self, ack: AsyncAck, body: Body, client: AsyncWebClient) -> None:
        reason = _submitted_reason(body)
        if not reason.strip():
            await ack(
                response_action="errors",
                errors={blocks.REJECT_REASON_BLOCK_ID: "A reason is required."},
            )
            return
        await ack()
        value = str(body.get("view", {}).get("private_metadata", ""))
        outcome = await self._service.reject(value, _user_id(body), reason)
        await self._apply(client, outcome, _user_id(body), None)

    async def _apply(
        self, client: AsyncWebClient, outcome: Outcome, user: str, channel: str | None
    ) -> None:
        pending = outcome.pending
        if outcome.update is not None and pending is not None:
            await client.chat_update(
                channel=pending.channel,
                ts=pending.message_ts,
                text=outcome.update.text,
                blocks=outcome.update.blocks,
            )
        target = pending.channel if pending is not None else channel
        if outcome.user_message and target:
            await client.chat_postEphemeral(channel=target, user=user, text=outcome.user_message)


def register(app: AsyncApp, handlers: SlackHandlers) -> None:
    app.action(blocks.APPROVE_ACTION_ID)(handlers.on_approve)
    app.action(blocks.REJECT_ACTION_ID)(handlers.on_reject)
    app.view(blocks.REJECT_CALLBACK_ID)(handlers.on_reject_submit)


def build_app(
    handlers: SlackHandlers, *, token: str | None = None, client: AsyncWebClient | None = None
) -> AsyncApp:
    """Bolt app for Socket Mode; pass `token` (bot token) in production, `client` in tests."""
    app = AsyncApp(token=token, client=client)
    register(app, handlers)
    return app


async def run_socket_mode(app: AsyncApp, app_token: str) -> None:
    """Block on the Socket Mode connection (app-level token with connections:write)."""
    handler = AsyncSocketModeHandler(app, app_token)
    await handler.start_async()  # type: ignore[no-untyped-call]  # Bolt leaves this untyped
