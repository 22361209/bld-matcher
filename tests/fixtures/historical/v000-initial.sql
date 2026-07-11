CREATE TABLE products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bld_no TEXT NOT NULL UNIQUE,
  series TEXT DEFAULT '',
  item TEXT DEFAULT '',
  oe_no_1 TEXT DEFAULT '',
  oe_no_2 TEXT DEFAULT '',
  models TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  source TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

INSERT INTO products
  (bld_no, series, item, oe_no_1, oe_no_2, models, active, source, created_at, updated_at)
VALUES
  ('HIST-000', 'LEGACY', 'Initial Product', 'OLD-OE', '', 'Legacy Car', 1, 'fixture',
   '2024-01-01 00:00:00', '2024-01-01 00:00:00');

CREATE TABLE aliases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_code TEXT NOT NULL UNIQUE,
  bld_no TEXT NOT NULL,
  note TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_key TEXT NOT NULL,
  detail TEXT DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT DEFAULT '',
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
