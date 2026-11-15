package faultline.actions_test

import rego.v1

import data.faultline.actions

limits := {
	"max_per_incident": 2,
	"block_min_confidence": 0.75,
	"block_min_prefix_v4": 24,
	"scale_max_replicas": 5,
}

fresh := {"actions_executed": 0, "change_on_path": true}

test_revert_allowed_for_bad_change if {
	actions.allow with input as {
		"action": {"kind": "git_revert", "target": {"commit_sha": "9f3c2a1"}},
		"verdict": {"verdict": "bad_change", "contributing_factor": null, "confidence": 0.82},
		"incident": fresh,
		"limits": limits,
	}
}

test_revert_denied_without_change_on_path if {
	not actions.allow with input as {
		"action": {"kind": "git_revert", "target": {"commit_sha": "9f3c2a1"}},
		"verdict": {"verdict": "bad_change", "contributing_factor": null, "confidence": 0.82},
		"incident": {"actions_executed": 0, "change_on_path": false},
		"limits": limits,
	}
}

test_revert_allowed_as_contributing_factor if {
	actions.allow with input as {
		"action": {"kind": "git_revert", "target": {"commit_sha": "ab12cd3"}},
		"verdict": {"verdict": "attack", "contributing_factor": "bad_change", "confidence": 0.8},
		"incident": fresh,
		"limits": limits,
	}
}

test_block_allowed if {
	actions.allow with input as {
		"action": {"kind": "block_source", "target": {"cidr": "172.18.5.0/24", "prefix_length": 24, "namespace": "ingress"}},
		"verdict": {"verdict": "attack", "contributing_factor": null, "confidence": 0.9},
		"incident": fresh,
		"limits": limits,
	}
}

test_block_denied_low_confidence if {
	not actions.allow with input as {
		"action": {"kind": "block_source", "target": {"cidr": "172.18.5.0/24", "prefix_length": 24, "namespace": "ingress"}},
		"verdict": {"verdict": "attack", "contributing_factor": null, "confidence": 0.6},
		"incident": fresh,
		"limits": limits,
	}
}

test_block_denied_wide_range if {
	not actions.allow with input as {
		"action": {"kind": "block_source", "target": {"cidr": "172.16.0.0/12", "prefix_length": 12, "namespace": "ingress"}},
		"verdict": {"verdict": "attack", "contributing_factor": null, "confidence": 0.95},
		"incident": fresh,
		"limits": limits,
	}
}

test_protected_namespace_denied if {
	not actions.allow with input as {
		"action": {"kind": "quarantine_pod", "target": {"namespace": "kube-system", "workload": "coredns"}},
		"verdict": {"verdict": "attack", "contributing_factor": null, "confidence": 0.9},
		"incident": fresh,
		"limits": limits,
	}
}

test_action_limit_denied if {
	not actions.allow with input as {
		"action": {"kind": "page_team", "target": {"team": "sre"}},
		"verdict": {"verdict": "unknown", "contributing_factor": null, "confidence": 0.3},
		"incident": {"actions_executed": 2, "change_on_path": false},
		"limits": limits,
	}
}

test_scale_denied_over_limit if {
	not actions.allow with input as {
		"action": {"kind": "scale_out", "target": {"namespace": "boutique", "workload": "frontend", "replicas": 9}},
		"verdict": {"verdict": "capacity", "contributing_factor": null, "confidence": 0.8},
		"incident": fresh,
		"limits": limits,
	}
}

test_soc_cannot_approve_revert if {
	not actions.approver_allowed with input as {
		"action": {"kind": "git_revert"},
		"approver": {"roles": ["soc"]},
	}
}

test_sre_can_approve_revert if {
	actions.approver_allowed with input as {
		"action": {"kind": "git_revert"},
		"approver": {"roles": ["sre"]},
	}
}
