"""Additive schema; called only by the application's explicit migration runner."""

CORE_TABLES = (
    "development_projects", "development_suppliers", "development_sources", "development_branches",
    "development_tasks", "development_components", "development_operations",
)


def install_schema(connection):
    statements = [
        """CREATE TABLE IF NOT EXISTS development_projects (
          id TEXT PRIMARY KEY, core_number TEXT NOT NULL, title TEXT NOT NULL,
          owner_username TEXT NOT NULL DEFAULT '', planned_date TEXT NOT NULL DEFAULT '',
          actual_date TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS development_suppliers (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS development_branches (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES development_projects(id),
          code TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', side TEXT NOT NULL DEFAULT 'single',
          owner_username TEXT NOT NULL DEFAULT '', planned_date TEXT NOT NULL DEFAULT '',
          actual_date TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
          production_ready INTEGER NOT NULL DEFAULT 0 CHECK(production_ready IN (0,1)),
          drawing_required INTEGER NOT NULL DEFAULT 1 CHECK(drawing_required IN (0,1)),
          drawing_reason TEXT NOT NULL DEFAULT '', drawing_status TEXT NOT NULL DEFAULT 'not_started',
          confirmed_by TEXT NOT NULL DEFAULT '', confirmed_at TEXT NOT NULL DEFAULT '',
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS development_sources (
          id TEXT PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
          created_by TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS development_tasks (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES development_projects(id),
          name TEXT NOT NULL, owner_username TEXT NOT NULL DEFAULT '',
          planned_date TEXT NOT NULL DEFAULT '', actual_date TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'not_started',
          development_mode TEXT NOT NULL DEFAULT 'new', procurement_mode TEXT NOT NULL DEFAULT 'made',
          supplier_id TEXT REFERENCES development_suppliers(id),
          material_category TEXT NOT NULL DEFAULT '', material_grade TEXT NOT NULL DEFAULT '',
          forming_process TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
          drawing_required INTEGER NOT NULL DEFAULT 1 CHECK(drawing_required IN (0,1)),
          drawing_reason TEXT NOT NULL DEFAULT '', drawing_status TEXT NOT NULL DEFAULT 'not_started',
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS development_components (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES development_projects(id),
          branch_id TEXT NOT NULL REFERENCES development_branches(id),
          parent_id TEXT REFERENCES development_components(id) DEFERRABLE INITIALLY DEFERRED,
          task_id TEXT NOT NULL REFERENCES development_tasks(id),
          code TEXT NOT NULL DEFAULT '', borrowed_code TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
          borrowed_source_id TEXT REFERENCES development_sources(id),
          quantity REAL NOT NULL DEFAULT 1 CHECK(quantity > 0), position TEXT NOT NULL DEFAULT '',
          notes TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS development_operations (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES development_projects(id),
          task_id TEXT NOT NULL REFERENCES development_tasks(id), sequence INTEGER NOT NULL CHECK(sequence > 0),
          name TEXT NOT NULL, executor TEXT NOT NULL DEFAULT '', owner_username TEXT NOT NULL DEFAULT '',
          planned_date TEXT NOT NULL DEFAULT '', actual_date TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'not_started', notes TEXT NOT NULL DEFAULT '',
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
    ]
    for statement in statements:
        connection.execute(statement)
    for table in ("development_branches", "development_tasks", "development_components", "development_operations"):
        connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_project ON {table}(project_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_development_components_task ON development_components(task_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_development_operations_task ON development_operations(task_id)")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS development_source_immutable
      BEFORE UPDATE ON development_sources
      WHEN OLD.id IS NOT NEW.id OR OLD.code IS NOT NEW.code OR OLD.name IS NOT NEW.name
        OR OLD.created_by IS NOT NEW.created_by OR OLD.created_at IS NOT NEW.created_at
      BEGIN SELECT RAISE(ABORT, 'development source is immutable'); END""")
