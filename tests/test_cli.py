import subprocess
import sys

import pytest

from faultline.cli import main

COMMANDS = [
    ["serve"],
    ["mcp"],
    ["migrate"],
    ["check-models"],
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
        [sys.executable, "-m", "faultline", "check-models"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not implemented in this session" in result.stderr
