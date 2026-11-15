package faultline.actions

import rego.v1

default allow := false

protected_namespaces := {
	"kube-system", "argocd", "monitoring", "security",
	"faultline", "llmops", "chaos-mesh",
}

action_roles := {
	"git_revert": {"sre"},
	"scale_out": {"sre"},
	"rate_limit": {"sre", "soc"},
	"block_source": {"soc"},
	"quarantine_pod": {"soc"},
	"page_team": {"sre", "soc", "network"},
}

allow if count(deny) == 0

deny contains "unknown action kind" if {
	not action_roles[input.action.kind]
}

deny contains "target namespace is protected" if {
	input.action.target.namespace in protected_namespaces
}

deny contains "action limit reached for this incident" if {
	input.incident.actions_executed >= input.limits.max_per_incident
}

deny contains "git_revert needs bad_change and a change on the affected path" if {
	input.action.kind == "git_revert"
	not revert_fits
}

revert_fits if {
	"bad_change" in {input.verdict.verdict, input.verdict.contributing_factor}
	input.incident.change_on_path == true
}

deny contains "block_source needs attack, enough confidence and a narrow range" if {
	input.action.kind == "block_source"
	not block_fits
}

block_fits if {
	input.verdict.verdict == "attack"
	input.verdict.confidence >= input.limits.block_min_confidence
	input.action.target.prefix_length >= input.limits.block_min_prefix_v4
}

deny contains "quarantine_pod needs an attack verdict" if {
	input.action.kind == "quarantine_pod"
	input.verdict.verdict != "attack"
}

deny contains "rate_limit needs an attack or capacity verdict" if {
	input.action.kind == "rate_limit"
	not input.verdict.verdict in {"attack", "capacity"}
}

deny contains "scale_out needs capacity and stays within the replica limit" if {
	input.action.kind == "scale_out"
	not scale_fits
}

scale_fits if {
	input.verdict.verdict == "capacity"
	input.action.target.replicas <= input.limits.scale_max_replicas
}

# Checked when an approver clicks a button.
approver_allowed if {
	some role in input.approver.roles
	role in action_roles[input.action.kind]
}
