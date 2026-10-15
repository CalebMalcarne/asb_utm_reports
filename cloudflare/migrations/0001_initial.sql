CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1), ciphertext TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE catalog (id INTEGER PRIMARY KEY CHECK (id = 1), fields TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE reports (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, object_key TEXT NOT NULL,
  created_at TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL,
  people INTEGER NOT NULL, created_by TEXT NOT NULL
);
CREATE INDEX reports_created ON reports(created_at DESC);
CREATE TABLE schedules (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, next_run TEXT NOT NULL,
  revision TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX schedules_due ON schedules(enabled, next_run);
CREATE TABLE jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, params TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', message TEXT NOT NULL DEFAULT '',
  report_id TEXT, schedule_id TEXT, slot TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE(schedule_id, slot)
);
CREATE INDEX jobs_created ON jobs(created_at DESC);
CREATE TABLE mail_attempts (
  job_id TEXT PRIMARY KEY, status TEXT NOT NULL,
  updated_at TEXT NOT NULL, message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL DEFAULT ''
);
