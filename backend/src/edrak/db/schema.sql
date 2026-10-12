-- =============================================================================
-- Edrak (إدراك) - full database schema, v0.2 (SQLite 3.35+)
--
-- Based on edrak_schema_v0.2_sqlite.sql, with these changes for this product:
--   * One user per workspace. No roles, permissions, members or invitations
--     (role_permissions, workspace_members, invitations removed; workspaces.owner_id added).
--   * Email and password only. No Google or Microsoft sign-in (auth_identities removed;
--     users.password_hash is NOT NULL and holds an argon2 hash).
--   * sessions: server-side sessions. Only the SHA-256 of the token is stored.
--   * Standard names on requests: competitors, markets, offering, target_customers.
--   * Competitor discovery and official socials: entities.discovered_by and entity_links.
--   * The second human checkpoint after verification: run_reviews, run status awaiting_review.
--   * Guardrails and evaluation: guardrail_events, run_evaluations, document guardrail columns.
--
-- Run these PRAGMAs on EVERY connection (foreign_keys is per connection and defaults to OFF):
--     PRAGMA foreign_keys = ON;      PRAGMA journal_mode = WAL;      PRAGMA busy_timeout = 5000;
--
-- How the types map
--   uuid, text ids   -> text. Default is 32 lowercase hex characters. Contract ids (uuid4 with
--                       hyphens) are stored as given.
--   timestamptz      -> text, ISO 8601 UTC ("2026-10-11T09:30:00.000Z"). Compare and sort as text.
--   boolean          -> integer 0 or 1
--   jsonb            -> text holding JSON (json_valid is checked). Use json_extract() to query.
--   text[]           -> text holding a JSON array, e.g. '["a","b"]' (json_type is checked)
--   date, time       -> text ("2026-10-11", "09:30")
--   numeric, double  -> real;   bigint, smallint -> integer
--
-- How the rules differ from PostgreSQL
--   * A trigger cannot edit the row being inserted, so brief numbers and evidence numbers (E1, E2, ...)
--     are set by an AFTER INSERT trigger that updates the new row. Insert with the default 0; do not set them.
--   * "A no_evidence result cannot carry findings" is checked immediately, not at commit. Insert the
--     worker_results row first (the documented order), then evidence and findings.
--   * A list of allowed values inside an array (allowed_source_types, monitors.focus) is checked by triggers.
--   * updated_at is refreshed by AFTER UPDATE triggers.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. IDENTITY
-- -----------------------------------------------------------------------------
CREATE TABLE users (
  id                text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  email             text NOT NULL,
  full_name         text NOT NULL,
  job_title         text,
  password_hash     text NOT NULL,              -- argon2 ("$argon2id$..."); "sha256$<hex>" only for accounts migrated from v1, upgraded at next sign-in
  avatar_url        text,
  email_verified_at text,
  last_sign_in_at   text,
  created_at        text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at        text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT users_email_shape CHECK (instr(email, '@') > 1)
);
CREATE UNIQUE INDEX users_email_uq ON users (lower(email));

-- One workspace per user, and one user per workspace.
CREATE TABLE workspaces (
  id         text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  owner_id   text NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  name       text NOT NULL,
  slug       text NOT NULL UNIQUE,
  plan       text NOT NULL DEFAULT 'trial' CHECK (plan IN ('trial','team','enterprise')),
  created_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE sessions (
  token_hash   text PRIMARY KEY,               -- sha256 of the cookie value; the token itself is never stored
  user_id      text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  expires_at   text NOT NULL,
  last_seen_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  user_agent   text
);
CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_expiry_idx ON sessions (expires_at);

-- Per-workspace counters. Used for brief numbers (0142).
CREATE TABLE workspace_counters (
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  key          text NOT NULL,
  value        integer NOT NULL DEFAULT 0,
  PRIMARY KEY (workspace_id, key)
);

-- -----------------------------------------------------------------------------
-- 2. SETTINGS
-- -----------------------------------------------------------------------------
CREATE TABLE workspace_settings (
  workspace_id               text PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  default_language           text NOT NULL DEFAULT 'en' CHECK (default_language IN ('en','ar')),
  time_zone                  text NOT NULL DEFAULT 'Africa/Cairo',
  export_format              text NOT NULL DEFAULT 'pdf' CHECK (export_format IN ('pdf','docx','markdown')),
  export_include_sources     integer NOT NULL DEFAULT 1 CHECK (export_include_sources IN (0,1)),
  export_include_limitations integer NOT NULL DEFAULT 1 CHECK (export_include_limitations IN (0,1)),
  updated_at                 text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE user_preferences (
  user_id                      text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  language                     text NOT NULL DEFAULT 'en' CHECK (language IN ('en','ar')),   -- 'ar' switches the layout to RTL
  time_zone                    text NOT NULL DEFAULT 'Africa/Cairo',
  auto_approve_scheduled_plans integer NOT NULL DEFAULT 0 CHECK (auto_approve_scheduled_plans IN (0,1)),
  notify_when_run_finishes     integer NOT NULL DEFAULT 1 CHECK (notify_when_run_finishes IN (0,1)),
  updated_at                   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE notification_preferences (
  user_id      text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  event        text NOT NULL CHECK (event IN ('plan_awaiting_approval','run_awaiting_review','run_finished','high_importance_alert','weekly_digest')),
  email        integer NOT NULL DEFAULT 1 CHECK (email IN (0,1)),
  in_app       integer NOT NULL DEFAULT 1 CHECK (in_app IN (0,1)),
  PRIMARY KEY (user_id, workspace_id, event)
);

-- -----------------------------------------------------------------------------
-- 3. WATCHLIST ENTITIES (created early: referenced by profile, monitors, briefs)
-- -----------------------------------------------------------------------------
CREATE TABLE entities (
  id                   text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id         text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind                 text NOT NULL CHECK (kind IN ('competitor','market','topic')),
  name                 text NOT NULL,
  sector               text,
  country              text,
  website_domain       text,
  summary              text,                    -- "What we know", regenerated each run
  summary_updated_at   text,
  is_tracked           integer NOT NULL DEFAULT 1 CHECK (is_tracked IN (0,1)),
  discovered_by        text NOT NULL DEFAULT 'user' CHECK (discovered_by IN ('user','agent')),
  discovery_reason     text,                    -- why the competitor agent proposed it
  discovery_confidence real CHECK (discovery_confidence BETWEEN 0 AND 1),
  tracked_since        text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  created_by           text REFERENCES users(id) ON DELETE SET NULL,
  created_at           text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at           text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX entities_name_uq ON entities (workspace_id, kind, lower(name));
CREATE INDEX entities_ws_kind_idx ON entities (workspace_id, kind) WHERE is_tracked;

-- An entity's website and official social accounts. found_on_url says where the link was read
-- (normally the entity's own homepage), so a handle is only trusted when the official site links to it.
CREATE TABLE entity_links (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  entity_id    text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  platform     text NOT NULL CHECK (platform IN ('website','facebook','instagram','x','linkedin','youtube','tiktok')),
  url          text NOT NULL,
  verified     integer NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
  found_on_url text,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT entity_links_url_shape CHECK ((url LIKE 'http://%' OR url LIKE 'https://%') AND instr(url, ' ') = 0)
);
CREATE UNIQUE INDEX entity_links_uq ON entity_links (entity_id, platform, lower(url));

-- -----------------------------------------------------------------------------
-- 4. COMPANY PROFILE (the baseline the Internal worker compares against)
-- -----------------------------------------------------------------------------
-- name, aliases, products and notes are the contract's CompanyProfile. The rest is app only.
-- When a request is submitted, the backend copies the profile into business_requests.company_profile.
CREATE TABLE company_profiles (
  workspace_id     text PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  name             text NOT NULL DEFAULT '',                    -- CompanyProfile.name (required to finish onboarding)
  aliases          text NOT NULL DEFAULT '[]' CHECK (json_valid(aliases) AND json_type(aliases) = 'array'),     -- CompanyProfile.aliases
  products         text NOT NULL DEFAULT '[]' CHECK (json_valid(products) AND json_type(products) = 'array'),   -- CompanyProfile.products ("what you offer")
  notes            text,                                      -- CompanyProfile.notes ("what the company does")
  target_customers text,                                      -- app only: who the company sells to
  industry         text,                                      -- app only
  industry_other   text,                                      -- app only
  goals            text NOT NULL DEFAULT '[]' CHECK (json_valid(goals) AND json_type(goals) = 'array'),         -- app only: strategic goals
  onboarding_step  integer NOT NULL DEFAULT 1,
  completed_at     text,
  updated_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Markets the company serves or considers, as watchlist entities of kind 'market'
CREATE TABLE company_markets (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES company_profiles(workspace_id) ON DELETE CASCADE,
  entity_id    text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  relation     text NOT NULL CHECK (relation IN ('serves','considers')),
  UNIQUE (workspace_id, entity_id)
);

-- The company's website and social accounts: one row per link, added one at a time.
CREATE TABLE company_links (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  platform     text NOT NULL CHECK (platform IN ('website','facebook','instagram','x','youtube','linkedin','tiktok')),
  url          text NOT NULL,
  position     integer NOT NULL DEFAULT 0,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT company_links_url_shape CHECK ((url LIKE 'http://%' OR url LIKE 'https://%') AND instr(url, ' ') = 0)
);
CREATE UNIQUE INDEX company_links_url_uq ON company_links (workspace_id, lower(url));
CREATE UNIQUE INDEX company_links_one_website_uq ON company_links (workspace_id) WHERE platform = 'website';
CREATE INDEX company_links_ws_idx ON company_links (workspace_id, position);

-- -----------------------------------------------------------------------------
-- 5. KNOWLEDGE AND SOURCES (what Edrak is allowed to read)
-- -----------------------------------------------------------------------------
CREATE TABLE connectors (
  id             text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id   text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind           text NOT NULL CHECK (kind IN ('postgres','file_drop','folder','workbook','other')),
  name           text NOT NULL,
  status         text NOT NULL DEFAULT 'not_connected' CHECK (status IN ('not_connected','connected','syncing','error')),
  access_mode    text NOT NULL DEFAULT 'read_only' CHECK (access_mode = 'read_only'),
  secret_ref     text,                        -- pointer into a secrets store, never the secret itself
  config         text NOT NULL DEFAULT '{}' CHECK (json_valid(config)),
  last_synced_at text,
  last_error     text,
  created_by     text REFERENCES users(id) ON DELETE SET NULL,
  created_at     text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at     text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (workspace_id, name)
);

CREATE TABLE documents (
  id               text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id     text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name             text NOT NULL,
  kind             text NOT NULL DEFAULT 'document' CHECK (kind IN ('document','table')),
  mime_type        text,
  size_bytes       integer,
  storage_uri      text NOT NULL,               -- where the original file lives (relative to the data folder)
  content_hash     text,
  origin           text NOT NULL DEFAULT 'upload' CHECK (origin IN ('upload','folder','connector')),
  connector_id     text REFERENCES connectors(id) ON DELETE SET NULL,
  status           text NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded','indexing','indexed','failed')),
  index_progress   integer CHECK (index_progress BETWEEN 0 AND 100),
  error_message    text,                        -- shown next to "Failed to parse"
  page_count       integer CHECK (page_count >= 0),
  row_count        integer CHECK (row_count >= 0),
  is_synthetic     integer NOT NULL DEFAULT 0 CHECK (is_synthetic IN (0,1)),
  guardrail_status text NOT NULL DEFAULT 'clean' CHECK (guardrail_status IN ('clean','flagged','quarantined')),
  guardrail_notes  text NOT NULL DEFAULT '[]' CHECK (json_valid(guardrail_notes) AND json_type(guardrail_notes) = 'array'),
  retrievability   real CHECK (retrievability BETWEEN 0 AND 1),   -- share of self-generated questions whose answer retrieval found in this file
  uploaded_by      text REFERENCES users(id) ON DELETE SET NULL,
  created_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  indexed_at       text,
  updated_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX documents_ws_idx ON documents (workspace_id, status);
CREATE UNIQUE INDEX documents_hash_uq ON documents (workspace_id, content_hash) WHERE content_hash IS NOT NULL;

-- One row per text chunk. The text and its embedding are also stored in ChromaDB, which does the
-- similarity search. This table keeps the readable copy so the file viewer and evidence links
-- can show the passage with an ordinary query. The Chroma record id is this row's id.
-- A chunk with excluded = 1 failed a guardrail and is never returned by retrieval.
CREATE TABLE document_chunks (
  id              text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id    text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  document_id     text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index     integer NOT NULL,
  content         text NOT NULL,
  locator         text,                          -- page number, sheet name, heading or row range
  token_count     integer,
  embedding_model text,
  flags           text NOT NULL DEFAULT '[]' CHECK (json_valid(flags) AND json_type(flags) = 'array'),
  excluded        integer NOT NULL DEFAULT 0 CHECK (excluded IN (0,1)),
  created_at      text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (document_id, chunk_index)
);
CREATE INDEX document_chunks_ws_idx ON document_chunks (workspace_id);

CREATE TABLE external_source_policies (
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_type  text NOT NULL CHECK (source_type IN ('web_search','competitor_sites','news','open_data','social_forums')),
  enabled      integer NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  config       text NOT NULL DEFAULT '{}' CHECK (json_valid(config)),
  updated_by   text REFERENCES users(id) ON DELETE SET NULL,
  updated_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (workspace_id, source_type)
);

CREATE TABLE external_source_allowlist (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_type  text NOT NULL CHECK (source_type IN ('competitor_sites','news','open_data')),
  value        text NOT NULL,                 -- domain, publisher or API identifier
  label        text,
  added_by     text REFERENCES users(id) ON DELETE SET NULL,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX external_source_allowlist_uq ON external_source_allowlist (workspace_id, source_type, lower(value));

-- -----------------------------------------------------------------------------
-- 6. MONITORS (referenced by requests)
-- -----------------------------------------------------------------------------
CREATE TABLE monitors (
  id                    text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id          text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name                  text NOT NULL,
  use_case              text NOT NULL CHECK (use_case IN ('competitive_intelligence','market_entry_expansion','product_launch')),
  goal                  text,                    -- the question each run answers; defaults from the use case
  status                text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused')),
  frequency             text NOT NULL CHECK (frequency IN ('daily','weekly','monthly','event_only')),
  day_of_week           integer CHECK (day_of_week BETWEEN 1 AND 7),          -- ISO, Monday = 1
  run_time              text,
  time_zone             text NOT NULL DEFAULT 'Africa/Cairo',
  focus                 text NOT NULL DEFAULT '[]' CHECK (json_valid(focus) AND json_type(focus) = 'array'),
  notify_email          integer NOT NULL DEFAULT 1 CHECK (notify_email IN (0,1)),
  notify_in_app         integer NOT NULL DEFAULT 1 CHECK (notify_in_app IN (0,1)),
  notify_min_importance text NOT NULL DEFAULT 'medium' CHECK (notify_min_importance IN ('low','medium','high')),
  auto_approve_plans    integer NOT NULL DEFAULT 0 CHECK (auto_approve_plans IN (0,1)),
  last_run_at           text,
  next_run_at           text,
  created_by            text REFERENCES users(id) ON DELETE SET NULL,
  created_at            text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at            text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT monitors_schedule_complete CHECK (frequency = 'event_only' OR run_time IS NOT NULL),
  CONSTRAINT monitors_weekly_has_day CHECK (frequency <> 'weekly' OR day_of_week IS NOT NULL)
);
CREATE INDEX monitors_due_idx ON monitors (next_run_at) WHERE status = 'active';

CREATE TABLE monitor_entities (
  monitor_id text NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
  entity_id  text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  PRIMARY KEY (monitor_id, entity_id)
);

CREATE TABLE monitor_triggers (
  id         text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  monitor_id text NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
  kind       text NOT NULL CHECK (kind IN ('document_uploaded','news_signal','competitor_announcement')),
  config     text NOT NULL DEFAULT '{}' CHECK (json_valid(config)),     -- folder, keywords, minimum importance
  enabled    integer NOT NULL DEFAULT 1 CHECK (enabled IN (0,1))
);

-- -----------------------------------------------------------------------------
-- 7. REQUEST, PLAN, TASKS, RUN
-- -----------------------------------------------------------------------------
-- BusinessRequest (the root of a run). The screens call it an "analysis".
-- request_id is also the research run id: runs, results and verification all point back to it.
-- BusinessContext is stored flat. targets keeps the contract field; competitors, markets, offering and
-- target_customers are the standard names the screens and workers use.
CREATE TABLE business_requests (
  request_id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))) CHECK (trim(request_id) <> ''),
  workspace_id         text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  created_by           text REFERENCES users(id) ON DELETE SET NULL,                     -- null for scheduled and event runs
  title                text NOT NULL,                                                    -- the name in the list
  goal                 text NOT NULL DEFAULT '',                                         -- BusinessRequest.goal (may be empty only while a draft)
  company_profile      text NOT NULL DEFAULT '{}' CHECK (json_valid(company_profile)),   -- BusinessRequest.company_profile, as sent
  use_case             text NOT NULL CHECK (use_case IN ('competitive_intelligence','market_entry_expansion','product_launch')),
  targets              text NOT NULL DEFAULT '[]' CHECK (json_valid(targets) AND json_type(targets) = 'array'),                 -- BusinessContext.targets (competitor names)
  competitors          text NOT NULL DEFAULT '[]' CHECK (json_valid(competitors) AND json_type(competitors) = 'array'),         -- [{name, entity_id?, website?, social_accounts[], discovered_by}]
  markets              text NOT NULL DEFAULT '[]' CHECK (json_valid(markets) AND json_type(markets) = 'array'),                 -- places, e.g. ["Egypt","Saudi Arabia"]
  offering             text,                                                             -- what is being launched or assessed
  target_customers     text,
  focus_areas          text NOT NULL DEFAULT '[]' CHECK (json_valid(focus_areas) AND json_type(focus_areas) = 'array'),         -- BusinessContext.focus_areas
  trigger              text NOT NULL DEFAULT 'on_demand' CHECK (trigger IN ('on_demand','scheduled')),
  time_window_days     integer CHECK (time_window_days > 0),
  constraints          text NOT NULL DEFAULT '[]' CHECK (json_valid(constraints) AND json_type(constraints) = 'array'),
  extras               text NOT NULL DEFAULT '{}' CHECK (json_valid(extras)),            -- BusinessRequest.extras
  form                 text NOT NULL DEFAULT '{}' CHECK (json_valid(form)),              -- the guided form as last submitted, to reopen it
  launched_by          text NOT NULL DEFAULT 'user' CHECK (launched_by IN ('user','monitor','event','follow_up')),
  monitor_id           text REFERENCES monitors(id) ON DELETE SET NULL,
  parent_request_id    text REFERENCES business_requests(request_id) ON DELETE SET NULL, -- set for "More investigation" follow-ups
  follow_up_reason     text,
  allowed_source_types text NOT NULL DEFAULT '["internal_kb","competitor_sites","web_search","news","open_data","social_forums"]' CHECK (json_valid(allowed_source_types) AND json_type(allowed_source_types) = 'array'),
  repeat_weekly        integer NOT NULL DEFAULT 0 CHECK (repeat_weekly IN (0,1)),
  notify_on_finish     integer NOT NULL DEFAULT 1 CHECK (notify_on_finish IN (0,1)),
  status               text NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','planning','awaiting_approval','running','awaiting_review','paused','completed','partial','failed','cancelled')),
  rejection_reason     text,                                                             -- why the last plan was rejected; fed back to the planner
  created_at           text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at           text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  completed_at         text,
  failure_reason       text,
  CONSTRAINT business_requests_goal_filled CHECK (status IN ('draft','planning') OR trim(goal) <> ''),
  CONSTRAINT business_requests_profile_is_object CHECK (json_type(company_profile) = 'object' AND (status IN ('draft','planning') OR json_extract(company_profile, '$.name') IS NOT NULL)),
  CONSTRAINT business_requests_extras_is_object CHECK (json_type(extras) = 'object'),
  CONSTRAINT business_requests_scheduled_has_monitor CHECK (trigger <> 'scheduled' OR monitor_id IS NOT NULL),
  CONSTRAINT business_requests_follow_up_has_parent CHECK (launched_by <> 'follow_up' OR parent_request_id IS NOT NULL)
);
CREATE INDEX business_requests_ws_idx ON business_requests (workspace_id, status, created_at DESC);
CREATE INDEX business_requests_monitor_idx ON business_requests (monitor_id, created_at DESC);

-- Clarifying questions asked during intake (Q1..Q4)
CREATE TABLE analysis_questions (
  id          text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  request_id  text NOT NULL REFERENCES business_requests(request_id) ON DELETE CASCADE,
  ordinal     integer NOT NULL,
  question    text NOT NULL,
  options     text NOT NULL DEFAULT '[]' CHECK (json_valid(options)),
  answer      text,
  answered_at text,
  skipped     integer NOT NULL DEFAULT 0 CHECK (skipped IN (0,1)),
  UNIQUE (request_id, ordinal)
);

CREATE TABLE analysis_documents (
  request_id  text NOT NULL REFERENCES business_requests(request_id) ON DELETE CASCADE,
  document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  PRIMARY KEY (request_id, document_id)
);

-- ResearchPlan. A rejected plan is rewritten, so older versions stay as superseded rows.
-- Checkpoint 1: a person approves the plan before anything runs (auto_approved = 1 when the monitor allows it).
CREATE TABLE research_plans (
  plan_id          text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))) CHECK (trim(plan_id) <> ''),
  request_id       text NOT NULL REFERENCES business_requests(request_id) ON DELETE CASCADE,
  rationale        text,
  created_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  version          integer NOT NULL DEFAULT 1,
  status           text NOT NULL DEFAULT 'awaiting_approval'
                   CHECK (status IN ('awaiting_approval','approved','rejected','superseded')),
  est_tasks        integer,
  est_workers      integer,
  est_minutes      integer,
  scope            text NOT NULL DEFAULT '{}' CHECK (json_valid(scope)),  -- quick scope edits: added competitors, time window, excluded sources
  auto_approved    integer NOT NULL DEFAULT 0 CHECK (auto_approved IN (0,1)),
  approved_by      text REFERENCES users(id) ON DELETE SET NULL,
  approved_at      text,
  rejected_by      text REFERENCES users(id) ON DELETE SET NULL,
  rejected_at      text,
  rejection_reason text,
  UNIQUE (request_id, version),
  UNIQUE (plan_id, request_id),
  CONSTRAINT research_plans_approved_has_time CHECK (status <> 'approved' OR approved_at IS NOT NULL),
  CONSTRAINT research_plans_auto_or_human CHECK (status <> 'approved' OR auto_approved OR approved_by IS NOT NULL)
);
CREATE UNIQUE INDEX research_plans_one_open_uq ON research_plans (request_id) WHERE status IN ('awaiting_approval','approved');

-- ResearchTask. The contract copies company_profile and business_context into every task; here they are
-- stored once on the request. A targeted retry keeps the task_id and raises attempt. This table also
-- carries the live state the run screen shows.
CREATE TABLE research_tasks (
  task_id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))) CHECK (trim(task_id) <> ''),
  parent_request_id text NOT NULL,
  plan_id           text NOT NULL,
  worker            text NOT NULL CHECK (worker IN ('internal_intelligence','competitor_intelligence','market_intelligence','customer_trends')),
  goal              text NOT NULL CHECK (trim(goal) <> ''),
  focus             text NOT NULL CHECK (trim(focus) <> ''),
  attempt           integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
  position          integer NOT NULL DEFAULT 0,
  enabled           integer NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),          -- the toggle on plan review
  origin            text NOT NULL DEFAULT 'plan' CHECK (origin IN ('plan','replan')),
  reason            text,
  state             text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','working','finished','failed')),
  progress          integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  sources           integer NOT NULL DEFAULT 0,
  status_line       text,
  error             text,
  created_at        text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (task_id, worker),
  UNIQUE (task_id, parent_request_id),
  FOREIGN KEY (plan_id, parent_request_id) REFERENCES research_plans (plan_id, request_id) ON DELETE CASCADE,
  CONSTRAINT research_tasks_replan_has_reason CHECK (origin <> 'replan' OR reason IS NOT NULL)
);
CREATE INDEX research_tasks_plan_idx ON research_tasks (plan_id, position);
CREATE INDEX research_tasks_request_idx ON research_tasks (parent_request_id, state);

-- The run. request_id is the run id, so there is one run per request.
CREATE TABLE runs (
  request_id            text PRIMARY KEY REFERENCES business_requests(request_id) ON DELETE CASCADE,
  plan_id               text NOT NULL,
  status                text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','awaiting_review','paused','completed','partial','failed','cancelled')),
  current_stage         text NOT NULL DEFAULT 'dispatch' CHECK (current_stage IN ('plan','dispatch','workers','verify','review','synthesize','brief','done')),
  source_counter        integer NOT NULL DEFAULT 0,     -- last evidence number handed out in this run (E1, E2, ...)
  triggered_by          text REFERENCES users(id) ON DELETE SET NULL,
  verification_state    text NOT NULL DEFAULT 'waiting' CHECK (verification_state IN ('waiting','running','done')),
  verification_progress integer NOT NULL DEFAULT 0 CHECK (verification_progress BETWEEN 0 AND 100),
  verification_activity text,
  notify_instead        integer NOT NULL DEFAULT 0 CHECK (notify_instead IN (0,1)),
  pause_requested       integer NOT NULL DEFAULT 0 CHECK (pause_requested IN (0,1)),
  error                 text,
  started_at            text,
  completed_at          text,
  created_at            text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at            text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  FOREIGN KEY (plan_id, request_id) REFERENCES research_plans (plan_id, request_id) ON DELETE RESTRICT
);
CREATE INDEX runs_active_idx ON runs (status) WHERE status IN ('queued','running','awaiting_review','paused');

-- Feeds the live run screen: the Supervisor log and the worker activity lines.
CREATE TABLE run_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id  text NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE,
  task_id     text REFERENCES research_tasks(task_id) ON DELETE SET NULL,
  occurred_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  channel     text NOT NULL CHECK (channel IN ('supervisor_log','worker_feed','verification_feed')),
  actor       text NOT NULL CHECK (actor IN ('supervisor','internal_intelligence','competitor_intelligence','market_intelligence','customer_trends','verification','synthesis','system','user')),
  kind        text NOT NULL CHECK (kind IN ('dispatch','progress','search','gap_reported','conflict','replan','failure','complete','approval','review','info')),
  message     text NOT NULL,
  callout     text CHECK (callout IS NULL OR json_valid(callout))   -- {"text": ..., "highlight": ...}
);
CREATE INDEX run_events_run_idx ON run_events (request_id, id);

-- Checkpoint 2: after the Supervisor and verification, the run pauses until a person decides.
CREATE TABLE run_reviews (
  id              text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  request_id      text NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE,
  verification_id text,                          -- verification_results.id the review is about (no FK: written in the same step)
  round           integer NOT NULL DEFAULT 1,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved')),
  payload         text NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),   -- what the person is shown: counts, gaps, conflicts, proposed tasks
  decision        text CHECK (decision IN ('continue','replan','stop')),
  edited_tasks    text NOT NULL DEFAULT '[]' CHECK (json_valid(edited_tasks) AND json_type(edited_tasks) = 'array'),
  note            text,
  auto_resolved   integer NOT NULL DEFAULT 0 CHECK (auto_resolved IN (0,1)),
  decided_by      text REFERENCES users(id) ON DELETE SET NULL,
  created_at      text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  decided_at      text,
  UNIQUE (request_id, round),
  CONSTRAINT run_reviews_resolved_has_decision CHECK (status <> 'resolved' OR decision IS NOT NULL)
);
CREATE UNIQUE INDEX run_reviews_one_pending_uq ON run_reviews (request_id) WHERE status = 'pending';

-- -----------------------------------------------------------------------------
-- 8. RESULTS: WorkerResult, Evidence, Finding, EvidenceRef, Conflict
-- -----------------------------------------------------------------------------
-- WorkerResult: one per task attempt. Written in one transaction, in this order:
-- worker_results, evidence, findings, finding_evidence, conflicts.
CREATE TABLE worker_results (
  task_id      text NOT NULL,
  attempt      integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
  worker       text NOT NULL CHECK (worker IN ('internal_intelligence','competitor_intelligence','market_intelligence','customer_trends')),
  request_id   text NOT NULL,
  status       text NOT NULL CHECK (status IN ('completed','partial','no_evidence','failed')),
  gaps         text NOT NULL DEFAULT '[]' CHECK (json_valid(gaps) AND json_type(gaps) = 'array'),
  confidence   real CHECK (confidence BETWEEN 0 AND 1),
  started_at   text,
  completed_at text,
  error        text,
  metadata     text NOT NULL DEFAULT '{}' CHECK (json_valid(metadata)),
  PRIMARY KEY (task_id, attempt),
  UNIQUE (task_id, attempt, request_id),
  FOREIGN KEY (task_id, worker) REFERENCES research_tasks (task_id, worker) ON DELETE CASCADE,
  FOREIGN KEY (task_id, request_id) REFERENCES research_tasks (task_id, parent_request_id) ON DELETE CASCADE,
  CONSTRAINT worker_results_failed_has_error CHECK (status <> 'failed' OR trim(coalesce(error, '')) <> ''),
  CONSTRAINT worker_results_metadata_is_object CHECK (json_type(metadata) = 'object')
);
CREATE INDEX worker_results_request_idx ON worker_results (request_id, worker);

-- Evidence: one extracted fact and where it came from. display_no is the E-number on the screens,
-- handed out per run by a trigger. Do not set it. uid is the stable id that app tables point at.
CREATE TABLE evidence (
  task_id        text NOT NULL,
  attempt        integer NOT NULL,
  evidence_id    text NOT NULL DEFAULT (lower(hex(randomblob(16)))) CHECK (trim(evidence_id) <> ''),
  request_id     text NOT NULL,
  uid            text NOT NULL UNIQUE DEFAULT (lower(hex(randomblob(16)))),
  display_no     integer NOT NULL DEFAULT 0,
  source_type    text NOT NULL CHECK (source_type IN
    ('web_page','search_result','official_documentation','pricing_page','release_notes','announcement','news_article',
     'review_site','market_report','regulatory','economic','internal_document','synthetic_internal','other')),
  origin         text GENERATED ALWAYS AS (CASE WHEN source_type IN ('internal_document','synthetic_internal') THEN 'internal' ELSE 'external' END) STORED,
  source_title   text,
  source_url     text,
  publisher      text,
  extracted_fact text NOT NULL CHECK (trim(extracted_fact) <> ''),
  excerpt        text,
  retrieved_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  is_synthetic   integer NOT NULL DEFAULT 0 CHECK (is_synthetic IN (0,1)),
  metadata       text NOT NULL DEFAULT '{}' CHECK (json_valid(metadata)),
  document_id    text REFERENCES documents(id) ON DELETE SET NULL,
  chunk_id       text REFERENCES document_chunks(id) ON DELETE SET NULL,
  PRIMARY KEY (task_id, attempt, evidence_id),
  UNIQUE (request_id, display_no),
  FOREIGN KEY (task_id, attempt, request_id) REFERENCES worker_results (task_id, attempt, request_id) ON DELETE CASCADE,
  CONSTRAINT evidence_synthetic_rule CHECK (source_type <> 'synthetic_internal' OR is_synthetic),
  CONSTRAINT evidence_metadata_is_object CHECK (json_type(metadata) = 'object')
);
CREATE INDEX evidence_request_idx ON evidence (request_id);
CREATE INDEX evidence_url_idx ON evidence (source_url) WHERE source_url IS NOT NULL;

CREATE TABLE findings (
  task_id     text NOT NULL,
  attempt     integer NOT NULL,
  finding_id  text NOT NULL DEFAULT (lower(hex(randomblob(16)))) CHECK (trim(finding_id) <> ''),
  request_id  text NOT NULL,
  uid         text NOT NULL UNIQUE DEFAULT (lower(hex(randomblob(16)))),
  statement   text NOT NULL CHECK (trim(statement) <> ''),
  category    text NOT NULL DEFAULT 'other' CHECK (category IN
    ('product_feature','pricing_packaging','positioning','target_customer','strength','gap','market_signal',
     'customer_sentiment','risk','opportunity','other')),
  confidence  real CHECK (confidence BETWEEN 0 AND 1),
  limitations text NOT NULL DEFAULT '[]' CHECK (json_valid(limitations) AND json_type(limitations) = 'array'),
  PRIMARY KEY (task_id, attempt, finding_id),
  FOREIGN KEY (task_id, attempt, request_id) REFERENCES worker_results (task_id, attempt, request_id) ON DELETE CASCADE
);
CREATE INDEX findings_request_idx ON findings (request_id);

CREATE TABLE finding_evidence (
  task_id     text NOT NULL,
  attempt     integer NOT NULL,
  finding_id  text NOT NULL,
  evidence_id text NOT NULL,
  relation    text NOT NULL DEFAULT 'supports' CHECK (relation IN ('supports','contradicts','contextualizes')),
  PRIMARY KEY (task_id, attempt, finding_id, evidence_id, relation),
  FOREIGN KEY (task_id, attempt, finding_id)  REFERENCES findings (task_id, attempt, finding_id) ON DELETE CASCADE,
  FOREIGN KEY (task_id, attempt, evidence_id) REFERENCES evidence (task_id, attempt, evidence_id) ON DELETE CASCADE
);
CREATE INDEX finding_evidence_evidence_idx ON finding_evidence (task_id, attempt, evidence_id);

CREATE TABLE conflicts (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id                    text NOT NULL,
  attempt                    integer NOT NULL,
  finding_id                 text NOT NULL,
  contradicting_evidence_ids text NOT NULL DEFAULT '[]' CHECK (json_valid(contradicting_evidence_ids) AND json_type(contradicting_evidence_ids) = 'array'),
  description                text NOT NULL CHECK (trim(description) <> ''),
  FOREIGN KEY (task_id, attempt, finding_id) REFERENCES findings (task_id, attempt, finding_id) ON DELETE CASCADE
);
CREATE INDEX conflicts_finding_idx ON conflicts (task_id, attempt, finding_id);

-- -----------------------------------------------------------------------------
-- 9. VERIFICATION, GAPS, ANNOTATIONS
-- -----------------------------------------------------------------------------
CREATE TABLE verification_results (
  id                    text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  request_id            text NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE,
  decision_status       text NOT NULL CHECK (decision_status IN ('verified','retry_required','replan_required','cannot_complete')),
  decision_summary      text NOT NULL CHECK (trim(decision_summary) <> ''),
  missing_information   text NOT NULL DEFAULT '[]' CHECK (json_valid(missing_information) AND json_type(missing_information) = 'array'),
  conflicts             text NOT NULL DEFAULT '[]' CHECK (json_valid(conflicts) AND json_type(conflicts) = 'array'),
  failures              text NOT NULL DEFAULT '[]' CHECK (json_valid(failures) AND json_type(failures) = 'array'),
  next_research_targets text NOT NULL DEFAULT '[]' CHECK (json_valid(next_research_targets) AND json_type(next_research_targets) = 'array'),
  metadata              text NOT NULL DEFAULT '{}' CHECK (json_valid(metadata)),
  completed_at          text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT verification_results_metadata_is_object CHECK (json_type(metadata) = 'object')
);
CREATE INDEX verification_results_request_idx ON verification_results (request_id, completed_at DESC);

CREATE TABLE verification_actions (
  verification_id text NOT NULL REFERENCES verification_results(id) ON DELETE CASCADE,
  position        integer NOT NULL,
  worker          text NOT NULL CHECK (worker IN ('internal_intelligence','competitor_intelligence','market_intelligence','customer_trends')),
  reason          text NOT NULL CHECK (trim(reason) <> ''),
  PRIMARY KEY (verification_id, position)
);

CREATE TABLE finding_verdicts (
  verification_id     text NOT NULL REFERENCES verification_results(id) ON DELETE CASCADE,
  task_id             text NOT NULL,
  attempt             integer NOT NULL,
  finding_id          text NOT NULL,
  verification_status text NOT NULL CHECK (verification_status IN ('verified','insufficient')),
  evidence_quality    text NOT NULL CHECK (evidence_quality IN ('high','medium','low')),
  evidence_ids        text NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_ids) AND json_type(evidence_ids) = 'array'),
  contradictions      text NOT NULL DEFAULT '[]' CHECK (json_valid(contradictions) AND json_type(contradictions) = 'array'),
  missing_information text NOT NULL DEFAULT '[]' CHECK (json_valid(missing_information) AND json_type(missing_information) = 'array'),
  confidence          real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  PRIMARY KEY (verification_id, task_id, attempt, finding_id),
  FOREIGN KEY (task_id, attempt, finding_id) REFERENCES findings (task_id, attempt, finding_id) ON DELETE CASCADE
);

-- Things we looked for and could not find. Always shown, never hidden.
CREATE TABLE information_gaps (
  id               text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  request_id       text NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE,
  worker           text CHECK (worker IN ('internal_intelligence','competitor_intelligence','market_intelligence','customer_trends')),
  subject          text NOT NULL,
  description      text,
  searched_note    text,
  suggested_action text NOT NULL DEFAULT 'investigate' CHECK (suggested_action IN ('investigate','add_data')),
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open','investigating','closed','accepted')),
  followed_up_by   text REFERENCES business_requests(request_id) ON DELETE SET NULL,
  created_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  closed_at        text
);
CREATE INDEX information_gaps_run_idx ON information_gaps (request_id, status);

-- "Flag as wrong", "Add note" on a finding or a piece of evidence.
CREATE TABLE annotations (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      text REFERENCES users(id) ON DELETE SET NULL,
  finding_uid  text REFERENCES findings(uid) ON DELETE CASCADE,
  evidence_uid text REFERENCES evidence(uid) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('flag_wrong','flag_low_quality','note')),
  body         text,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT annotations_one_target CHECK (((finding_uid IS NOT NULL) + (evidence_uid IS NOT NULL)) = 1)
);
CREATE INDEX annotations_finding_idx ON annotations (finding_uid) WHERE finding_uid IS NOT NULL;
CREATE INDEX annotations_evidence_idx ON annotations (evidence_uid) WHERE evidence_uid IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 10. ENTITY HISTORY (needs runs and evidence)
-- -----------------------------------------------------------------------------
CREATE TABLE entity_timeline_events (
  id          text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  entity_id   text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  occurred_on text NOT NULL,
  title       text NOT NULL,
  detail      text,
  request_id  text REFERENCES runs(request_id) ON DELETE SET NULL,
  created_at  text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX entity_timeline_idx ON entity_timeline_events (entity_id, occurred_on DESC);

CREATE TABLE entity_timeline_event_evidence (
  event_id     text NOT NULL REFERENCES entity_timeline_events(id) ON DELETE CASCADE,
  evidence_uid text NOT NULL REFERENCES evidence(uid) ON DELETE RESTRICT,
  PRIMARY KEY (event_id, evidence_uid)
);

-- Tracked series: entry plan price, search interest, sentiment, ...
CREATE TABLE entity_metric_points (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  entity_id    text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  metric       text NOT NULL,
  observed_on  text NOT NULL,
  value        real NOT NULL,
  unit         text,
  request_id   text REFERENCES runs(request_id) ON DELETE SET NULL,
  evidence_uid text REFERENCES evidence(uid) ON DELETE SET NULL,
  UNIQUE (entity_id, metric, observed_on)
);

-- -----------------------------------------------------------------------------
-- 11. BRIEFS AND DECISIONS
-- -----------------------------------------------------------------------------
-- The whole brief is one JSON document that the frontend renders directly. The contract tables
-- stay the source of truth; the document is a frozen copy of what the reader sees.
CREATE TABLE briefs (
  id               text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id     text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  request_id       text NOT NULL UNIQUE REFERENCES runs(request_id) ON DELETE RESTRICT,
  number           integer NOT NULL DEFAULT 0,    -- 142 is shown as 0142; assigned per workspace by trigger
  title            text NOT NULL,
  use_case         text NOT NULL CHECK (use_case IN ('competitive_intelligence','market_entry_expansion','product_launch')),
  language         text NOT NULL DEFAULT 'en' CHECK (language IN ('en','ar')),
  summary          text,
  evidence_status  text NOT NULL CHECK (evidence_status IN ('verified','partial','insufficient','failed')),
  lifecycle        text NOT NULL DEFAULT 'ready' CHECK (lifecycle IN ('draft','ready','reviewed','decided','superseded')),
  requested_by     text REFERENCES users(id) ON DELETE SET NULL,
  source_count     integer NOT NULL DEFAULT 0,
  worker_count     integer NOT NULL DEFAULT 0,
  document         text NOT NULL CHECK (json_valid(document)),
  document_version integer NOT NULL DEFAULT 1,
  generated_at     text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  created_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at       text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (workspace_id, number),
  CONSTRAINT briefs_document_is_object CHECK (json_type(document) = 'object')
);
CREATE INDEX briefs_ws_idx ON briefs (workspace_id, generated_at DESC);

CREATE TABLE brief_entities (
  brief_id  text NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  entity_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  PRIMARY KEY (brief_id, entity_id)
);

CREATE TABLE brief_exports (
  id                  text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  brief_id            text NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  format              text NOT NULL CHECK (format IN ('pdf','docx','markdown')),
  include_sources     integer NOT NULL DEFAULT 1 CHECK (include_sources IN (0,1)),
  include_limitations integer NOT NULL DEFAULT 1 CHECK (include_limitations IN (0,1)),
  storage_uri         text,
  exported_by         text REFERENCES users(id) ON DELETE SET NULL,
  created_at          text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE decisions (
  id            text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id  text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  brief_id      text NOT NULL REFERENCES briefs(id) ON DELETE RESTRICT,
  option_label  text CHECK (length(option_label) = 1),
  title         text NOT NULL,
  rationale     text,
  owner_id      text REFERENCES users(id) ON DELETE SET NULL,
  review_on     text,
  status        text NOT NULL DEFAULT 'decided' CHECK (status IN ('decided','superseded')),
  supersedes_id text REFERENCES decisions(id) ON DELETE SET NULL,
  recorded_by   text NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  recorded_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX decisions_one_active_uq ON decisions (brief_id) WHERE status = 'decided';
CREATE INDEX decisions_ws_idx ON decisions (workspace_id, recorded_at DESC);

CREATE TABLE decision_next_steps (
  id          text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  decision_id text NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  position    integer NOT NULL DEFAULT 0,
  body        text NOT NULL,
  owner_id    text REFERENCES users(id) ON DELETE SET NULL,
  due_on      text,
  done_at     text
);

CREATE TABLE brief_reviews (
  id                   text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  brief_id             text NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  reviewer_id          text NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  action               text NOT NULL CHECK (action IN ('accepted_as_reviewed','requested_investigation','decision_recorded','decision_undone')),
  note                 text,
  follow_up_request_id text REFERENCES business_requests(request_id) ON DELETE SET NULL,
  decision_id          text REFERENCES decisions(id) ON DELETE SET NULL,
  created_at           text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT brief_reviews_decision_link CHECK (action NOT IN ('decision_recorded','decision_undone') OR decision_id IS NOT NULL)
);
CREATE INDEX brief_reviews_brief_idx ON brief_reviews (brief_id, created_at);

-- Locked when a decision is recorded: "The brief can change later. This record cannot."
CREATE TABLE evidence_snapshots (
  id            text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  decision_id   text NOT NULL UNIQUE REFERENCES decisions(id) ON DELETE RESTRICT,
  brief_id      text NOT NULL REFERENCES briefs(id) ON DELETE RESTRICT,
  source_count  integer NOT NULL,
  worker_count  integer NOT NULL,
  brief_content text NOT NULL CHECK (json_valid(brief_content)),
  created_at    text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE evidence_snapshot_items (
  snapshot_id  text NOT NULL REFERENCES evidence_snapshots(id) ON DELETE RESTRICT,
  evidence_uid text NOT NULL REFERENCES evidence(uid) ON DELETE RESTRICT,
  PRIMARY KEY (snapshot_id, evidence_uid)
);

-- -----------------------------------------------------------------------------
-- 12. CHANGES, ALERTS, DIGESTS
-- -----------------------------------------------------------------------------
CREATE TABLE change_events (
  id                  text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id        text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  monitor_id          text REFERENCES monitors(id) ON DELETE SET NULL,
  request_id          text NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE,
  previous_request_id text REFERENCES runs(request_id) ON DELETE SET NULL,
  entity_id           text REFERENCES entities(id) ON DELETE SET NULL,
  change_type         text NOT NULL CHECK (change_type IN ('added','changed','removed')),
  importance          text NOT NULL CHECK (importance IN ('high','medium','low')),
  title               text NOT NULL,
  detail              text,
  created_at          text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX change_events_run_idx ON change_events (request_id);
CREATE INDEX change_events_monitor_idx ON change_events (monitor_id, created_at DESC);

CREATE TABLE change_event_evidence (
  change_event_id text NOT NULL REFERENCES change_events(id) ON DELETE CASCADE,
  evidence_uid    text NOT NULL REFERENCES evidence(uid) ON DELETE RESTRICT,
  PRIMARY KEY (change_event_id, evidence_uid)
);

CREATE TABLE alerts (
  id                   text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id         text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  monitor_id           text REFERENCES monitors(id) ON DELETE SET NULL,
  change_event_id      text REFERENCES change_events(id) ON DELETE SET NULL,
  brief_id             text REFERENCES briefs(id) ON DELETE SET NULL,
  kind                 text NOT NULL CHECK (kind IN ('competitor_change','signal','document_event')),
  importance           text NOT NULL CHECK (importance IN ('high','medium','low')),
  title                text NOT NULL,
  what_changed         text,
  why_it_matters       text,
  status               text NOT NULL DEFAULT 'unread'
                       CHECK (status IN ('unread','read','dismissed','escalated','recheck_started')),
  escalated_request_id text REFERENCES business_requests(request_id) ON DELETE SET NULL,
  acted_by             text REFERENCES users(id) ON DELETE SET NULL,
  acted_at             text,
  created_at           text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  read_at              text
);
CREATE INDEX alerts_feed_idx ON alerts (workspace_id, status, importance, created_at DESC);

CREATE TABLE alert_evidence (
  alert_id     text NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
  evidence_uid text NOT NULL REFERENCES evidence(uid) ON DELETE RESTRICT,
  PRIMARY KEY (alert_id, evidence_uid)
);

CREATE TABLE digests (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  week_start   text NOT NULL,
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready')),
  summary      text,
  generated_at text,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (workspace_id, week_start)
);

-- -----------------------------------------------------------------------------
-- 13. NOTIFICATIONS, ASK EDRAK, AUDIT
-- -----------------------------------------------------------------------------
CREATE TABLE notifications (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN
    ('plan_awaiting_approval','run_awaiting_review','brief_ready','alert','gaps_found','indexing_finished','run_finished','digest_ready')),
  title        text NOT NULL,
  body         text,
  request_id   text REFERENCES business_requests(request_id) ON DELETE CASCADE,
  brief_id     text REFERENCES briefs(id) ON DELETE CASCADE,
  alert_id     text REFERENCES alerts(id) ON DELETE CASCADE,
  document_id  text REFERENCES documents(id) ON DELETE CASCADE,
  read_at      text,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT notifications_one_target CHECK (((request_id IS NOT NULL) + (brief_id IS NOT NULL) + (alert_id IS NOT NULL) + (document_id IS NOT NULL)) <= 1)
);
CREATE INDEX notifications_inbox_idx ON notifications (user_id, workspace_id, created_at DESC) WHERE read_at IS NULL;

-- Ask Edrak (global panel) and the intake chat on New analysis share one model
CREATE TABLE chat_threads (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('ask_edrak','analysis_intake')),
  scope        text NOT NULL DEFAULT 'workspace' CHECK (scope IN ('workspace','brief','entity','analysis')),
  brief_id     text REFERENCES briefs(id) ON DELETE CASCADE,
  entity_id    text REFERENCES entities(id) ON DELETE CASCADE,
  request_id   text REFERENCES business_requests(request_id) ON DELETE CASCADE,
  title        text,
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CONSTRAINT chat_threads_scope_target CHECK (
    (scope = 'workspace' AND ((brief_id IS NOT NULL) + (entity_id IS NOT NULL) + (request_id IS NOT NULL)) = 0) OR
    (scope = 'brief'     AND brief_id IS NOT NULL) OR
    (scope = 'entity'    AND entity_id IS NOT NULL) OR
    (scope = 'analysis'  AND request_id IS NOT NULL)),
  CONSTRAINT chat_threads_intake_has_request CHECK (kind <> 'analysis_intake' OR request_id IS NOT NULL)
);
CREATE INDEX chat_threads_user_idx ON chat_threads (user_id, workspace_id, updated_at DESC);

CREATE TABLE chat_messages (
  id         text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  thread_id  text NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('user','assistant','system')),
  content    text NOT NULL,
  cards      text NOT NULL DEFAULT '[]' CHECK (json_valid(cards)),
  created_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX chat_messages_thread_idx ON chat_messages (thread_id, created_at);

CREATE TABLE chat_message_evidence (
  message_id   text NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  evidence_uid text NOT NULL REFERENCES evidence(uid) ON DELETE RESTRICT,
  PRIMARY KEY (message_id, evidence_uid)
);

CREATE TABLE audit_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  actor_kind   text NOT NULL CHECK (actor_kind IN ('user','agent','system')),
  actor_id     text REFERENCES users(id) ON DELETE SET NULL,
  action       text NOT NULL,                 -- 'plan.approved', 'decision.recorded', 'evidence.flagged', ...
  target_type  text NOT NULL,
  target_id    text,
  metadata     text NOT NULL DEFAULT '{}' CHECK (json_valid(metadata)),
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX audit_log_ws_idx ON audit_log (workspace_id, created_at DESC);
CREATE INDEX audit_log_target_idx ON audit_log (target_type, target_id);

-- -----------------------------------------------------------------------------
-- 14. GUARDRAILS AND EVALUATION
-- -----------------------------------------------------------------------------
-- Every guardrail decision on a question, a document, a web page or a brief.
CREATE TABLE guardrail_events (
  id           text PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  request_id   text REFERENCES business_requests(request_id) ON DELETE CASCADE,
  document_id  text REFERENCES documents(id) ON DELETE CASCADE,
  stage        text NOT NULL CHECK (stage IN ('query','document','web','output','chat')),
  check_name   text NOT NULL,                  -- 'prompt_injection', 'pii', 'scope', 'file_type', ...
  severity     text NOT NULL CHECK (severity IN ('info','low','medium','high')),
  action       text NOT NULL CHECK (action IN ('allowed','flagged','redacted','blocked')),
  detail       text NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
  created_at   text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX guardrail_events_ws_idx ON guardrail_events (workspace_id, created_at DESC);
CREATE INDEX guardrail_events_request_idx ON guardrail_events (request_id) WHERE request_id IS NOT NULL;

-- Quality scores for one run: citation coverage, groundedness, source diversity, judge scores.
CREATE TABLE run_evaluations (
  request_id   text NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE,
  metric       text NOT NULL,
  value        real NOT NULL,
  detail       text NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
  evaluated_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (request_id, metric)
);

-- -----------------------------------------------------------------------------
-- TRIGGERS AND RULES
-- -----------------------------------------------------------------------------
-- updated_at refreshes on every update
CREATE TRIGGER briefs_set_updated_at AFTER UPDATE ON briefs FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE briefs SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;
CREATE TRIGGER business_requests_set_updated_at AFTER UPDATE ON business_requests FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE business_requests SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE request_id = NEW.request_id; END;
CREATE TRIGGER chat_threads_set_updated_at AFTER UPDATE ON chat_threads FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE chat_threads SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;
CREATE TRIGGER company_profiles_set_updated_at AFTER UPDATE ON company_profiles FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE company_profiles SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE workspace_id = NEW.workspace_id; END;
CREATE TRIGGER connectors_set_updated_at AFTER UPDATE ON connectors FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE connectors SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;
CREATE TRIGGER documents_set_updated_at AFTER UPDATE ON documents FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE documents SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;
CREATE TRIGGER entities_set_updated_at AFTER UPDATE ON entities FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE entities SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;
CREATE TRIGGER external_source_policies_set_updated_at AFTER UPDATE ON external_source_policies FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE external_source_policies SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE workspace_id = NEW.workspace_id AND source_type = NEW.source_type; END;
CREATE TRIGGER monitors_set_updated_at AFTER UPDATE ON monitors FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE monitors SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;
CREATE TRIGGER runs_set_updated_at AFTER UPDATE ON runs FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE runs SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE request_id = NEW.request_id; END;
CREATE TRIGGER user_preferences_set_updated_at AFTER UPDATE ON user_preferences FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE user_preferences SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE user_id = NEW.user_id; END;
CREATE TRIGGER users_set_updated_at AFTER UPDATE ON users FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE users SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;
CREATE TRIGGER workspace_settings_set_updated_at AFTER UPDATE ON workspace_settings FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE workspace_settings SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE workspace_id = NEW.workspace_id; END;
CREATE TRIGGER workspaces_set_updated_at AFTER UPDATE ON workspaces FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN UPDATE workspaces SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = NEW.id; END;

-- Brief numbers are handed out per workspace (insert with number = 0)
CREATE TRIGGER briefs_assign_number AFTER INSERT ON briefs FOR EACH ROW WHEN NEW.number = 0
BEGIN
  INSERT INTO workspace_counters (workspace_id, key, value) VALUES (NEW.workspace_id, 'brief', 1)
    ON CONFLICT (workspace_id, key) DO UPDATE SET value = workspace_counters.value + 1;
  UPDATE briefs SET number = (SELECT value FROM workspace_counters WHERE workspace_id = NEW.workspace_id AND key = 'brief')
   WHERE id = NEW.id;
END;

-- Evidence numbers (E1, E2, ...) are handed out per run (insert with display_no = 0)
CREATE TRIGGER evidence_assign_number AFTER INSERT ON evidence FOR EACH ROW WHEN NEW.display_no = 0
BEGIN
  UPDATE runs SET source_counter = source_counter + 1 WHERE request_id = NEW.request_id;
  UPDATE evidence SET display_no = (SELECT source_counter FROM runs WHERE request_id = NEW.request_id)
   WHERE task_id = NEW.task_id AND attempt = NEW.attempt AND evidence_id = NEW.evidence_id;
END;

-- Evidence is a record of what was retrieved: its fact columns cannot be edited.
CREATE TRIGGER evidence_facts_frozen BEFORE UPDATE ON evidence FOR EACH ROW
WHEN NEW.extracted_fact IS NOT OLD.extracted_fact OR NEW.excerpt IS NOT OLD.excerpt
  OR NEW.source_url IS NOT OLD.source_url OR NEW.source_title IS NOT OLD.source_title
  OR NEW.source_type IS NOT OLD.source_type OR NEW.retrieved_at IS NOT OLD.retrieved_at
  OR NEW.request_id IS NOT OLD.request_id OR (NEW.display_no IS NOT OLD.display_no AND OLD.display_no <> 0)
BEGIN SELECT RAISE(ABORT, 'evidence facts cannot be changed'); END;

-- A run can only start from an approved plan of the same request
CREATE TRIGGER runs_require_approved_plan BEFORE INSERT ON runs FOR EACH ROW
WHEN NOT EXISTS (SELECT 1 FROM research_plans WHERE plan_id = NEW.plan_id AND request_id = NEW.request_id AND status = 'approved')
BEGIN SELECT RAISE(ABORT, 'a run needs an approved plan of the same request'); END;

-- Contract rule: the same evidence cannot both support and contradict one finding
CREATE TRIGGER finding_evidence_relation_insert BEFORE INSERT ON finding_evidence FOR EACH ROW
WHEN NEW.relation IN ('supports','contradicts') AND EXISTS (
  SELECT 1 FROM finding_evidence f
  WHERE f.task_id = NEW.task_id AND f.attempt = NEW.attempt AND f.finding_id = NEW.finding_id
    AND f.evidence_id = NEW.evidence_id AND f.relation IN ('supports','contradicts') AND f.relation <> NEW.relation)
BEGIN SELECT RAISE(ABORT, 'evidence cannot both support and contradict one finding'); END;
CREATE TRIGGER finding_evidence_relation_update BEFORE UPDATE ON finding_evidence FOR EACH ROW
WHEN NEW.relation IN ('supports','contradicts') AND EXISTS (
  SELECT 1 FROM finding_evidence f
  WHERE f.task_id = NEW.task_id AND f.attempt = NEW.attempt AND f.finding_id = NEW.finding_id
    AND f.evidence_id = NEW.evidence_id AND f.relation IN ('supports','contradicts') AND f.relation <> NEW.relation)
BEGIN SELECT RAISE(ABORT, 'evidence cannot both support and contradict one finding'); END;

-- Contract rule: a no_evidence result cannot carry findings (checked immediately; write the result row first)
CREATE TRIGGER findings_no_evidence_rule BEFORE INSERT ON findings FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM worker_results r WHERE r.task_id = NEW.task_id AND r.attempt = NEW.attempt AND r.status = 'no_evidence')
BEGIN SELECT RAISE(ABORT, 'a no_evidence result cannot carry findings'); END;
CREATE TRIGGER worker_results_no_evidence_rule BEFORE UPDATE OF status ON worker_results FOR EACH ROW
WHEN NEW.status = 'no_evidence' AND EXISTS (SELECT 1 FROM findings f WHERE f.task_id = NEW.task_id AND f.attempt = NEW.attempt)
BEGIN SELECT RAISE(ABORT, 'a no_evidence result cannot carry findings'); END;

-- Contract rules for Conflict: every contradicting evidence id must be evidence of the same result,
-- and a conflict cannot refute evidence its own finding cites as support.
CREATE TRIGGER conflicts_rule_same_result_insert BEFORE INSERT ON conflicts FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.contradicting_evidence_ids) j WHERE NOT EXISTS (
  SELECT 1 FROM evidence v WHERE v.task_id = NEW.task_id AND v.attempt = NEW.attempt AND v.evidence_id = j.value))
BEGIN SELECT RAISE(ABORT, 'conflict cites evidence that is not in the same result'); END;
CREATE TRIGGER conflicts_rule_not_support_insert BEFORE INSERT ON conflicts FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.contradicting_evidence_ids) j WHERE EXISTS (
  SELECT 1 FROM finding_evidence f WHERE f.task_id = NEW.task_id AND f.attempt = NEW.attempt
    AND f.finding_id = NEW.finding_id AND f.evidence_id = j.value AND f.relation = 'supports'))
BEGIN SELECT RAISE(ABORT, 'conflict cannot refute evidence that its finding cites as support'); END;
CREATE TRIGGER conflicts_rule_same_result_update BEFORE UPDATE ON conflicts FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.contradicting_evidence_ids) j WHERE NOT EXISTS (
  SELECT 1 FROM evidence v WHERE v.task_id = NEW.task_id AND v.attempt = NEW.attempt AND v.evidence_id = j.value))
BEGIN SELECT RAISE(ABORT, 'conflict cites evidence that is not in the same result'); END;
CREATE TRIGGER conflicts_rule_not_support_update BEFORE UPDATE ON conflicts FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.contradicting_evidence_ids) j WHERE EXISTS (
  SELECT 1 FROM finding_evidence f WHERE f.task_id = NEW.task_id AND f.attempt = NEW.attempt
    AND f.finding_id = NEW.finding_id AND f.evidence_id = j.value AND f.relation = 'supports'))
BEGIN SELECT RAISE(ABORT, 'conflict cannot refute evidence that its finding cites as support'); END;

-- A brief is a frozen record: once saved, its document and the copies of it cannot change.
CREATE TRIGGER briefs_frozen BEFORE UPDATE ON briefs FOR EACH ROW
WHEN NEW.document IS NOT OLD.document OR NEW.document_version IS NOT OLD.document_version
  OR NEW.title IS NOT OLD.title OR NEW.summary IS NOT OLD.summary
  OR NEW.evidence_status IS NOT OLD.evidence_status OR NEW.request_id IS NOT OLD.request_id
BEGIN SELECT RAISE(ABORT, 'a saved brief cannot be changed'); END;

-- Allowed values inside JSON arrays
CREATE TRIGGER business_requests_source_types_insert BEFORE INSERT ON business_requests FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.allowed_source_types) j WHERE j.value NOT IN ('internal_kb','competitor_sites','web_search','news','open_data','social_forums'))
BEGIN SELECT RAISE(ABORT, 'allowed_source_types has an unknown value'); END;
CREATE TRIGGER business_requests_source_types_update BEFORE UPDATE OF allowed_source_types ON business_requests FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.allowed_source_types) j WHERE j.value NOT IN ('internal_kb','competitor_sites','web_search','news','open_data','social_forums'))
BEGIN SELECT RAISE(ABORT, 'allowed_source_types has an unknown value'); END;
CREATE TRIGGER monitors_focus_insert BEFORE INSERT ON monitors FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.focus) j WHERE j.value NOT IN ('pricing','features','announcements','customer_perception'))
BEGIN SELECT RAISE(ABORT, 'monitors.focus has an unknown value'); END;
CREATE TRIGGER monitors_focus_update BEFORE UPDATE OF focus ON monitors FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM json_each(NEW.focus) j WHERE j.value NOT IN ('pricing','features','announcements','customer_perception'))
BEGIN SELECT RAISE(ABORT, 'monitors.focus has an unknown value'); END;

-- Decision records and the audit trail are append-only
CREATE TRIGGER evidence_snapshots_no_update BEFORE UPDATE ON evidence_snapshots FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'evidence_snapshots rows are immutable'); END;
CREATE TRIGGER evidence_snapshots_no_delete BEFORE DELETE ON evidence_snapshots FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'evidence_snapshots rows are immutable'); END;
CREATE TRIGGER evidence_snapshot_items_no_update BEFORE UPDATE ON evidence_snapshot_items FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'evidence_snapshot_items rows are immutable'); END;
CREATE TRIGGER evidence_snapshot_items_no_delete BEFORE DELETE ON evidence_snapshot_items FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'evidence_snapshot_items rows are immutable'); END;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'audit_log rows are immutable'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'audit_log rows are immutable'); END;
