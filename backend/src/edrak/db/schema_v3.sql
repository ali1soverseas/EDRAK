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

-- The action plan that follows a chosen option. Every save is a new version; an approved version is frozen.
CREATE TABLE IF NOT EXISTS action_plans (
  id            text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  brief_id      text NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  workspace_id  text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  option_key    text NOT NULL,
  version       integer NOT NULL DEFAULT 1,
  status        text NOT NULL DEFAULT 'generating' CHECK (status IN ('generating','draft','approved','superseded','failed')),
  document      text NOT NULL DEFAULT '{}' CHECK (json_valid(document)),
  note          text,
  error         text,
  created_by    text REFERENCES users(id),
  approved_by   text REFERENCES users(id),
  approved_at   text,
  supersedes_id text REFERENCES action_plans(id),
  created_at    text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (brief_id, option_key, version)
);
CREATE INDEX IF NOT EXISTS action_plans_brief_idx ON action_plans (brief_id, option_key, version);

CREATE TRIGGER IF NOT EXISTS action_plans_approved_frozen BEFORE UPDATE ON action_plans FOR EACH ROW
WHEN OLD.status = 'approved' AND (NEW.document IS NOT OLD.document OR NEW.status NOT IN ('approved','superseded'))
BEGIN SELECT RAISE(ABORT, 'an approved action plan cannot be changed'); END;

-- Keyword half of the brief's hybrid search (the vector half lives in Chroma, one collection per brief).
CREATE VIRTUAL TABLE IF NOT EXISTS brief_search USING fts5(
  brief_id UNINDEXED, kind UNINDEXED, ref_id UNINDEXED, card_version UNINDEXED, text, tokenize = 'unicode61'
);

-- A challenge: something a person typed, or a web result they chose to add, sent back through extract and verify.
CREATE TABLE IF NOT EXISTS card_challenges (
  id         text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  brief_id   text NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  card_key   text NOT NULL,
  source     text NOT NULL CHECK (source IN ('user','web')),
  text       text NOT NULL,
  url        text,
  title      text,
  outcome    text,
  created_by text REFERENCES users(id),
  created_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Company research: facts found about the company beyond the fixed profile fields, the canonical keys they are
-- filed under, and the runs that found them. A fact is a suggestion until a person approves it.
-- Categories are not a CHECK here: the list lives in edrak.company_research.categories and can grow without
-- rebuilding the table. fact_keys is seeded from edrak.company_research.seed_keys by connection._apply_additions.
CREATE TABLE IF NOT EXISTS research_runs (
  id            text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id  text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  started_at    text,
  finished_at   text,
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','cancelled')),
  input         text NOT NULL DEFAULT '{}' CHECK (json_valid(input) AND json_type(input) = 'object'),
  pages_crawled integer NOT NULL DEFAULT 0 CHECK (pages_crawled >= 0),
  pdfs_read     integer NOT NULL DEFAULT 0 CHECK (pdfs_read >= 0),
  facts_found   integer NOT NULL DEFAULT 0 CHECK (facts_found >= 0),
  warnings      text NOT NULL DEFAULT '[]' CHECK (json_valid(warnings) AND json_type(warnings) = 'array'),
  error         text,
  created_at    text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  stage         text,                          -- the graph node the run is in, for the screen to poll
  stats         text NOT NULL DEFAULT '{}' CHECK (json_valid(stats) AND json_type(stats) = 'object')
);
CREATE INDEX IF NOT EXISTS research_runs_ws_idx ON research_runs (workspace_id, created_at DESC);
-- One company per workspace, so one active run per workspace. The database enforces it, whatever process asks.
CREATE UNIQUE INDEX IF NOT EXISTS research_runs_one_active_idx ON research_runs (workspace_id) WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS company_facts (
  id                text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id      text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  category          text NOT NULL,
  proposed_category text,                      -- only for category 'other': the name the model proposed, for a person to promote
  key               text NOT NULL CHECK (key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_]*'),
  value             text NOT NULL,             -- for value_type 'list', a JSON array of strings
  value_type        text NOT NULL DEFAULT 'text' CHECK (value_type IN ('text','number','date','url','list')),
  source_tier       text NOT NULL CHECK (source_tier IN ('own_site','own_pdf','own_social','third_party')),
  source_url        text NOT NULL,
  pdf_page          integer CHECK (pdf_page >= 1),
  quote             text NOT NULL,             -- the exact text on the source that supports the value
  observed_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  confidence        text NOT NULL CHECK (confidence IN ('high','medium','low')),
  status            text NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','approved','rejected')),
  conflict_group    text,                      -- shared by facts that disagree
  run_id            text REFERENCES research_runs(id) ON DELETE SET NULL,
  created_at        text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at        text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  edited_by_user    integer NOT NULL DEFAULT 0 CHECK (edited_by_user IN (0,1)),
  CONSTRAINT company_facts_other_names_category CHECK (category <> 'other' OR proposed_category IS NOT NULL),
  CONSTRAINT company_facts_list_is_array CHECK (value_type <> 'list' OR (json_valid(value) AND json_type(value) = 'array')),
  CONSTRAINT company_facts_source_url_shape CHECK ((source_url LIKE 'http://%' OR source_url LIKE 'https://%') AND instr(source_url, ' ') = 0)
);
CREATE INDEX IF NOT EXISTS company_facts_ws_category_idx ON company_facts (workspace_id, category, key);
CREATE INDEX IF NOT EXISTS company_facts_ws_status_idx ON company_facts (workspace_id, status);
CREATE INDEX IF NOT EXISTS company_facts_run_idx ON company_facts (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS company_facts_conflict_idx ON company_facts (workspace_id, conflict_group) WHERE conflict_group IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS company_facts_set_updated_at AFTER UPDATE ON company_facts FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE company_facts SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;

-- The canonical keys a fact is filed under. Global, not per workspace. synonyms is {"en": [...], "ar": [...]}.
CREATE TABLE IF NOT EXISTS fact_keys (
  id         text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  category   text NOT NULL,
  key        text NOT NULL CHECK (key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_]*'),
  label_en   text NOT NULL,
  label_ar   text NOT NULL,
  synonyms   text NOT NULL DEFAULT '{"en":[],"ar":[]}' CHECK (json_valid(synonyms) AND json_type(synonyms) = 'object'),
  value_type text NOT NULL DEFAULT 'text' CHECK (value_type IN ('text','number','date','url','list')),
  created_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  origin     text NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','promoted')),
  UNIQUE (category, key)
);
