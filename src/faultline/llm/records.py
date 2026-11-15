"""Records of every model call, written to the `llm_calls` table (migrations/001_core.sql).

Requests and responses are stored redacted; the model's reasoning text is never stored.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

LlmRole = Literal["triage", "investigator", "report", "embedding", "check"]
LlmStatus = Literal["ok", "error", "timeout", "fallback"]

INSERT_SQL = (
    "INSERT INTO llm_calls (incident_id, role, model, prompt_version, input_tokens, "
    "output_tokens, latency_ms, status, error, request, response) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


class LlmCallRecord(BaseModel):
    incident_id: UUID | None = None  # null for startup checks
    role: LlmRole
    model: str
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    status: LlmStatus
    error: str | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None


class LlmCallRecorder(Protocol):
    async def record(self, rec: LlmCallRecord) -> None: ...


class MemoryRecorder:
    """Keeps records in a list (tests, or runs without a database)."""

    def __init__(self) -> None:
        self.records: list[LlmCallRecord] = []

    async def record(self, rec: LlmCallRecord) -> None:
        self.records.append(rec)


@asynccontextmanager
async def _psycopg_connect(url: str) -> AsyncIterator[Any]:
    import psycopg

    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        yield conn


class PostgresRecorder:
    """One short autocommit connection per record; call volume is low (a few per incident)."""

    def __init__(
        self,
        database_url: str,
        connect: Callable[[str], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self._url = database_url
        self._connect = connect or _psycopg_connect

    async def record(self, rec: LlmCallRecord) -> None:
        from psycopg.types.json import Jsonb

        def js(v: dict[str, Any] | None) -> Jsonb | None:
            return None if v is None else Jsonb(v)

        params = (
            rec.incident_id, rec.role, rec.model, rec.prompt_version, rec.input_tokens,
            rec.output_tokens, rec.latency_ms, rec.status, rec.error,
            js(rec.request), js(rec.response),
        )
        async with self._connect(self._url) as conn:
            await conn.execute(INSERT_SQL, params)
