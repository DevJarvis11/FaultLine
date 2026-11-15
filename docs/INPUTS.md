# Inputs (non-secret)

Fill in every blank before Session 1. Secrets (API keys, tokens, private keys)
never go here: put them only in `.env` on the VM, which is git-ignored.
Claude Code reads this file and stops to ask if a value it needs is blank.

## Machine (WSL2)
- Laptop total RAM (GB): 15.7
- Laptop CPU cores: 32 logical processors
- Free disk (GB): 867 (free space on the WSL2 disk)
- Windows version (winver): Windows 11 Pro, build 26300
- WSL2 memory limit set in .wslconfig (GB): 10 (free -h shows 10Gi total)
- WSL2 processors set in .wslconfig: 32 (nproc output)
- WSL2 distro: Ubuntu 22.04 (installed; fine for this project), systemd enabled: yes
- uname -r output: 6.6.114.1-microsoft-standard-WSL2
- Kernel gate result (pass or fail; paste the 12-option check output below): all 12 options present (9 built in, 3 as modules: CRYPTO_USER_API_HASH, NET_CLS_BPF, NET_SCH_INGRESS), BTF present, Docker works; module load test pending
- Claude Code runs: inside the same WSL2 instance as the lab

## GitHub
- Owner: DevJarvis11
- App repository: DevJarvis11/FaultLine
- GitOps repository: DevJarvis11/faultline-gitops
- Repository visibility (public or private): private
- GitHub App ID:
- GitHub App installation ID:
- GitHub App private key path on the VM:

## Model server (values in config/models.yaml; the URL and the API key go only in .env, never here)
- OpenAI-compatible API (yes or no): yes (LiteLLM gateway, API key required)
- Triage model: gpt-oss-120b (reasoning_effort low)
- Investigator model: gpt-oss-120b (reasoning_effort medium)
- Investigator fallback model: none (no second chat model)
- Report model: gpt-oss-120b (reasoning_effort low)
- Embedding model: qwen3-embed
- Embedding dimension: 2560

## Slack
- Workspace name:
- Incident channel ID:
- Approvers (Slack user ID: roles from sre, soc, network):
  - :

## Lab
- Lab hostname for the shop:
