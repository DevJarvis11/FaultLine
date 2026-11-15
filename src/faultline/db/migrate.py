"""Apply numbered SQL migrations in order, then LangGraph's checkpointer setup.

DESIGN (Migrations): each file in `migrations/` runs once, in its own transaction, and its
version (the file stem, for example `001_core`) is recorded in `schema_migrations`.
`{{EMBED_DIM}}` is filled from `config/models.yaml`.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

MIGRATIONS_DIR = Path("migrations")
EMBED_DIM_PLACEHOLDER = "{{EMBED_DIM}}"

_FILE = re.compile(r"^(\d+)_[a-z0-9_]+\.sql$")
_ANY_PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")


class Connection(Protocol):
    """The subset of `psycopg.Connection` that migrations use."""

    def execute(self, query: Any, params: Any = None) -> Any: ...

    def transaction(self) -> AbstractContextManager[Any]: ...


class Migration(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    path: Path


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Numbered `.sql` files sorted by number; duplicate numbers are an error."""
    found: dict[int, Migration] = {}
    for path in directory.glob("*.sql"):
        m = _FILE.match(path.name)
        if not m:
            continue
        number = int(m.group(1))
        if number in found:
            raise ValueError(f"duplicate migration number {number}: {path.name}")
        found[number] = Migration(version=path.stem, path=path)
    return [found[n] for n in sorted(found)]


def render(sql: str, embed_dim: int) -> str:
    if embed_dim <= 0:
        raise ValueError(f"embedding dimension must be positive, got {embed_dim}")
    out = sql.replace(EMBED_DIM_PLACEHOLDER, str(embed_dim))
    left = _ANY_PLACEHOLDER.findall(out)
    if left:
        raise ValueError(f"unknown placeholder(s) in migration: {sorted(set(left))}")
    return out


def applied_versions(conn: Connection) -> set[str]:
    exists = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()
    if exists is None or exists[0] is None:
        return set()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_pending(conn: Connection, migrations: Sequence[Migration], embed_dim: int) -> list[str]:
    """Run each not-yet-applied migration in its own transaction; return what ran."""
    done = applied_versions(conn)
    ran: list[str] = []
    for mig in migrations:
        if mig.version in done:
            continue
        script = render(mig.path.read_text(encoding="utf-8"), embed_dim)
        with conn.transaction():
            # Trusted repo file with no parameters: psycopg runs it as one multi-statement
            # script. Bytes keep psycopg's LiteralString typing satisfied.
            conn.execute(script.encode("utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (mig.version,))
        ran.append(mig.version)
    return ran


@contextmanager
def _psycopg_connect(url: str) -> Iterator[Connection]:
    import psycopg

    # Autocommit, so each `conn.transaction()` block is its own real transaction.
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


def setup_langgraph_checkpointer(url: str) -> None:
    """Create LangGraph's checkpoint tables via the library's own `setup()`."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()


def run_migrate(
    database_url: str,
    migrations: Sequence[Migration],
    embed_dim: int,
    connect: Callable[[str], AbstractContextManager[Connection]] = _psycopg_connect,
    setup_checkpointer: Callable[[str], None] = setup_langgraph_checkpointer,
) -> list[str]:
    with connect(database_url) as conn:
        ran = apply_pending(conn, migrations, embed_dim)
    setup_checkpointer(database_url)
    return ran
