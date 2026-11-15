"""Startup model check against a mocked gateway."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from faultline.config import ModelsConfig, load_env, load_models
from faultline.llm.check import CheckReport, SafeRecorder, run_model_check
from faultline.llm.client import ModelClient
from faultline.llm.records import LlmCallRecord, MemoryRecorder

BASE = "http://gw.test/v1"
MODELS = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> ModelsConfig:
    monkeypatch.setenv("LLM_BASE_URL", BASE)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    return load_models(MODELS, load_env(env_file=None))


class Gateway:
    """Answers like the live gateway; knobs switch individual capabilities off."""

    def __init__(self, *, tools: bool = True, schema: bool = True, effort: bool = True,
                 models: tuple[str, ...] = ("gpt-oss-120b", "qwen3-embed"), dim: int = 2560):
        self.tools, self.schema, self.effort, self.models, self.dim = (
            tools, schema, effort, models, dim)

    def chat(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        msg: dict[str, Any] = {"role": "assistant", "content": "ok"}
        finish, tokens = "stop", 40
        if body.get("tools"):
            if self.tools:
                name = body["tools"][0]["function"]["name"]
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": name, "arguments": "{\"service\": \"checkout\"}"}}]}
                finish = "tool_calls"
        elif body.get("response_format"):
            msg["content"] = (json.dumps({"verdict": "attack", "confidence": 0.8})
                              if self.schema else "I think it is an attack")
        elif self.effort and body.get("reasoning_effort") == "high":
            tokens = 150
        return httpx.Response(200, json={
            "model": body["model"], "choices": [{"finish_reason": finish, "message": msg}],
            "usage": {"prompt_tokens": 100, "completion_tokens": tokens}})

    def mount(self) -> None:
        respx.get(f"{BASE}/models").mock(return_value=httpx.Response(
            200, json={"data": [{"id": m} for m in self.models]}))
        respx.post(f"{BASE}/chat/completions").mock(side_effect=self.chat)
        respx.post(f"{BASE}/embeddings").mock(return_value=httpx.Response(200, json={
            "data": [{"index": 0, "embedding": [0.0] * self.dim}]}))


async def _run(cfg: ModelsConfig, gw: Gateway) -> tuple[CheckReport, MemoryRecorder]:
    gw.mount()
    rec = MemoryRecorder()
    report = await run_model_check(ModelClient(cfg, rec, backoff_s=0), cfg)
    return report, rec


def _item(report: CheckReport, name: str) -> Any:
    return next(i for i in report.items if i.name == name)


@respx.mock
async def test_all_capabilities_pass(cfg: ModelsConfig) -> None:
    report, rec = await _run(cfg, Gateway())
    assert report.passed, report.items
    assert report.tool_calling is True
    assert report.reasoning_effort_effective is True
    assert rec.records and all(r.role == "check" for r in rec.records)
    assert all(r.incident_id is None for r in rec.records)


@respx.mock
async def test_missing_model_fails(cfg: ModelsConfig) -> None:
    report, _ = await _run(cfg, Gateway(models=("gpt-oss-120b",)))
    assert not report.passed
    assert "qwen3-embed" in _item(report, "models exist").detail


@respx.mock
async def test_schema_failure_is_fatal(cfg: ModelsConfig) -> None:
    report, _ = await _run(cfg, Gateway(schema=False))
    assert not report.passed
    assert not _item(report, "json schema: triage").ok


@respx.mock
async def test_no_tool_calling_is_reported_not_fatal(cfg: ModelsConfig) -> None:
    report, _ = await _run(cfg, Gateway(tools=False))
    assert report.passed
    assert report.tool_calling is False
    assert "plan-then-execute" in _item(report, "tool calling: investigator").detail


@respx.mock
async def test_ignored_reasoning_effort_reported_not_fatal(cfg: ModelsConfig) -> None:
    report, _ = await _run(cfg, Gateway(effort=False))
    assert report.passed
    assert report.reasoning_effort_effective is False
    assert "default effort" in _item(report, "reasoning effort").detail


@respx.mock
async def test_wrong_embedding_dimension_fatal(cfg: ModelsConfig) -> None:
    report, _ = await _run(cfg, Gateway(dim=1024))
    assert not report.passed
    assert not _item(report, "embeddings").ok


async def test_safe_recorder_captures_errors() -> None:
    class Broken:
        async def record(self, rec: LlmCallRecord) -> None:
            raise OSError("db down")

    safe = SafeRecorder(Broken())
    await safe.record(LlmCallRecord(role="check", model="m", status="ok"))
    assert safe.errors == ["OSError: db down"]
    assert safe.written == 0
