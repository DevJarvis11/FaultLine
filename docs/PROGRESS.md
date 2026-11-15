# Progress

Resume point: Session 3, first task (Sessions 1-2 done; cluster-free parts of 5-7 merged from the parallel track).

Parallel track (owner approved 2026-11-15, an exception to "one session at a time"): builder in a git worktree builds the cluster-free parts of Sessions 5 to 7 (OPA policies and client, migration code, models.yaml, model client, check-models, redaction, size limits, evidence IDs). It merges into main after Session 2. Live checks (migrate, the llm_calls row, packs on live data) stay with their sessions.

## Session 2 plan (Cluster base, Opus)
1. Kernel gate: owner runs `sudo modprobe -a sch_ingress cls_bpf algif_hash` (pending in INPUTS). Stop if it fails.
2. Owner runs the k3s install (sudo): v1.37.2+k3s1 pinned, `--flannel-backend=none --disable-network-policy --disable=traefik`. k3s's own kube-proxy is kept (design does not ask for kube-proxy replacement). Kubeconfig copied to ~/.kube/k3s.yaml (existing ~/.kube/config holds a docker-desktop context and is left alone).
3. Helm v4.3.2 and cilium CLI v0.20.3 into ~/.local/bin (no sudo); Cilium 1.20.4 via Helm, pod CIDR 10.42.0.0/16, Hubble metrics on, relay and UI off (builder).
4. Traefik via Helm in `ingress`: JSON access logs, client-IP preservation (builder).
5. Done-when checks: `cilium status`, Hubble flows, two generator containers show two client IPs.

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
| 2 Cluster base | done | Cilium 1.20.4, Traefik 42.4.0; native Docker Engine 29.9.3 replaced Docker Desktop integration (owner's choice) |
| 3 GitOps, app, observability | not started | |
| 4 Security and scenario tooling | not started | |
| 5 Data, policy, LLMOps | partial | OPA policies, client, migration code merged; Postgres, OPA server, MLflow, live migrate pending |
| 6 Model layer | partial | models.yaml, client, check-models merged (live gateway check passed); llm_calls DB row and plan-then-execute pending |
| 7 Evidence | partial | redaction, size limits, evidence IDs merged; collectors, traffic shape, live packs pending |
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

### Session 2 (2026-11-15, Opus)
- Helm v4.3.2 and cilium-cli v0.20.3 in ~/.local/bin, sha256 checked. Cilium 1.20.4: `cilium status --wait` all OK, node Ready, operator.replicas=1 (the default of 2 leaves one Pending on a single node). Hubble flows: `kubectl -n kube-system exec ds/cilium -c cilium-agent -- hubble observe` works; :9965 serves hubble_drop_total and hubble_flows_processed_total.
- Traefik chart 42.4.0 / image v3.7.18 in `ingress`, hostNetwork: true (Cilium reports HostPort disabled without KPR), service ClusterIP. Entry points web :8000, websecure :8443, traefik :8080, metrics :9101 (9100 left for node-exporter). JSON access log field `request_User-Agent` seen live.
- Memory used/available: 3.0G/7.4G at start, 3.7G/6.7G after Cilium, 3.8G/6.7G after Traefik.
- Open: Hubble dns metrics appear only once DNS L7 visibility policies exist; clients reach Traefik on 8000/8443, not 80/443.

- Done-when (after native Docker): `deploy/lab/check-client-ip.sh` -> ClientHost 172.20.0.2 and 172.21.0.2, PASS: 2 distinct client IPs (target is the docker0 gateway; WSL2 runs mirrored-style interfaces eth2 192.168.1.8). `cilium status`: Cilium OK, Operator OK. `hubble observe --last 3` inside the agent shows FORWARDED flows. Pod DNS and egress work after Docker's iptables changes (busybox test pod). Memory: 3.8G used, 6.6G available.
- Merge of the parallel track (db0466c): ruff clean, mypy clean (17 files), pytest 159 passed, opa test 11/11.

### Parallel track (Sessions 5-7, cluster-free; merged into main as db0466c)
- Commits ba6a516 (S5 OPA policies + client), 4ae9bce (S6 models.yaml + loader), 8d807fe (S5 migrations + migrate), 02ab247 (S7 redaction, size limits, evidence IDs), e5415eb (S6 client, check-models, llm_calls).
- Main session re-ran checks in the worktree: ruff clean; mypy clean (17 files); pytest 159 passed; opa test 11/11.
- Builder's own choices, not from the spec (owner to review): reasoning effort counts as effective when high uses >= 1.5x low's completion tokens; packs estimate 3 chars per token for the budget; missing tool calling warns and points to plan-then-execute instead of failing.
- Deviation: the sandbox refused `set -a; source .env` in the worktree, so the builder loaded .env in-process with the project's load_env(); no values were printed.
- Still needs live systems: `faultline migrate` on real Postgres; a real llm_calls row; the OPA server URL; S7 packs on live data; pgvector index choice (2560 dims; no vector index is created yet).

## VERIFY log
<!-- Per VERIFY item: what was confirmed, value, source URL or command. -->
- Model server (Session 6; live LiteLLM gateway, read-only calls, 2026-11-15): GET /models lists gpt-oss-120b, qwen3-embed, qwen3-rerank, cerebro-llm, mistral; qwen3-embed returns 2560 dims; chat works (responses carry `reasoning_content`, which the client drops); finish_reason `length` appears when max_tokens is too low; tool calling returns finish_reason `tool_calls`; response_format json_schema with strict is honoured; reasoning_effort changes token use (low 51-64, high 121-154 completion tokens).
- GitHub Actions versions (Session 1; via `gh api repos/<repo>/releases/latest`): actions/checkout v7.0.1 (https://github.com/actions/checkout/releases); astral-sh/setup-uv v10.3.3, full tag pinned, uv 0.12.0 pinned to match uv.lock (https://github.com/astral-sh/setup-uv/releases); open-policy-agent/setup-opa v2.4.0, full tag pinned, OPA 1.22.4 (https://github.com/open-policy-agent/setup-opa/releases). Recheck in Session 12.
- OPA CLI v1.22.4 installed at ~/.local/bin/opa, sha256 35e3b352…dcccba matches the release .sha256 (https://github.com/open-policy-agent/opa/releases/tag/v1.22.4).

- Cilium 1.20 (Session 2): hubble.metrics.enabled is a list [dns, drop, flow], port 9965; relay and UI default to false; kubeProxyReplacement "false". Sources: https://docs.cilium.io/en/v1.20/observability/metrics/ , https://docs.cilium.io/en/v1.20/installation/k3s/ , `helm show values cilium/cilium --version 1.20.4`.
- enableDefaultDeny: not a Helm value. It is a CiliumNetworkPolicy field, spec.enableDefaultDeny (cilium.io/v2), per `kubectl explain` on the live CRD.
- k3s flags `--flannel-backend=none --disable-network-policy --disable=traefik` (https://docs.k3s.io/cli/server , https://docs.cilium.io/en/stable/installation/k3s/); INSTALL_K3S_VERSION is read by the get.k3s.io script.
- Traefik (Sessions 2/3): accessLog.format json plus fields.headers.names User-Agent=keep gives field `request_User-Agent`; CRD group traefik.io/v1alpha1; entry points as above. Source: https://doc.traefik.io/traefik/reference/install-configuration/observability/logs-and-accesslogs/ , `helm show values traefik/traefik --version 42.4.0`.

## Blocked
<!-- Item, file, what it needed, why blocked. -->
- (resolved) Session 2 client-IP check: Docker Desktop proxied container traffic (127.0.0.1). The owner chose native Docker Engine 5:29.9.3-1~ubuntu.22.04~jammy (https://docs.docker.com/engine/install/ubuntu/), Docker Desktop WSL integration off for Ubuntu, ~/.docker/config.json credsStore removed (backup config.json.desktop-bak).
