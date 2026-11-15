CREATE TABLE incident_memory (
  incident_id     uuid PRIMARY KEY REFERENCES incidents(id),
  summary         text NOT NULL,
  verdict         text NOT NULL,
  action_kind     text,
  outcome         text,
  embedding       vector({{EMBED_DIM}}) NOT NULL,
  embedding_model text NOT NULL,
  tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', summary)) STORED,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX incident_memory_tsv_idx ON incident_memory USING gin (tsv);

CREATE TABLE runbook_chunks (
  id              bigserial PRIMARY KEY,
  path            text NOT NULL,
  heading         text NOT NULL,
  content         text NOT NULL,
  content_hash    text NOT NULL,
  embedding       vector({{EMBED_DIM}}) NOT NULL,
  embedding_model text NOT NULL,
  tsv             tsvector GENERATED ALWAYS AS
                    (to_tsvector('english', heading || ' ' || content)) STORED,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (path, heading)
);
CREATE INDEX runbook_chunks_tsv_idx ON runbook_chunks USING gin (tsv);
