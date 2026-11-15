"""OPA client: request shape, response parsing and fail-closed behaviour (HTTP mocked)."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from faultline.config import ActionSettings
from faultline.policy.opa import (
    OpaClient,
    OpaError,
    PolicyInput,
    build_policy_input,
    prefix_length_v4,
)
from faultline.schemas import ActionTarget, ProposedAction

OPA = "http://opa.test:8181"
DECISION_URL = f"{OPA}/v1/data/faultline/actions"

LIMITS = ActionSettings(
    max_per_incident=2,
    block_min_confidence=0.75,
    block_min_prefix_v4=24,
    block_ttl_minutes=60,
    rate_limit_ttl_minutes=60,
    scale_max_replicas=5,
)


def _block_input() -> PolicyInput:
    action = ProposedAction(
        kind="block_source",
        target=ActionTarget(cidr="172.18.5.0/24", namespace="ingress"),
        reason="flood from one range",
    )
    return build_policy_input(
        action=action,
        verdict="attack",
        contributing_factor=None,
        confidence=0.9,
        actions_executed=0,
        change_on_path=False,
        limits=LIMITS,
    )


def test_prefix_length_v4() -> None:
    assert prefix_length_v4("172.18.5.0/24") == 24
    assert prefix_length_v4("10.0.0.1") == 32


def test_prefix_length_rejects_ipv6() -> None:
    with pytest.raises(ValueError):
        prefix_length_v4("2001:db8::/48")


def test_build_input_computes_prefix_and_limits() -> None:
    body = _block_input().to_body()["input"]
    assert isinstance(body, dict)
    assert body["action"]["target"]["prefix_length"] == 24
    assert body["action"]["target"]["cidr"] == "172.18.5.0/24"
    assert body["limits"] == {
        "max_per_incident": 2,
        "block_min_confidence": 0.75,
        "block_min_prefix_v4": 24,
        "scale_max_replicas": 5,
    }
    assert body["incident"] == {"actions_executed": 0, "change_on_path": False}
    assert body["verdict"]["contributing_factor"] is None
    assert "approver" not in body


@respx.mock
async def test_evaluate_allow() -> None:
    route = respx.post(DECISION_URL).mock(
        return_value=httpx.Response(200, json={"result": {"allow": True, "deny": []}})
    )
    decision = await OpaClient(OPA).evaluate(_block_input())
    assert decision.allow is True
    assert decision.deny == []
    assert decision.approver_allowed is False  # missing means false
    sent = json.loads(route.calls[0].request.content)
    assert set(sent) == {"input"}
    assert sent["input"]["action"]["kind"] == "block_source"


@respx.mock
async def test_evaluate_deny_maps_to_policy_decision() -> None:
    respx.post(DECISION_URL).mock(
        return_value=httpx.Response(
            200,
            json={"result": {"allow": False, "deny": ["target namespace is protected"]}},
        )
    )
    decision = await OpaClient(OPA).evaluate(_block_input())
    pd = decision.to_policy_decision()
    assert pd.allow is False
    assert pd.deny_reasons == ["target namespace is protected"]


@respx.mock
async def test_approver_allowed_when_present() -> None:
    respx.post(DECISION_URL).mock(
        return_value=httpx.Response(
            200, json={"result": {"allow": True, "deny": [], "approver_allowed": True}}
        )
    )
    inp = _block_input().with_approver(["soc"])
    decision = await OpaClient(OPA).evaluate(inp)
    assert decision.approver_allowed is True


@respx.mock
async def test_missing_result_fails_closed() -> None:
    respx.post(DECISION_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(OpaError):
        await OpaClient(OPA).evaluate(_block_input())


@respx.mock
async def test_http_error_fails_closed() -> None:
    respx.post(DECISION_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(OpaError):
        await OpaClient(OPA).evaluate(_block_input())


@respx.mock
async def test_connection_error_fails_closed() -> None:
    respx.post(DECISION_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(OpaError):
        await OpaClient(OPA).evaluate(_block_input())


def _opa_bin() -> str | None:
    local = Path.home() / ".local/bin/opa"
    return shutil.which("opa") or (str(local) if local.exists() else None)


def _decide(inp: PolicyInput) -> dict[str, object]:
    out = subprocess.run(
        [str(_opa_bin()), "eval", "-d", "policies", "-I", "--format", "json",
         "data.faultline.actions"],
        input=json.dumps(inp.to_body()["input"]),
        capture_output=True, text=True, check=True,
    )
    value: dict[str, object] = json.loads(out.stdout)["result"][0]["expressions"][0]["value"]
    return value


@pytest.mark.skipif(_opa_bin() is None, reason="OPA CLI not installed")
def test_python_input_matches_rego_contract() -> None:
    """The input Faultline builds is the shape policies/actions.rego expects."""
    ok = _decide(_block_input().with_approver(["soc"]))
    assert ok["allow"] is True
    assert ok.get("approver_allowed") is True

    base = _block_input()
    wide_target = base.action.target.model_copy(update={"prefix_length": 12})
    wide_action = base.action.model_copy(update={"target": wide_target})
    denied = _decide(base.model_copy(update={"action": wide_action}).with_approver(["sre"]))
    assert denied["allow"] is False
    assert "approver_allowed" not in denied
