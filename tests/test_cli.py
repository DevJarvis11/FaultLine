import subprocess
import sys
from pathlib import Path

import pytest

from faultline.cli import main

REPO = Path(__file__).resolve().parents[1]

COMMANDS = [
    ["serve"],
    ["mcp"],
    ["index-runbooks"],
    ["eval", "--suite", "replay"],
    ["eval", "--suite", "live-quick"],
    ["eval", "--suite", "live-full"],
]


@pytest.mark.parametrize("argv", COMMANDS)
def test_unimplemented_commands_exit_clearly(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"faultline {argv[0]}" in err
    assert "not implemented in this session" in err


def test_no_command_shows_usage(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_eval_requires_suite() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["eval"])
    assert exc.value.code == 2


def test_eval_rejects_unknown_suite() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["eval", "--suite", "nightly"])
    assert exc.value.code == 2


def test_unknown_command_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["deploy"])
    assert exc.value.code == 2


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "faultline", "mcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not implemented in this session" in result.stderr


def test_migrate_without_database_url_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["migrate"])
    assert exc.value.code == 1
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_migrate_runs_with_repo_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def fake_run(url: str, migrations: list[object], embed_dim: int) -> list[str]:
        seen.update(url=url, n=len(migrations), dim=embed_dim)
        return ["001_core", "002_vectors"]

    monkeypatch.chdir(REPO)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    monkeypatch.setattr("faultline.db.migrate.run_migrate", fake_run)
    with pytest.raises(SystemExit) as exc:
        main(["migrate"])
    assert exc.value.code == 0
    assert seen == {"url": "postgresql://u:p@db/x", "n": 2, "dim": 2560}
    assert "001_core, 002_vectors" in capsys.readouterr().out
