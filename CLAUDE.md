# Faultline: rules for Claude Code

## Sources of truth
- docs/DESIGN.md: what to build and why.
- docs/BUILD_SPEC.md: how to build it, including the session plan and model routing.
- docs/INPUTS.md: environment values provided by the owner (no secrets).
- docs/PROGRESS.md: what is done and where to resume. Update it before ending.

## Working rules
- Work on one session at a time, exactly as scoped in the session plan.
- Use uv only: `uv add`, `uv remove`, `uv sync`, `uv run`. Never pip.
- Never invent metric names, label names, Helm values, API fields, CLI flags or
  model names. For anything marked VERIFY, confirm it from current official
  documentation or by querying the live system, and record the source in
  docs/PROGRESS.md. If it cannot be confirmed, stop and ask.
- Never hard-code a model name. Read config/models.yaml.
- If a required input is missing, stop and ask. Do not use made-up values.
- The lab runs in WSL2. If the WSL2 kernel gate in docs/DESIGN.md fails, or Cilium or Falco cannot start, stop and ask the owner. Do not switch approach on your own.
- Do not add components that the Design tab's "Lean lab" section leaves out.
- On the model server, use only the LiteLLM gateway (chat and embeddings) with the API key
  from .env, read-only. Never SSH to it, never use its direct ports, never change anything on it.
- Pin every Helm chart version and container image tag you install.

## Token rules
- Read only the doc sections the current task needs, by heading. Never load
  both docs in full.
- Subagents report in at most 15 lines: files changed, commands, results,
  open issues. No file contents or full logs in reports.
- Keep command output short: quiet flags, and only the last lines of long
  logs (for example `| tail -n 40`).
- Run targeted tests while working; run the full suite once at the end of a task.
- Do not re-read files you have just written unless a check failed.

## Safety rules
- Faultline never writes to the cluster directly. All changes go through the
  faultline-gitops repository and Argo CD. The only exceptions are the Argo CD
  sync API call and reading cluster state.
- Faultline's Kubernetes service account is read-only and cannot read Secrets
  or the chaos-mesh namespace.
- Simulated attack traffic targets only this lab.
- Never commit secrets. .env is git-ignored; .env.example has no values.
- Ask before deleting files, branches, namespaces or data.

## Quality rules
- Write tests first for: redaction, the verifier, OPA policies, idempotency
  keys, and evidence-pack size limits.
- Definition of done for any task:
  `uv run ruff check`, `uv run mypy src`, `uv run pytest` and
  `opa test policies/ -v` all pass, plus the session's "Done when" checks.
- Keep functions small and typed; Pydantic models for every boundary.
- Commit at the end of each task with a message naming the session and task.
