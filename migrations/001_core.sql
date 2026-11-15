CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE schema_migrations (
  version    text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE incident_number_seq;

CREATE TABLE incidents (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_id          text NOT NULL UNIQUE
                      DEFAULT 'INC-' || lpad(nextval('incident_number_seq')::text, 4, '0'),
  incident_key        text NOT NULL,   -- alert name + service + namespace
  status              text NOT NULL CHECK (status IN ('open', 'investigating',
                        'awaiting_approval', 'acting', 'watching', 'escalated',
                        'resolved', 'closed', 'duplicate')),
  mode                text NOT NULL CHECK (mode IN ('normal', 'shadow', 'degraded', 'eval')),
  severity            text CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  service             text NOT NULL,
  namespace           text NOT NULL,
  verdict             text CHECK (verdict IN ('attack', 'network_fault', 'bad_change',
                        'capacity', 'unknown')),
  contributing_factor text CHECK (contributing_factor IN ('attack', 'network_fault',
                        'bad_change', 'capacity')),
  confidence          numeric(4,3) CHECK (confidence BETWEEN 0 AND 1),
  duplicate_of        uuid REFERENCES incidents(id),
  thread_id           text NOT NULL UNIQUE,
  opened_at           timestamptz NOT NULL DEFAULT now(),
  resolved_at         timestamptz,
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX incidents_one_active_per_key ON incidents (incident_key)
  WHERE status NOT IN ('resolved', 'closed', 'duplicate');

CREATE TABLE alerts (
  id          bigserial PRIMARY KEY,
  incident_id uuid NOT NULL REFERENCES incidents(id),
  fingerprint text NOT NULL,
  starts_at   timestamptz NOT NULL,
  status      text NOT NULL CHECK (status IN ('firing', 'resolved')),
  payload     jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (fingerprint, starts_at, status)
);

CREATE TABLE evidence_items (
  id           bigserial PRIMARY KEY,
  incident_id  uuid NOT NULL REFERENCES incidents(id),
  item_id      text NOT NULL CHECK (item_id ~ '^(chg|trf|sec|hlt)-[0-9]+$'),
  pack         text NOT NULL CHECK (pack IN ('change', 'traffic', 'security', 'health')),
  summary      text NOT NULL,
  data         jsonb NOT NULL,
  source       text NOT NULL,
  collected_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (incident_id, item_id)
);

CREATE TABLE verdicts (
  id              bigserial PRIMARY KEY,
  incident_id     uuid NOT NULL REFERENCES incidents(id),
  cycle           integer NOT NULL DEFAULT 1 CHECK (cycle >= 1),
  attempt         integer NOT NULL CHECK (attempt >= 1),
  triage          jsonb,
  verdict         jsonb NOT NULL,
  verifier_passed boolean NOT NULL,
  verifier_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (incident_id, cycle, attempt)
);

CREATE TABLE llm_calls (
  id                bigserial PRIMARY KEY,
  incident_id       uuid REFERENCES incidents(id),   -- null for startup checks
  role              text NOT NULL CHECK (role IN ('triage', 'investigator', 'report',
                      'embedding', 'check')),
  model             text NOT NULL,
  prompt_version    text,
  input_tokens      integer,
  output_tokens     integer,
  latency_ms        integer,
  status            text NOT NULL CHECK (status IN ('ok', 'error', 'timeout', 'fallback')),
  error             text,
  request           jsonb,           -- redacted messages sent
  response          jsonb,           -- redacted model output
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_incident_idx ON llm_calls (incident_id);

CREATE TABLE actions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id         uuid NOT NULL REFERENCES incidents(id),
  verdict_id          bigint REFERENCES verdicts(id),
  action_key          text NOT NULL UNIQUE,   -- incident + kind + target
  kind                text NOT NULL CHECK (kind IN ('git_revert', 'rate_limit',
                        'block_source', 'quarantine_pod', 'scale_out', 'page_team',
                        'expire_cleanup')),
  target              jsonb NOT NULL,
  status              text NOT NULL CHECK (status IN ('proposed', 'policy_denied',
                        'awaiting_approval', 'approved', 'rejected', 'expired',
                        'executing', 'executed', 'failed', 'restored')),
  opa_decision        jsonb,
  pr_url              text,
  merge_sha           text,
  argocd_sync         jsonb,
  approval_expires_at timestamptz,
  rule_expires_at     timestamptz,             -- blocks, quarantines, rate limits
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX actions_rule_expiry_idx ON actions (rule_expires_at)
  WHERE status = 'executed';

CREATE TABLE approvals (
  id            bigserial PRIMARY KEY,
  action_id     uuid NOT NULL UNIQUE REFERENCES actions(id),
  slack_user_id text NOT NULL,
  roles         text[] NOT NULL,
  decision      text NOT NULL CHECK (decision IN ('approved', 'rejected')),
  reason        text,
  decided_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (decision = 'approved' OR reason IS NOT NULL)
);

CREATE TABLE recovery_checks (
  id         bigserial PRIMARY KEY,
  action_id  uuid NOT NULL UNIQUE REFERENCES actions(id),
  started_at timestamptz NOT NULL,
  ended_at   timestamptz,
  recovered  boolean,
  metrics    jsonb
);

CREATE TABLE reports (
  incident_id uuid PRIMARY KEY REFERENCES incidents(id),
  content     jsonb NOT NULL,   -- Report schema
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
  id          bigserial PRIMARY KEY,
  incident_id uuid REFERENCES incidents(id),
  event_type  text NOT NULL,
  actor       text NOT NULL,   -- 'system', 'llm:<role>' or 'human:<slack id>'
  details     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_incident_idx ON audit_events (incident_id, created_at);

CREATE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only';
END;
$$;

CREATE TRIGGER audit_events_no_change
  BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
