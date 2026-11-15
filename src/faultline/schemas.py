"""Data contracts for every Faultline boundary (BUILD_SPEC: Data contracts)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Pack = Literal["change", "traffic", "security", "health"]
HypothesisType = Literal["attack", "network_fault", "bad_change", "capacity"]
VerdictType = Literal["attack", "network_fault", "bad_change", "capacity", "unknown"]
ActionKind = Literal["git_revert", "rate_limit", "block_source",
                     "quarantine_pod", "scale_out", "page_team"]
Severity = Literal["low", "medium", "high", "critical"]
ToolName = Literal["get_service_health", "get_traffic_profile", "get_network_signals",
                   "get_security_alerts", "get_changes", "get_change_diff",
                   "search_logs", "find_similar_incidents", "get_runbook"]


# ---- Alertmanager webhook (format version 4) ----
class AMAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = {}
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = ""
    fingerprint: str

class AMWebhook(BaseModel):
    version: str
    groupKey: str
    status: Literal["firing", "resolved"]
    receiver: str
    groupLabels: dict[str, str] = {}
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL: str = ""
    alerts: list[AMAlert]


# ---- Evidence ----
class EvidenceItem(BaseModel):
    id: str = Field(pattern=r"^(chg|trf|sec|hlt)-\d+$")
    pack: Pack
    summary: str = Field(max_length=200)
    data: dict
    source: str

class EvidencePacks(BaseModel):
    change: list[EvidenceItem] = []
    traffic: list[EvidenceItem] = []
    security: list[EvidenceItem] = []
    health: list[EvidenceItem] = []
    dropped_counts: dict[str, int] = {}   # items cut by top-N limits, per pack


# ---- LLM outputs ----
class TriageResult(BaseModel):
    severity: Severity
    duplicate_of: UUID | None = None
    ranked_hypotheses: list[HypothesisType] = Field(min_length=4, max_length=4)
    plan: list[ToolName] = Field(max_length=6)
    notes: str = Field(max_length=500)

class Hypothesis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: HypothesisType
    score: float = Field(ge=0, le=1)
    for_: list[str] = Field(default_factory=list, alias="for")
    against: list[str] = Field(default_factory=list)

class ActionTarget(BaseModel):
    commit_sha: str | None = None     # git_revert
    route: str | None = None          # rate_limit, e.g. "/cart/checkout"
    cidr: str | None = None           # block_source
    namespace: str | None = None
    workload: str | None = None       # quarantine_pod, scale_out
    replicas: int | None = None       # scale_out
    team: str | None = None           # page_team

class ProposedAction(BaseModel):
    kind: ActionKind
    target: ActionTarget
    reason: str = Field(max_length=300)

class Verdict(BaseModel):
    hypotheses: list[Hypothesis] = Field(min_length=4, max_length=4)
    verdict: VerdictType
    contributing_factor: HypothesisType | None = None
    explanation: str = Field(max_length=800)
    flip_conditions: list[str] = Field(max_length=5)
    action: ProposedAction | None = None
    confidence: float = Field(ge=0, le=1)

class Report(BaseModel):
    summary: str
    timeline: list[str]
    root_cause: str
    impact: str
    action_taken: str
    follow_ups: list[str]


# ---- Internal records ----
class VerifierResult(BaseModel):
    passed: bool
    errors: list[str] = []

class PolicyDecision(BaseModel):
    allow: bool
    deny_reasons: list[str] = []

class Approval(BaseModel):
    slack_user_id: str
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    decided_at: datetime

class RecoveryResult(BaseModel):
    recovered: bool
    checked_from: datetime
    checked_to: datetime
    metrics: dict
