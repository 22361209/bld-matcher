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
  ('006_shipment_recognition_jobs'),
  ('007_product_status'),
  ('008_internal_api_key_plaintext'),
  ('009_quote_records'),
  ('010_quote_record_bld_prices'),
  ('011_customer_price_bld_index'),
  ('012_scrub_internal_api_key_plaintext');

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

CREATE TABLE quote_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_name TEXT NOT NULL,
  bld_no TEXT DEFAULT '',
  customer_product_code TEXT DEFAULT '',
  product_model TEXT NOT NULL,
  price REAL NOT NULL,
  tax_price REAL,
  net_price REAL,
  currency TEXT NOT NULL,
  moq INTEGER,
  quote_date TEXT NOT NULL,
  quoted_by TEXT DEFAULT '',
  source_type TEXT NOT NULL DEFAULT 'manual',
  source_text TEXT DEFAULT '',
  attachment_path TEXT DEFAULT '',
  remark TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE quote_record_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_id INTEGER NOT NULL,
  changed_by TEXT DEFAULT '',
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

INSERT INTO quote_records
  (customer_name, bld_no, customer_product_code, product_model, price, tax_price, net_price,
   currency, moq, quote_date, quoted_by, source_type, source_text, attachment_path, remark,
   created_at, updated_at)
VALUES
  ('Historical Customer', 'HIST-Q-012', '', 'HIST-Q-012', 88, 88, 80, 'CNY', 10,
   '2026-01-01', 'legacy', 'manual', '', '', '', '2026-01-01 00:00:00', '2026-01-01 00:00:00');
