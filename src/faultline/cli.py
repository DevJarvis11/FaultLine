"""Command-line entry point for Faultline (BUILD_SPEC: Entry points)."""

import argparse
import sys
from collections.abc import Callable, Sequence

SUITES = ("replay", "live-quick", "live-full")

COMMANDS: dict[str, str] = {
    "serve": "API (Alertmanager webhook), workflow workers, Slack Socket Mode client, expiry loop",
    "mcp": "Read-only MCP tool server (streamable HTTP)",
    "migrate": "Apply SQL migrations and LangGraph checkpointer setup",
    "check-models": "Startup model check, standalone",
    "index-runbooks": "Embed runbooks/ into runbook_chunks",
    "eval": "Run the eval harness",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="faultline", description="Faultline incident agent")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, help_text in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        if name == "eval":
            cmd.add_argument("--suite", required=True, choices=SUITES)
    return parser


def _not_implemented(command: str) -> int:
    print(f"faultline {command}: not implemented in this session", file=sys.stderr)
    return 1


def cmd_migrate(_args: argparse.Namespace) -> int:
    from faultline.config import load_env, load_models
    from faultline.db.migrate import discover, run_migrate

    env = load_env()
    if env.database_url is None:
        print("faultline migrate: DATABASE_URL is not set", file=sys.stderr)
        return 1
    embed_dim = load_models(env=env).roles.embedding.dimensions
    ran = run_migrate(env.database_url.get_secret_value(), discover(), embed_dim)
    print(f"applied: {', '.join(ran) if ran else 'nothing (up to date)'}; checkpointer ready")
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "migrate": cmd_migrate,
}


def main(argv: Sequence[str] | None = None) -> None:
    """Parse arguments and dispatch; exits with the command's status code."""
    args = build_parser().parse_args(argv)
    handler = HANDLERS.get(args.command)
    sys.exit(handler(args) if handler else _not_implemented(args.command))
