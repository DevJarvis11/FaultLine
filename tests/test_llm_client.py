"""Model client against a mocked OpenAI-compatible gateway (no network)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from faultline.config import ModelsConfig, load_env, load_models
from faultline.llm.client import LlmError, ModelClient, TruncatedError
from faultline.llm.records import MemoryRecorder

BASE = "http://gw.test/v1"
MODELS = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> ModelsConfig:
    monkeypatch.setenv("LLM_BASE_URL", BASE)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    return load_models(MODELS, load_env(env_file=None))


def _chat(content: str | None = "ok", finish: str = "stop", tool_calls: Any = None,
          completion_tokens: int = 10) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content,
                           "reasoning_content": "private thoughts", "provider_specific_fields": {}}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={
        "model": "gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": finish, "message": msg}],
        "usage": {"prompt_tokens": 50, "completion_tokens": completion_tokens, "total_tokens": 60},
    })


def _client(cfg: ModelsConfig, rec: MemoryRecorder, **kw: Any) -> ModelClient:
    return ModelClient(cfg, rec, backoff_s=0, **kw)


@respx.mock
async def test_chat_sends_role_settings_and_auth(cfg: ModelsConfig) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=_chat())
    rec = MemoryRecorder()
    res = await _client(cfg, rec).chat("investigator", [{"role": "user", "content": "hi"}])
    assert res.content == "ok"
    assert res.finish_reason == "stop"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer test-key"
    body = json.loads(req.content)
    assert body["model"] == cfg.roles.investigator.model
    assert body["max_tokens"] == cfg.roles.investigator.max_tokens
    assert body["temperature"] == cfg.roles.investigator.temperature
    assert body["reasoning_effort"] == "medium"


@respx.mock
async def test_reasoning_effort_omitted_when_gateway_ignores_it(cfg: ModelsConfig) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=_chat())
    await _client(cfg, MemoryRecorder(), use_reasoning_effort=False).chat(
        "triage", [{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_messages_redacted_before_sending_and_storing(cfg: ModelsConfig) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=_chat())
    rec = MemoryRecorder()
    await _client(cfg, rec).chat(
        "triage", [{"role": "user", "content": "user bob@example.com password=hunter2"}])
    sent = route.calls[0].request.content.decode()
    assert "bob@example.com" not in sent and "hunter2" not in sent
    stored = json.dumps(rec.records[0].request)
    assert "bob@example.com" not in stored and "hunter2" not in stored


@respx.mock
async def test_record_written_without_reasoning_text(cfg: ModelsConfig) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=_chat())
    rec = MemoryRecorder()
    await _client(cfg, rec).chat("report", [{"role": "user", "content": "hi"}],
                                 prompt_version="report-v1")
    r = rec.records[0]
    assert (r.role, r.status, r.model, r.prompt_version) == ("report", "ok", "gpt-oss-120b",
                                                             "report-v1")
    assert (r.input_tokens, r.output_tokens) == (50, 10)
    assert r.latency_ms is not None and r.latency_ms >= 0
    assert r.incident_id is None
    assert "private thoughts" not in json.dumps(r.response)
    assert r.response is not None and r.response["content"] == "ok"


@respx.mock
async def test_finish_reason_length_retries_once_with_higher_cap(cfg: ModelsConfig) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[_chat(None, finish="length"), _chat("done")])
    rec = MemoryRecorder()
    res = await _client(cfg, rec).chat("triage", [{"role": "user", "content": "hi"}])
    assert res.content == "done"
    caps = [json.loads(c.request.content)["max_tokens"] for c in route.calls]
    assert caps == [cfg.roles.triage.max_tokens, cfg.roles.triage.max_tokens * 2]
    assert [r.status for r in rec.records] == ["error", "ok"]
    assert rec.records[0].error == "finish_reason=length"


@respx.mock
async def test_finish_reason_length_twice_escalates(cfg: ModelsConfig) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=_chat(None, finish="length"))
    rec = MemoryRecorder()
    with pytest.raises(TruncatedError):
        await _client(cfg, rec).chat("triage", [{"role": "user", "content": "hi"}])
    assert route.call_count == 2
    assert [r.status for r in rec.records] == ["error", "error"]


@respx.mock
async def test_transient_errors_retried_then_ok(cfg: ModelsConfig) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[httpx.Response(429), httpx.ReadTimeout("slow"), _chat()])
    rec = MemoryRecorder()
    res = await _client(cfg, rec).chat("triage", [{"role": "user", "content": "hi"}])
    assert res.content == "ok"
    assert route.call_count == 3  # max_retries = 2
    assert [r.status for r in rec.records] == ["ok"]


@respx.mock
async def test_timeouts_exhaust_retries(cfg: ModelsConfig) -> None:
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    rec = MemoryRecorder()
    with pytest.raises(LlmError):
        await _client(cfg, rec).chat("triage", [{"role": "user", "content": "hi"}])
    assert [r.status for r in rec.records] == ["timeout"]


@respx.mock
async def test_client_error_not_retried(cfg: ModelsConfig) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "bad"}))
    rec = MemoryRecorder()
    with pytest.raises(LlmError):
        await _client(cfg, rec).chat("triage", [{"role": "user", "content": "hi"}])
    assert route.call_count == 1
    assert rec.records[0].status == "error"


@respx.mock
async def test_tool_calls_parsed(cfg: ModelsConfig) -> None:
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "get_service_health", "arguments": "{\"service\": \"x\"}"}}]
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=_chat(None, finish="tool_calls", tool_calls=calls))
    tools = [{"type": "function", "function": {"name": "get_service_health", "parameters": {}}}]
    res = await _client(cfg, MemoryRecorder()).chat(
        "investigator", [{"role": "user", "content": "go"}], tools=tools, tool_choice="auto")
    assert res.tool_calls[0].name == "get_service_health"
    assert res.tool_calls[0].arguments == "{\"service\": \"x\"}"
    body = json.loads(route.calls[0].request.content)
    assert body["tools"] == tools and body["tool_choice"] == "auto"


@respx.mock
async def test_embed_checks_dimension(cfg: ModelsConfig) -> None:
    respx.post(f"{BASE}/embeddings").mock(return_value=httpx.Response(200, json={
        "data": [{"index": 0, "embedding": [0.1] * 2560}],
        "usage": {"prompt_tokens": 2, "total_tokens": 2}}))
    rec = MemoryRecorder()
    vecs = await _client(cfg, rec).embed(["hello"])
    assert len(vecs[0]) == 2560
    assert rec.records[0].role == "embedding"
    assert "0.1" not in json.dumps(rec.records[0].response)  # vectors are not stored


@respx.mock
async def test_embed_wrong_dimension_fails(cfg: ModelsConfig) -> None:
    respx.post(f"{BASE}/embeddings").mock(return_value=httpx.Response(200, json={
        "data": [{"index": 0, "embedding": [0.1] * 8}]}))
    with pytest.raises(LlmError, match="dimension"):
        await _client(cfg, MemoryRecorder()).embed(["hello"])


@respx.mock
async def test_list_models(cfg: ModelsConfig) -> None:
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "gpt-oss-120b"}, {"id": "qwen3-embed"}]}))
    assert await _client(cfg, MemoryRecorder()).list_models() == ["gpt-oss-120b", "qwen3-embed"]
