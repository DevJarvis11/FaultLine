"""OpenAI-compatible model client for the LiteLLM gateway (DESIGN: The server in use).

- Roles, not model names: settings come from `config/models.yaml`.
- Redaction runs on every message before it is sent or stored.
- Timeouts, rate limits and 5xx are retried `max_retries` times with backoff (shared server).
- `finish_reason == "length"` means the answer was cut off (reasoning tokens count against
  `max_tokens`): the call is treated as failed, retried once with double the cap, then
  `TruncatedError` is raised so the caller escalates.
- Only the final answer, tool calls and token counts are stored; reasoning text is dropped.
"""

import asyncio
import time
from typing import Any

import httpx
from pydantic import BaseModel

from faultline.config import ChatRole, ChatRoleName, ModelsConfig, ReasoningEffort
from faultline.llm.records import LlmCallRecord, LlmCallRecorder, LlmRole, LlmStatus
from faultline.redaction import redact

TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
Message = dict[str, Any]


class LlmError(RuntimeError):
    """The model call failed after retries; the caller decides (escalate or degraded mode)."""


class TruncatedError(LlmError):
    """The answer hit `max_tokens` twice."""


class _CallFailedError(Exception):
    def __init__(self, status: LlmStatus, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str  # JSON text, exactly as the model produced it


class ChatResult(BaseModel):
    model: str
    content: str | None
    tool_calls: list[ToolCall] = []
    finish_reason: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def _parse_chat(data: dict[str, Any], model: str) -> ChatResult:
    try:
        choice = data["choices"][0]
        msg = choice["message"]
        calls = [
            ToolCall(id=c.get("id", ""), name=c["function"]["name"],
                     arguments=c["function"].get("arguments") or "")
            for c in msg.get("tool_calls") or []
        ]
        usage = data.get("usage") or {}
        return ChatResult(
            model=data.get("model") or model,
            content=msg.get("content"),
            tool_calls=calls,
            finish_reason=choice.get("finish_reason") or "",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise _CallFailedError("error", f"unexpected chat response shape: {exc!r}") from exc


def _stored_response(res: ChatResult) -> dict[str, Any]:
    return {
        "content": redact(res.content),
        "tool_calls": [redact(c.model_dump()) for c in res.tool_calls],
        "finish_reason": res.finish_reason,
    }


class ModelClient:
    def __init__(
        self,
        cfg: ModelsConfig,
        recorder: LlmCallRecorder,
        *,
        use_reasoning_effort: bool = True,
        backoff_s: float = 1.0,
    ) -> None:
        self._cfg = cfg
        self._base, self._key = cfg.provider.require()
        self._recorder = recorder
        self.use_reasoning_effort = use_reasoning_effort
        self._backoff = backoff_s

    # ---- HTTP with retries ----

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            timeout=self._cfg.provider.timeout_s,
            headers={"Authorization": f"Bearer {self._key}"},
        )

    async def _request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        last = _CallFailedError("error", "no attempt made")
        for attempt in range(self._cfg.provider.max_retries + 1):
            try:
                async with self._http() as http:
                    resp = await http.request(method, path, json=body)
            except httpx.TimeoutException as exc:
                last = _CallFailedError("timeout", f"timeout: {exc}")
            except httpx.TransportError as exc:
                last = _CallFailedError("error", f"transport error: {exc}")
            else:
                if resp.status_code in TRANSIENT_STATUS:
                    last = _CallFailedError("error", f"HTTP {resp.status_code}")
                elif resp.is_error:
                    raise _CallFailedError("error", f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    try:
                        data: dict[str, Any] = resp.json()
                    except ValueError as exc:
                        raise _CallFailedError("error", "response is not JSON") from exc
                    return data
            if attempt < self._cfg.provider.max_retries:
                await asyncio.sleep(self._backoff * 2**attempt)
        raise last

    # ---- chat ----

    def _chat_body(
        self, rc: ChatRole, messages: list[Message], cap: int,
        effort: ReasoningEffort | None, extra: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": rc.model, "messages": messages,
            "temperature": rc.temperature, "max_tokens": cap,
        }
        if self.use_reasoning_effort:
            body["reasoning_effort"] = effort or rc.reasoning_effort
        body.update({k: v for k, v in extra.items() if v is not None})
        return body

    async def _chat_once(
        self, body: dict[str, Any], meta: dict[str, Any]
    ) -> ChatResult:
        stored_req = {k: v for k, v in body.items() if k != "tools"}
        if body.get("tools"):
            stored_req["tools"] = [t.get("function", {}).get("name") for t in body["tools"]]
        start = time.monotonic()
        try:
            res = _parse_chat(await self._request("POST", "/chat/completions", body),
                              body["model"])
        except _CallFailedError as exc:
            await self._record(meta, body["model"], start, exc.status, exc.error, stored_req)
            raise LlmError(exc.error) from exc
        truncated = res.finish_reason == "length"
        await self._record(
            meta, res.model, start, "error" if truncated else "ok",
            "finish_reason=length" if truncated else None, stored_req,
            _stored_response(res), res.input_tokens, res.output_tokens,
        )
        return res

    async def chat(
        self,
        role: ChatRoleName,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        incident_id: Any = None,
        prompt_version: str | None = None,
        record_as: LlmRole | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ChatResult:
        rc = self._cfg.roles.chat_roles()[role]
        safe = redact(messages)
        extra = {"tools": tools, "tool_choice": tool_choice, "response_format": response_format}
        meta = {"incident_id": incident_id, "prompt_version": prompt_version,
                "role": record_as or role}
        cap = rc.max_tokens
        for _ in range(2):
            body = self._chat_body(rc, safe, cap, reasoning_effort, extra)
            res = await self._chat_once(body, meta)
            if res.finish_reason != "length":
                return res
            cap *= 2
        raise TruncatedError(f"{role}: answer cut off at max_tokens twice (last cap {cap // 2})")

    # ---- embeddings and model list ----

    async def embed(
        self, texts: list[str], *, incident_id: Any = None, record_as: LlmRole = "embedding"
    ) -> list[list[float]]:
        er = self._cfg.roles.embedding
        meta = {"incident_id": incident_id, "prompt_version": None, "role": record_as}
        # Inputs can be whole runbook sections: store counts, not the text or the vectors.
        stored_req = {"model": er.model, "inputs": len(texts)}
        start = time.monotonic()
        try:
            data = await self._request("POST", "/embeddings",
                                       {"model": er.model, "input": redact(texts)})
            vectors = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
        except _CallFailedError as exc:
            await self._record(meta, er.model, start, exc.status, exc.error, stored_req)
            raise LlmError(exc.error) from exc
        except (KeyError, TypeError) as exc:
            await self._record(meta, er.model, start, "error", "bad embeddings shape", stored_req)
            raise LlmError(f"unexpected embeddings response: {exc!r}") from exc
        dims = {len(v) for v in vectors}
        ok = dims == {er.dimensions}
        usage = data.get("usage") or {}
        await self._record(
            meta, er.model, start, "ok" if ok else "error",
            None if ok else f"dimension {sorted(dims)} != {er.dimensions}", stored_req,
            {"vectors": len(vectors), "dimensions": sorted(dims)}, usage.get("prompt_tokens"),
        )
        if not ok:
            raise LlmError(f"embedding dimension {sorted(dims)} != configured {er.dimensions}")
        return vectors

    async def list_models(self) -> list[str]:
        try:
            data = await self._request("GET", "/models")
        except _CallFailedError as exc:
            raise LlmError(exc.error) from exc
        return [m["id"] for m in data.get("data", []) if "id" in m]

    async def _record(
        self, meta: dict[str, Any], model: str, start: float, status: LlmStatus,
        error: str | None, request: dict[str, Any], response: dict[str, Any] | None = None,
        input_tokens: int | None = None, output_tokens: int | None = None,
    ) -> None:
        await self._recorder.record(LlmCallRecord(
            incident_id=meta["incident_id"], role=meta["role"], model=model,
            prompt_version=meta["prompt_version"], input_tokens=input_tokens,
            output_tokens=output_tokens, latency_ms=int((time.monotonic() - start) * 1000),
            status=status, error=error, request=request, response=response,
        ))
