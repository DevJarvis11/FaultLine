"""llm_calls recording: the INSERT matches the 001_core.sql columns (fake connection)."""

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from faultline.llm.records import INSERT_SQL, LlmCallRecord, PostgresRecorder

DDL = (Path(__file__).resolve().parents[1] / "migrations" / "001_core.sql").read_text()


class FakeAsyncConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, query: Any, params: Any = None) -> None:
        self.calls.append((str(query), params))

    async def __aenter__(self) -> "FakeAsyncConn":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _llm_calls_columns() -> set[str]:
    block = DDL.split("CREATE TABLE llm_calls (")[1].split(");")[0]
    return {m.group(1) for m in re.finditer(r"^\s+([a-z_]+)\s", block, re.M)} - {"id", "created_at"}


def test_insert_columns_match_ddl() -> None:
    cols = re.search(r"INSERT INTO llm_calls \(([^)]*)\)", INSERT_SQL)
    assert cols is not None
    assert {c.strip() for c in cols.group(1).split(",")} == _llm_calls_columns()


def test_role_and_status_checked_like_ddl() -> None:
    with pytest.raises(ValidationError):
        LlmCallRecord(role="planner", model="m", status="ok")
    with pytest.raises(ValidationError):
        LlmCallRecord(role="check", model="m", status="maybe")


async def test_postgres_recorder_inserts_row() -> None:
    conn = FakeAsyncConn()
    rec = PostgresRecorder("postgresql://x", connect=lambda _url: conn)
    iid = uuid4()
    await rec.record(LlmCallRecord(
        incident_id=iid, role="triage", model="gpt-oss-120b", prompt_version="triage-v1",
        input_tokens=10, output_tokens=5, latency_ms=120, status="ok",
        request={"messages": []}, response={"content": "ok"}))
    sql, params = conn.calls[0]
    assert sql == INSERT_SQL
    assert params[0] == iid and params[1] == "triage" and params[7] == "ok"
    assert params[9].obj == {"messages": []}  # psycopg Jsonb wrapper
