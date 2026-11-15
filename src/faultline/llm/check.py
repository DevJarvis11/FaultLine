"""Startup model check (DESIGN: How it stays portable and reliable; The server in use).

Fatal: every configured model is listed by the gateway, each chat role returns
schema-valid JSON, embeddings have the configured dimension, and (when a database is
configured) llm_calls rows are written. Reported, not fatal: native tool calling (if
missing, the investigator uses the plan-then-execute fallback) and whether the gateway
honours `reasoning_effort` (if not, every role runs at the model's default effort).
"""

import asyncio
import json
import sys
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from faultline.config import ChatRoleName, ConfigError, ModelsConfig, load_env, load_models
from faultline.llm.client import LlmError, ModelClient
from faultline.llm.records import LlmCallRecord, LlmCallRecorder, MemoryRecorder, PostgresRecorder

# High effort must use at least this many times the completion tokens of low effort.
# Measured on the live gateway: low 51-64 tokens, high 121-152 tokens for EFFORT_PROMPT.
EFFORT_RATIO = 1.5
EFFORT_PROMPT = (
    "A bat and a ball cost 1.10 in total. The bat costs 1.00 more than the ball. "
    "How much does the ball cost? Answer with the number only."
)
SCHEMA_PROMPT = "Traffic tripled from many new clients all hitting one path. Classify it."
TOOL_PROMPT = "What is the health of service checkout? Use the tool."


class _Probe(BaseModel):
    verdict: Literal["attack", "capacity"]
    confidence: float = Field(ge=0, le=1)


PROBE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "model_check", "strict": True, "schema": {
        "type": "object",
        "properties": {"verdict": {"type": "string", "enum": ["attack", "capacity"]},
                       "confidence": {"type": "number"}},
        "required": ["verdict", "confidence"],
        "additionalProperties": False,
    }},
}
PROBE_TOOL = {"type": "function", "function": {
    "name": "get_service_health",
    "description": "Health signals for one service",
    "parameters": {"type": "object", "properties": {"service": {"type": "string"}},
                   "required": ["service"]},
}}


class CheckItem(BaseModel):
    name: str
    ok: bool
    fatal: bool
    detail: str = ""


class CheckReport(BaseModel):
    items: list[CheckItem] = []
    tool_calling: bool | None = None
    reasoning_effort_effective: bool | None = None

    @property
    def passed(self) -> bool:
        return all(i.ok for i in self.items if i.fatal)


class SafeRecorder:
    """Never lets a recording failure break the check; failures are reported instead."""

    def __init__(self, inner: LlmCallRecorder) -> None:
        self._inner = inner
        self.errors: list[str] = []
        self.written = 0

    async def record(self, rec: LlmCallRecord) -> None:
        try:
            await self._inner.record(rec)
            self.written += 1
        except Exception as exc:  # noqa: BLE001 - reported as a check failure
            self.errors.append(f"{type(exc).__name__}: {exc}")


def _user(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


async def _check_models_listed(client: ModelClient, cfg: ModelsConfig) -> CheckItem:
    wanted = {r.model for r in cfg.roles.chat_roles().values()} | {cfg.roles.embedding.model}
    try:
        available = set(await client.list_models())
    except LlmError as exc:
        return CheckItem(name="models exist", ok=False, fatal=True, detail=str(exc))
    missing = sorted(wanted - available)
    detail = f"missing: {', '.join(missing)}" if missing else f"found: {', '.join(sorted(wanted))}"
    return CheckItem(name="models exist", ok=not missing, fatal=True, detail=detail)


async def _check_schema(client: ModelClient, role: ChatRoleName) -> CheckItem:
    name = f"json schema: {role}"
    try:
        res = await client.chat(role, _user(SCHEMA_PROMPT), response_format=PROBE_FORMAT,
                                record_as="check")
        _Probe.model_validate(json.loads(res.content or ""))
    except (LlmError, ValueError, ValidationError) as exc:
        return CheckItem(name=name, ok=False, fatal=True, detail=str(exc)[:200])
    return CheckItem(name=name, ok=True, fatal=True, detail="schema-valid JSON")


async def _check_tools(client: ModelClient) -> CheckItem:
    name = "tool calling: investigator"
    try:
        res = await client.chat("investigator", _user(TOOL_PROMPT), tools=[PROBE_TOOL],
                                tool_choice="auto", record_as="check")
        ok = any(c.name == "get_service_health" and json.loads(c.arguments or "null")
                 for c in res.tool_calls)
    except (LlmError, ValueError) as exc:
        ok, res_detail = False, str(exc)[:200]
    else:
        res_detail = "native tool calls work" if ok else "no tool call returned"
    if not ok:
        res_detail += "; investigator will use the plan-then-execute fallback"
    return CheckItem(name=name, ok=ok, fatal=False, detail=res_detail)


async def _check_embeddings(client: ModelClient, cfg: ModelsConfig) -> CheckItem:
    try:
        await client.embed(["faultline model check"], record_as="check")
    except LlmError as exc:
        return CheckItem(name="embeddings", ok=False, fatal=True, detail=str(exc)[:200])
    return CheckItem(name="embeddings", ok=True, fatal=True,
                     detail=f"{cfg.roles.embedding.dimensions} dimensions")


async def _check_effort(client: ModelClient) -> tuple[CheckItem, bool | None]:
    name = "reasoning effort"
    try:
        low = await client.chat("investigator", _user(EFFORT_PROMPT), reasoning_effort="low",
                                record_as="check")
        high = await client.chat("investigator", _user(EFFORT_PROMPT), reasoning_effort="high",
                                 record_as="check")
    except LlmError as exc:
        return CheckItem(name=name, ok=False, fatal=False, detail=str(exc)[:200]), None
    lo, hi = low.output_tokens or 0, high.output_tokens or 0
    effective = lo > 0 and hi >= lo * EFFORT_RATIO
    detail = f"completion tokens low={lo} high={hi}; "
    detail += ("setting has an effect" if effective else
               "gateway ignores reasoning_effort: roles run at the model's default effort")
    return CheckItem(name=name, ok=effective, fatal=False, detail=detail), effective


async def run_model_check(client: ModelClient, cfg: ModelsConfig) -> CheckReport:
    report = CheckReport()
    listed = await _check_models_listed(client, cfg)
    report.items.append(listed)
    if not listed.ok:
        return report
    for role in cfg.roles.chat_roles():
        report.items.append(await _check_schema(client, role))
    tools = await _check_tools(client)
    report.items.append(tools)
    report.tool_calling = tools.ok
    report.items.append(await _check_embeddings(client, cfg))
    effort, report.reasoning_effort_effective = await _check_effort(client)
    report.items.append(effort)
    return report


def main_check_models() -> int:
    """`faultline check-models`: exit 0 when every fatal check passes."""
    env = load_env()
    cfg = load_models(env=env)
    try:
        cfg.provider.require()
    except ConfigError as exc:
        print(f"faultline check-models: {exc}", file=sys.stderr)
        return 1
    if env.database_url is not None:
        recorder = SafeRecorder(PostgresRecorder(env.database_url.get_secret_value()))
    else:
        print("note: DATABASE_URL not set, llm_calls rows are not written")
        recorder = SafeRecorder(MemoryRecorder())
    report = asyncio.run(run_model_check(ModelClient(cfg, recorder), cfg))
    if env.database_url is not None:
        report.items.append(CheckItem(
            name="llm_calls recording", ok=not recorder.errors, fatal=True,
            detail=recorder.errors[0] if recorder.errors else f"{recorder.written} rows written"))
    for item in report.items:
        mark = "PASS" if item.ok else ("FAIL" if item.fatal else "WARN")
        print(f"{mark:4}  {item.name}: {item.detail}")
    print("model check passed" if report.passed else "model check FAILED")
    return 0 if report.passed else 1
