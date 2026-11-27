from __future__ import annotations

from .bom_common import PLATE, link_id, normalize_plate_code


def migrate_plate_codes(conn) -> int:
    rows = conn.execute("SELECT code FROM bom_parts WHERE kind=? AND code LIKE '板料/%'", (PLATE,)).fetchall()
    renames = [(row["code"], normalize_plate_code(row["code"])) for row in rows]
    for old, new in renames:
        if conn.execute("SELECT 1 FROM bom_parts WHERE code=?", (new,)).fetchone():
            raise ValueError(f"板料编码 {new} 已被其他物料使用，无法移除旧前缀。")
    for old, new in renames:
        conn.execute("UPDATE bom_parts SET plate=? WHERE plate=?", (new, old))
        for table, alias_table in (("bom_links", "bom_source_aliases"), ("bom_removed_links", "bom_removed_sources")):
            relations = {}
            for row in conn.execute(f"SELECT sync_id,parent,child FROM {table} WHERE parent=? OR child=?", (old, old)):
                parent = normalize_plate_code(row["parent"])
                child = normalize_plate_code(row["child"])
                relation = link_id(parent, child)
                relations[row["sync_id"]] = relation
                conn.execute(f"UPDATE {table} SET sync_id=?,parent=?,child=? WHERE sync_id=?",
                             (relation, parent, child, row["sync_id"]))
            for previous, relation in relations.items():
                conn.execute(f"UPDATE {alias_table} SET relation=? WHERE relation=?", (relation, previous))
        conn.execute("UPDATE bom_parts SET code=? WHERE code=?", (new, old))
    return len(renames)


def normalize_package_plate_codes(payload) -> None:
    for row in payload.get("bom_parts", []):
        if row.get("kind") == PLATE:
            row["code"] = normalize_plate_code(row.get("code"))
        if row.get("plate"):
            row["plate"] = normalize_plate_code(row["plate"])
    for table, alias_table in (("bom_links", "bom_source_aliases"), ("bom_removed_links", "bom_removed_sources")):
        relations = {}
        for row in payload.get(table, []):
            parent = normalize_plate_code(row.get("parent"))
            child = normalize_plate_code(row.get("child"))
            if parent != row.get("parent") or child != row.get("child"):
                previous = row["sync_id"]
                row.update(parent=parent, child=child, sync_id=link_id(parent, child))
                relations[previous] = row["sync_id"]
        for row in payload.get(alias_table, []):
            if row.get("relation") in relations:
                row["relation"] = relations[row["relation"]]
