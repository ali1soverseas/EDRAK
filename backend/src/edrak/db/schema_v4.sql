-- Competitor knowledge base. Every statement is IF NOT EXISTS so this file is applied to new and
-- existing databases alike, on every start (see connection._apply_additions).

-- One row per real company, shared by every workspace: public web facts are the same for everyone.
-- kb_key is the official domain ("jumia.com.eg"), or "name:<normalised name>" while no site is known.
CREATE TABLE IF NOT EXISTS competitor_kb (
  kb_key         text PRIMARY KEY,
  name           text NOT NULL,
  website_domain text,
  markets        text NOT NULL DEFAULT '[]' CHECK (json_valid(markets)),   -- markets researched so far
  status         text NOT NULL DEFAULT 'building' CHECK (status IN ('building','ready','partial','failed')),
  profile        text NOT NULL DEFAULT '{}' CHECK (json_valid(profile)),   -- verified profile, every field sourced
  page_count     integer NOT NULL DEFAULT 0,
  chunk_count    integer NOT NULL DEFAULT 0,
  error_message  text,
  built_at       text,
  created_at     text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at     text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- What one workspace asked for: a tracked competitor and the progress of its knowledge-base build.
-- This is what the "Loading competitors..." panel polls.
CREATE TABLE IF NOT EXISTS competitor_kb_jobs (
  workspace_id  text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  entity_id     text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  kb_key        text REFERENCES competitor_kb(kb_key) ON DELETE SET NULL,
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','building','ready','partial','failed')),
  stage         text,
  progress      integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  error_message text,
  updated_at    text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (workspace_id, entity_id)
);
CREATE INDEX IF NOT EXISTS competitor_kb_jobs_status_idx ON competitor_kb_jobs (status);

-- Mirror of every chunk written to the vector store, so the store can be rebuilt and inspected.
CREATE TABLE IF NOT EXISTS competitor_kb_chunks (
  id           text PRIMARY KEY,
  kb_key       text NOT NULL REFERENCES competitor_kb(kb_key) ON DELETE CASCADE,
  source_url   text NOT NULL,
  title        text,
  source_type  text NOT NULL CHECK (source_type IN ('official','third_party')),
  page_type    text,
  market       text,
  language     text,
  chunk_index  integer NOT NULL,
  content      text NOT NULL,
  content_hash text NOT NULL,
  fetched_at   text NOT NULL
);
CREATE INDEX IF NOT EXISTS competitor_kb_chunks_key_idx ON competitor_kb_chunks (kb_key, source_url);

