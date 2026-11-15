# Progress

Resume point: Session 2, first task (Session 1 done).

## Session 1 plan (Repository scaffold)
1. uv project, dependencies, entry points, package layout (builder).
2. Config files (.env.example, settings.yaml, service_catalog.yaml) and typed config loading (builder).
3. `schemas.py` data contracts with tests (builder).
4. CLI skeleton (builder).
5. `.github/workflows/ci.yml`, action versions VERIFIED; install OPA CLI locally (pinned) (builder).
6. Main session: ruff, mypy, pytest, opa test; push; confirm CI green.

| Session | Status | Notes |
| --- | --- | --- |
| 0 Prerequisites (owner) | done | Filled in by the owner before starting |
| 1 Repository scaffold | done | 5 commits f870c1d..76c99ae; CI green run 43725339525 |
| 2 Cluster base | not started | |
| 3 GitOps, app, observability | not started | |
| 4 Security and scenario tooling | not started | |
| 5 Data, policy, LLMOps | not started | |
| 6 Model layer | not started | |
| 7 Evidence | not started | |
| 8 Reasoning | not started | |
| 9 Slack and approval | not started | |
| 10 Actions | not started | |
| 11 Scenarios | not started | |
| 12 Eval harness | not started | |
| 13 Memory, reports, modes | not started | |
| 14 Proof | not started | |

## Evidence log
<!-- Per task: command run and result. -->
### Session 1 (2026-11-15, Opus)
- `uv run ruff check`: clean. `uv run mypy src` (strict): no issues in 5 files. `uv run pytest`: 52 passed. `opa test policies/ -v`: exit 0 (empty dir, policies come in Session 5). `uv sync --locked`: ok.
- CI: https://github.com/DevJarvis11/FaultLine/actions/runs/43725339525 passed (checks job, 18s).
- Report: uv project (Python 3.12, entry point `faultline.cli:main`), config files copied from spec plus typed Pydantic loading (`FAULTLINE_MODE` override, SecretStr secrets), `schemas.py` verbatim from spec (ruff N815 and mypy bare-dict exemptions on that file only), argparse CLI skeleton (all commands exit 1 "not implemented in this session"), ci.yml, policies/.gitkeep. No Containerfile or models.yaml (not in Session 1 scope).
- Open: service_catalog.yaml placeholders (`<SLACK_USER_ID>` etc.) wait on Slack inputs; CI annotation says ubuntu-latest moves to Ubuntu 26 (informational).

## VERIFY log
<!-- Per VERIFY item: what was confirmed, value, source URL or command. -->
- GitHub Actions versions (Session 1; via `gh api repos/<repo>/releases/latest`): actions/checkout v7.0.1 (https://github.com/actions/checkout/releases); astral-sh/setup-uv v10.3.3, full tag pinned, uv 0.12.0 pinned to match uv.lock (https://github.com/astral-sh/setup-uv/releases); open-policy-agent/setup-opa v2.4.0, full tag pinned, OPA 1.22.4 (https://github.com/open-policy-agent/setup-opa/releases). Recheck in Session 12.
- OPA CLI v1.22.4 installed at ~/.local/bin/opa, sha256 35e3b352…dcccba matches the release .sha256 (https://github.com/open-policy-agent/opa/releases/tag/v1.22.4).

## Blocked
<!-- Item, file, what it needed, why blocked. -->
