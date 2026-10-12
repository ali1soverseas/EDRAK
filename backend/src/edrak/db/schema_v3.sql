-- Brief v2 additions. Every statement is IF NOT EXISTS so this file is applied to new and existing databases alike.

CREATE TABLE IF NOT EXISTS metrics (
  uid            text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  request_id     text NOT NULL,
  metric_id      text,
  entity         text NOT NULL,
  entity_id      text REFERENCES entities(id) ON DELETE SET NULL,
  value          real,
  value_text     text NOT NULL,
  unit           text,
  period         text,
  definition     text,
  evidence_uid   text REFERENCES evidence(uid) ON DELETE SET NULL,
  source_span    text,
  scope          text NOT NULL DEFAULT 'third_party'
                   CHECK (scope IN ('official','third_party','internal','public_proxy_for_internal')),
  sample_size    integer,
  label          text,
  status         text NOT NULL DEFAULT 'unverified' CHECK (status IN ('verified','thin','conflict','unverified')),
  derived        integer NOT NULL DEFAULT 0 CHECK (derived IN (0,1)),
  formula        text,
  inputs         text NOT NULL DEFAULT '[]' CHECK (json_valid(inputs)),
  worker         text,
  method         text NOT NULL DEFAULT 'worker' CHECK (method IN ('worker','extract','derived','challenge')),
  created_at     text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS metrics_request_idx ON metrics (request_id, metric_id, entity);

CREATE TABLE IF NOT EXISTS metric_conflicts (
  id          integer PRIMARY KEY AUTOINCREMENT,
  request_id  text NOT NULL,
  metric_id   text,
  entity      text NOT NULL,
  period      text,
  low         real NOT NULL,
  high        real NOT NULL,
  metric_uids text NOT NULL CHECK (json_valid(metric_uids))
);
CREATE INDEX IF NOT EXISTS metric_conflicts_request_idx ON metric_conflicts (request_id);

CREATE TABLE IF NOT EXISTS requirement_outcomes (
  request_id text NOT NULL,
  task_id    text NOT NULL,
  metric_id  text NOT NULL,
  entity     text NOT NULL,
  status     text NOT NULL CHECK (status IN ('found','retry','gap')),
  reason     text CHECK (reason IN ('not_found','not_publicly_disclosed','only_third_party_source','below_min_sample','conflict')),
  attempts   integer NOT NULL DEFAULT 1,
  required   integer NOT NULL DEFAULT 1,
  updated_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (request_id, task_id, metric_id, entity)
);

-- Live versions of the cards on a brief. The brief document is frozen; a challenge adds a new version here.
CREATE TABLE IF NOT EXISTS brief_cards (
  id            text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  brief_id      text NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  card_key      text NOT NULL,
  version       integer NOT NULL DEFAULT 1,
  section       text,
  component     text NOT NULL,
  headline      text,
  why_visual    text,
  slots         text NOT NULL DEFAULT '{}' CHECK (json_valid(slots)),
  labels        text NOT NULL DEFAULT '{}' CHECK (json_valid(labels)),
  status        text,
  origin        text NOT NULL DEFAULT 'pipeline' CHECK (origin IN ('pipeline','challenge')),
  supersedes_id text REFERENCES brief_cards(id),
  note          text,
  created_by    text,
  created_at    text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (brief_id, card_key, version)
);
CREATE INDEX IF NOT EXISTS brief_cards_brief_idx ON brief_cards (brief_id, card_key);
