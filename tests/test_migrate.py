"""`faultline migrate` logic, exercised against a fake connection (no live database)."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import pytest

from faultline.db.migrate import (
    MIGRATIONS_DIR,
    Migration,
    applied_versions,
    apply_pending,
    discover,
    render,
    run_migrate,
)

REPO_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConn:
    def __init__(self, versions: list[str] | None) -> None:
        self.versions = versions  # None means schema_migrations does not exist yet
        self.executed: list[tuple[str, Any]] = []
        self.transactions = 0

    def execute(self, query: Any, params: Any = None) -> FakeCursor:
        text = query.decode() if isinstance(query, bytes) else str(query)
        self.executed.append((text, params))
        if "to_regclass" in text:
            return FakeCursor([(None if self.versions is None else "schema_migrations",)])
        if text.startswith("SELECT version FROM schema_migrations"):
            return FakeCursor([(v,) for v in self.versions or []])
        if text.startswith("INSERT INTO schema_migrations"):
            self.versions = [*(self.versions or []), params[0]]
        return FakeCursor([])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.transactions += 1
        yield


def test_repo_migrations_discovered_in_order() -> None:
    found = discover(REPO_MIGRATIONS)
    assert [m.version for m in found] == ["001_core", "002_vectors"]
    assert MIGRATIONS_DIR.name == "migrations"


def test_discover_rejects_duplicate_numbers(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "001_b.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="duplicate"):
        discover(tmp_path)


def test_discover_ignores_unnumbered_files(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "notes.sql").write_text("SELECT 1;")
    assert [m.version for m in discover(tmp_path)] == ["001_a"]


def test_render_fills_embed_dim() -> None:
    sql = (REPO_MIGRATIONS / "002_vectors.sql").read_text()
    out = render(sql, 2560)
    assert "{{" not in out
    assert out.count("vector(2560)") == 2


def test_render_rejects_bad_dim() -> None:
    with pytest.raises(ValueError):
        render("vector({{EMBED_DIM}})", 0)


def test_render_rejects_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        render("SELECT '{{OTHER}}';", 8)


def test_applied_versions_empty_before_first_migration() -> None:
    assert applied_versions(FakeConn(None)) == set()


def test_apply_pending_from_empty_db() -> None:
    conn = FakeConn(None)
    done = apply_pending(conn, discover(REPO_MIGRATIONS), embed_dim=2560)
    assert done == ["001_core", "002_vectors"]
    assert conn.versions == ["001_core", "002_vectors"]
    assert conn.transactions == 2
    scripts = [q for q, _ in conn.executed if "CREATE TABLE" in q]
    assert "vector(2560)" in scripts[1]


def test_apply_pending_is_idempotent() -> None:
    conn = FakeConn(["001_core", "002_vectors"])
    assert apply_pending(conn, discover(REPO_MIGRATIONS), embed_dim=2560) == []
    assert conn.transactions == 0


def test_apply_pending_only_new(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "002_b.sql").write_text("SELECT 2;")
    conn = FakeConn(["001_a"])
    assert apply_pending(conn, discover(tmp_path), embed_dim=8) == ["002_b"]


def test_run_migrate_calls_checkpointer_setup_after_migrations() -> None:
    conn = FakeConn(None)
    order: list[str] = []

    def setup(url: str) -> None:
        order.append(f"setup:{url}:{len(conn.versions or [])}")

    migrations = [Migration(version="001_a", path=REPO_MIGRATIONS / "001_core.sql")]
    done = run_migrate(
        "postgresql://x", migrations, embed_dim=8, connect=lambda _url: nullcontext(conn),
        setup_checkpointer=setup,
    )
    assert done == ["001_a"]
    assert order == ["setup:postgresql://x:1"]
