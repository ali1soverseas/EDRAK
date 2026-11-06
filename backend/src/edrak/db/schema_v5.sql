-- Brief revisions are append-only snapshots; the original brief row stays frozen.
CREATE TABLE IF NOT EXISTS brief_revisions (
  brief_id        text NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  version         integer NOT NULL CHECK (version > 1),
  reason          text NOT NULL,
  summary         text,
  evidence_status text NOT NULL CHECK (evidence_status IN ('verified','partial','insufficient','failed')),
  document        text NOT NULL CHECK (json_valid(document)),
  document_version integer NOT NULL,
  created_at      text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (brief_id, version)
);

CREATE INDEX IF NOT EXISTS brief_revisions_created_idx
  ON brief_revisions (brief_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS brief_revisions_frozen
BEFORE UPDATE ON brief_revisions FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'a saved brief revision cannot be changed');
END;
