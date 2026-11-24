from __future__ import annotations

from .bom_common import ASSEMBLY, PLATE, part, revision_time


def install_removals(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS bom_removed_links (
        sync_id TEXT PRIMARY KEY, parent TEXT NOT NULL, child TEXT NOT NULL,
        qty REAL NOT NULL CHECK(qty > 0), active INTEGER NOT NULL CHECK(active IN (0,1)),
        origin TEXT, updated_at TEXT NOT NULL, removed_at TEXT NOT NULL, removed_by TEXT NOT NULL,
        UNIQUE(parent,child), CHECK(parent <> child))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS bom_removed_sources (
        origin TEXT PRIMARY KEY, relation TEXT NOT NULL REFERENCES bom_removed_links(sync_id) DEFERRABLE INITIALLY DEFERRED,
        removed_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS bom_removed_where_used ON bom_removed_links(child)")


def remove_link(conn, data, actor):
    row = conn.execute("SELECT * FROM bom_links WHERE parent=? AND child=?",
                       (data.get("parent"), data.get("code"))).fetchone()
    if not row:
        raise ValueError("装配关系不存在或已被移除。")
    if data.get("revision") and data["revision"] != row["updated_at"]:
        raise ValueError("装配关系已变更，请重新核对。")
    removed_at = revision_time()
    values = dict(row)
    conn.execute("""INSERT INTO bom_removed_links(sync_id,parent,child,qty,active,origin,updated_at,removed_at,removed_by)
        VALUES (:sync_id,:parent,:child,:qty,:active,:origin,:updated_at,:removed_at,:removed_by)""",
        values | {"removed_at": removed_at, "removed_by": actor or "local"})
    origins = [r[0] for r in conn.execute("SELECT origin FROM bom_source_aliases WHERE relation=?", (row["sync_id"],))]
    if row["origin"]:
        origins.insert(0, row["origin"])
    conn.executemany("INSERT INTO bom_removed_sources(origin,relation,removed_at) VALUES (?,?,?)",
                     [(origin, row["sync_id"], removed_at) for origin in origins])
    conn.execute("DELETE FROM bom_source_aliases WHERE relation=?", (row["sync_id"],))
    conn.execute("DELETE FROM bom_links WHERE sync_id=?", (row["sync_id"],))


def restore_link(conn, data):
    row = conn.execute("SELECT * FROM bom_removed_links WHERE parent=? AND child=?",
                       (data.get("parent"), data.get("code"))).fetchone()
    if not row:
        raise ValueError("已移除关系不存在或已恢复。")
    if data.get("revision") and data["revision"] != row["removed_at"]:
        raise ValueError("已移除关系已变化，请刷新。")
    parent = part(conn, row["parent"])
    child = part(conn, row["child"])
    if not parent or parent["kind"] != ASSEMBLY or not child or child["kind"] == PLATE:
        raise ValueError("物料档案已变化，不能恢复此关系。")
    if conn.execute("SELECT 1 FROM bom_links WHERE parent=? AND child=?", (row["parent"], row["child"])).fetchone():
        raise ValueError("当前已有相同装配关系。")
    if conn.execute("""WITH RECURSIVE descendants(code) AS
        (SELECT child FROM bom_links WHERE parent=? UNION SELECT b.child FROM bom_links b
         JOIN descendants d ON d.code=b.parent) SELECT 1 FROM descendants WHERE code=?""",
                    (row["child"], row["parent"])).fetchone():
        raise ValueError("恢复这条关系会形成循环 BOM。")
    sources = conn.execute("SELECT origin FROM bom_removed_sources WHERE relation=?", (row["sync_id"],)).fetchall()
    if any(not conn.execute("SELECT 1 FROM material_items WHERE sync_id=?", (source["origin"],)).fetchone()
           for source in sources):
        raise ValueError("历史来源资料不完整，不能恢复。")
    restored_at = revision_time()
    conn.execute("INSERT INTO bom_links VALUES (?,?,?,?,?,?,?)",
                 (row["sync_id"], row["parent"], row["child"], row["qty"], row["active"], row["origin"], restored_at))
    conn.executemany("INSERT INTO bom_source_aliases(origin,relation,updated_at) VALUES (?,?,?)",
                     [(source["origin"], row["sync_id"], restored_at) for source in sources if source["origin"] != row["origin"]])
    conn.execute("DELETE FROM bom_removed_sources WHERE relation=?", (row["sync_id"],))
    conn.execute("DELETE FROM bom_removed_links WHERE sync_id=?", (row["sync_id"],))


def delete_part(conn, data):
    current = part(conn, data.get("code", ""))
    if not current or current["kind"] == PLATE:
        raise ValueError("请选择可删除的物料档案。")
    if data.get("revision") and data["revision"] != current["updated_at"]:
        raise ValueError("物料资料已变更，请重新打开核对。")
    code = current["code"]
    if current["kind"] == ASSEMBLY:
        referenced = conn.execute("""SELECT 1 FROM bom_links WHERE child=?
            UNION SELECT 1 FROM bom_removed_links WHERE child=?
            UNION SELECT 1 FROM bom_parts WHERE plate=?""", (code, code, code)).fetchone()
        historical = conn.execute("""SELECT 1 FROM material_items WHERE code=? COLLATE NOCASE
            UNION SELECT 1 FROM tube_items WHERE code=? COLLATE NOCASE""", (code, code)).fetchone()
    else:
        referenced = conn.execute("""SELECT 1 FROM bom_links WHERE parent=? OR child=?
            UNION SELECT 1 FROM bom_removed_links WHERE parent=? OR child=?
            UNION SELECT 1 FROM bom_parts WHERE plate=?""", (code, code, code, code, code)).fetchone()
        historical = conn.execute("""SELECT 1 FROM material_items WHERE code=? COLLATE NOCASE OR model=? COLLATE NOCASE
            UNION SELECT 1 FROM tube_items WHERE code=? COLLATE NOCASE""", (code, code, code)).fetchone()
    if referenced or historical:
        raise ValueError("此物料仍被其他母件引用或具有历史来源，不能删除；可先移除关系或停用。")
    if current["kind"] == ASSEMBLY:
        conn.execute("DELETE FROM bom_source_aliases WHERE relation IN (SELECT sync_id FROM bom_links WHERE parent=?)", (code,))
        conn.execute("DELETE FROM bom_removed_sources WHERE relation IN (SELECT sync_id FROM bom_removed_links WHERE parent=?)", (code,))
        conn.execute("DELETE FROM bom_links WHERE parent=?", (code,))
        conn.execute("DELETE FROM bom_removed_links WHERE parent=?", (code,))
        conn.execute("DELETE FROM material_items WHERE model=? COLLATE NOCASE", (code,))
    conn.execute("DELETE FROM bom_parts WHERE code=?", (code,))
