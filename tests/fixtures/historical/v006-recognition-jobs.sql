CREATE TABLE schema_migrations (
  id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO schema_migrations (id) VALUES
  ('001_audit_log_actor'),
  ('002_product_price_and_image'),
  ('003_product_drawings'),
  ('004_product_image_slots'),
  ('005_internal_api_keys'),
  ('006_shipment_recognition_jobs');

CREATE TABLE internal_api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT 'OpenClaw',
  token_hash TEXT NOT NULL UNIQUE,
  token_prefix TEXT DEFAULT '',
  token_suffix TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_by TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT DEFAULT ''
);

CREATE TABLE shipment_recognition_jobs (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

INSERT INTO shipment_recognition_jobs (id, owner, payload, created_at, updated_at)
VALUES (
  'legacy-job-006',
  '/legacy/output::007',
  '{"id":"legacy-job-006","status":"running","phase":"recognizing","message":"旧任务","total":2,"completed":1,"percent":50}',
  '2026-07-10 12:00:00',
  '2026-07-10 12:01:00'
);
