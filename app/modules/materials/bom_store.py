"""Shared material masters, blank stock and assembly relationships."""
from __future__ import annotations

from collections import Counter, defaultdict

from .bom_common import (
    ASSEMBLY, KINDS, PART_FIELDS, PLATE, STAMP, TUBE_KINDS, link_id, part, plate_code, positive, revision_time,
)
from .bom_removal import delete_part, install_removals, remove_link, restore_link
from .domain import material_grade_for_code
from .specification import _parse_material_spec_text
from .weights import validated_net_weight, validated_part_weight


class ExistingBomLink(ValueError):
    pass


def install(conn):
    conn.execute("""CREATE TABLE bom_parts (
        code TEXT PRIMARY KEY COLLATE NOCASE, name TEXT NOT NULL, model TEXT, kind TEXT NOT NULL,
        plate TEXT REFERENCES bom_parts(code) DEFERRABLE INITIALLY DEFERRED,
        pieces REAL CHECK(pieces > 0), net_weight TEXT, grade TEXT,
        thickness REAL, width REAL, length REAL, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE bom_links (
        sync_id TEXT PRIMARY KEY, parent TEXT NOT NULL REFERENCES bom_parts(code) DEFERRABLE INITIALLY DEFERRED,
        child TEXT NOT NULL REFERENCES bom_parts(code) DEFERRABLE INITIALLY DEFERRED,
        qty REAL NOT NULL CHECK(qty > 0), active INTEGER NOT NULL CHECK(active IN (0,1)),
        origin TEXT, updated_at TEXT NOT NULL, UNIQUE(parent,child), CHECK(parent <> child))""")
    conn.execute("CREATE UNIQUE INDEX bom_source ON bom_links(origin) WHERE origin IS NOT NULL")
    conn.execute("CREATE INDEX bom_where_used ON bom_links(child)")
    conn.execute("""CREATE TABLE bom_source_aliases (
        origin TEXT PRIMARY KEY, relation TEXT NOT NULL REFERENCES bom_links(sync_id) DEFERRABLE INITIALLY DEFERRED,
        updated_at TEXT NOT NULL)""")
    install_removals(conn)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(material_items)")}
    if {"model", "code", "part", "pieces", "width", "length", "thickness", "sync_id",
        "active", "material_grade", "net_weight"} <= columns:
        migrate_rows(conn)


def save_part(conn, values):
    conn.execute("""INSERT INTO bom_parts(code,name,model,kind,plate,pieces,net_weight,grade,thickness,width,length,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,model=excluded.model,
        plate=excluded.plate,pieces=excluded.pieces,net_weight=excluded.net_weight,updated_at=excluded.updated_at""",
        [*(values.get(k) for k in PART_FIELDS), revision_time()])


def stock(conn, grade, dimensions):
    dims = [positive(v, "板料尺寸") for v in dimensions]
    row = dict(zip(("thickness", "width", "length"), dims, strict=True))
    code = plate_code(grade, row)
    if not part(conn, code):
        save_part(conn, {**row, "code": code, "name": grade, "grade": grade, "kind": PLATE})
    return code


def add_link(conn, parent, child, qty: float = 1, active=1, origin=None):
    if conn.execute("SELECT 1 FROM bom_links WHERE parent=? AND child=?", (parent, child)).fetchone():
        raise ExistingBomLink("该母件已有此子件，可在原关系上修改用量或启用。")
    if conn.execute("SELECT 1 FROM bom_removed_links WHERE parent=? AND child=?", (parent, child)).fetchone():
        raise ValueError("这条关系已移除，请在“已移除”中恢复。")
    if parent == child or conn.execute("""WITH RECURSIVE descendants(code) AS
        (SELECT child FROM bom_links WHERE parent=? UNION SELECT b.child FROM bom_links b
         JOIN descendants d ON d.code=b.parent) SELECT 1 FROM descendants WHERE code=?""", (child, parent)).fetchone():
        raise ValueError("这条关系会形成循环 BOM。")
    conn.execute("INSERT INTO bom_links VALUES (?,?,?,?,?,?,?)",
                 (link_id(parent, child), parent, child, positive(qty, "装配用量"), active, origin, revision_time()))


def pending_rows(conn):
    return [dict(r) for r in conn.execute("""SELECT m.* FROM material_items m
        LEFT JOIN bom_links b ON b.origin=m.sync_id LEFT JOIN bom_source_aliases a ON a.origin=m.sync_id
        LEFT JOIN bom_removed_sources r ON r.origin=m.sync_id
        WHERE b.sync_id IS NULL AND a.origin IS NULL AND r.origin IS NULL ORDER BY m.model,m.code,m.id""")]


def migrate_rows(conn):
    """Keep ambiguous source rows intact, including their old production quantities."""
    pending = pending_rows(conn)
    groups = defaultdict(list)
    for row in pending:
        groups[row["code"]].append(row)
    parents = {str(r["model"] or "").casefold() for r in pending}
    spellings = Counter(str(c).casefold() for c in groups)
    for code, rows in groups.items():
        signatures = {(r["part"], r["thickness"], r["width"], r["length"], r["pieces"], r["material_grade"]) for r in rows}
        weights = {r["net_weight"] for r in rows if r["net_weight"]}
        models = [r["model"] for r in rows]
        if (not code or any(not m for m in models) or not rows[0]["part"] or code.casefold() in parents or len(signatures) != 1
                or len(weights) > 1
                or spellings[code.casefold()] > 1 or any(not r["sync_id"] for r in rows)):
            continue
        counts = Counter(m.casefold() for m in models)
        rows = [r for r in rows if counts[r["model"].casefold()] == 1]
        if not rows:
            continue
        models = [r["model"] for r in rows]
        r = rows[0]
        existing = part(conn, code)
        if any((parent := part(conn, m)) and (parent["kind"] != ASSEMBLY or parent["code"] != m) for m in models):
            continue
        if any(conn.execute("SELECT 1 FROM bom_links WHERE parent=? AND child=?", (m, code)).fetchone() for m in models):
            continue
        try:
            dims = [positive(r[k], "板料尺寸") for k in ("thickness", "width", "length")]
            positive(r["pieces"], "下料只数")
            net = validated_net_weight(r, next(iter(weights), None))
        except ValueError:
            continue
        grade = r["material_grade"] or material_grade_for_code(code)
        expected_plate = plate_code(grade, r)
        if existing and (existing["code"] != code or existing["kind"] != STAMP or existing["name"] != r["part"]
                         or existing["plate"] != expected_plate or existing["pieces"] != r["pieces"]
                         or (net and existing["net_weight"] and net != existing["net_weight"])):
            continue
        plate = stock(conn, grade, dims)
        if not existing or (net and not existing["net_weight"]):
            save_part(conn, dict(code=code, kind=STAMP, name=r["part"], plate=plate, pieces=r["pieces"], net_weight=net))
        for row in rows:
            if not part(conn, row["model"]):
                save_part(conn, dict(code=row["model"], kind=ASSEMBLY, name=row["category"] or ASSEMBLY))
            add_link(conn, row["model"], code, active=row["active"], origin=row["sync_id"])


def resolve_duplicate(conn, data):
    model, code = data.get("parent"), data.get("code")
    matching = [r for r in pending_rows(conn) if r["model"] == model and r["code"] == code]
    fields = ("part", "thickness", "width", "length", "material_grade", "pieces")
    if (not code or len(matching) < 2 or not all(r["sync_id"] for r in matching)
            or len({tuple(r[k] for k in fields) for r in matching}) != 1
            or len({r["net_weight"] for r in matching if r["net_weight"]}) > 1):
        raise ValueError("请先核对同一母件下的重复行，统一零件和下料资料。")
    qty = positive(data.get("qty"), "确认装配用量")
    matching.sort(key=lambda r: (not r["net_weight"], r["id"]))
    relation = link_id(model, code)
    conn.executemany("INSERT INTO bom_source_aliases VALUES (?,?,?)",
                     [(r["sync_id"], relation, revision_time()) for r in matching[1:]])
    migrate_rows(conn)
    changed = conn.execute("UPDATE bom_links SET qty=?,active=?,updated_at=? WHERE sync_id=? AND origin=?",
                           (qty, int(any(r["active"] for r in matching)), revision_time(), relation, matching[0]["sync_id"]))
    if not changed.rowcount:
        raise ValueError("其他母件还有同码冲突，请先核对这些原始资料。")


def _form_values(data, key):
    if hasattr(data, "getlist"):
        return data.getlist(key)
    value = data.get(key, [])
    return list(value) if isinstance(value, (list, tuple)) else [value]


def save_assembly(conn, data, actor):
    code = str(data.get("code", "")).strip()
    current = part(conn, code)
    if current and current["kind"] != ASSEMBLY:
        raise ValueError("此编码不是母件，请返回核对物料档案。")
    if current and data.get("structure_revision"):
        links = conn.execute("SELECT updated_at FROM bom_links WHERE parent=?", (code,)).fetchall()
        latest = max([current["updated_at"], *(row["updated_at"] for row in links)])
        if data["structure_revision"] != latest:
            raise ValueError("母件或子件清单已变更，请重新打开核对。")
    values = dict(action="edit" if current else "create", code=code, name=data.get("name"),
                  model=str(data.get("model", "")).strip(), kind=ASSEMBLY,
                  net_weight=data.get("net_weight"), revision=data.get("revision", ""))
    update_bom(conn, values, actor)

    fields = ("child_code", "child_qty", "new_name", "new_kind", "new_spec", "new_pieces", "new_net_weight", "new_tube_code")
    columns = {key: _form_values(data, key) for key in fields}
    count = len(columns["child_code"])
    if count > 500 or any(len(values) not in (0, count) for key, values in columns.items() if key != "child_code"):
        raise ValueError("子件清单行数或字段不完整，请重新录入。")
    submitted = []
    for index, raw_code in enumerate(columns["child_code"]):
        child_code = str(raw_code).strip()
        if not child_code:
            continue
        if any(child_code.casefold() == existing.casefold() for existing in submitted):
            raise ValueError(f"子件 {child_code} 重复，请合并后填写总用量。")
        submitted.append(child_code)
        child = part(conn, child_code)
        new_kind = columns["new_kind"][index] if columns["new_kind"] else ""
        if child:
            if new_kind:
                raise ValueError(f"子件 {child_code} 已存在，请直接选择，不要重复新建。")
        else:
            new_values = {key: (columns[key][index] if columns[key] else "") for key in fields}
            update_bom(conn, dict(action="create", code=child_code, name=new_values["new_name"],
                                  kind=new_kind, spec=new_values["new_spec"], pieces=new_values["new_pieces"],
                                  net_weight=new_values["new_net_weight"], tube_code=new_values["new_tube_code"]), actor)
        qty = positive(columns["child_qty"][index], f"第 {index + 1} 行装配用量")
        link = conn.execute("SELECT qty,updated_at FROM bom_links WHERE parent=? AND child=?", (code, child_code)).fetchone()
        if link:
            if link["qty"] != qty:
                conn.execute("UPDATE bom_links SET qty=?,updated_at=? WHERE parent=? AND child=?",
                             (qty, revision_time(), code, child_code))
        else:
            add_link(conn, code, child_code, qty)
    for link in conn.execute("SELECT child FROM bom_links WHERE parent=?", (code,)).fetchall():
        if link["child"].casefold() not in {code.casefold() for code in submitted}:
            remove_link(conn, dict(parent=code, code=link["child"]), actor)


def update_bom(conn, data, actor="local"):
    action = data.get("action")
    if action == "assembly_save":
        save_assembly(conn, data, actor)
        return
    if action == "merge":
        resolve_duplicate(conn, data)
        return
    code = str(data.get("code", "")).strip()
    parent = part(conn, data.get("parent", ""))
    current = part(conn, code)
    if (action == "edit" and current and data.get("revision")
            and data["revision"] != current["updated_at"]):
        raise ValueError("物料资料已变更，请重新打开核对。")
    if action == "remove":
        if not parent or not current:
            raise ValueError("请选择有效的装配关系。")
        remove_link(conn, dict(data, parent=parent["code"], code=current["code"]), actor)
        return
    if action == "restore":
        restore_link(conn, data)
        return
    if action == "delete":
        delete_part(conn, data)
        return
    if action in ("attach", "usage", "create", "copy") and data.get("parent") and (not parent or parent["kind"] != ASSEMBLY):
        raise ValueError("请选择有效的装配母件。")
    if action == "attach" and parent and data.get("tube_kind") in TUBE_KINDS:
        if data.get("tube_code") != code:
            raise ValueError("请选择管件资料中已登记的启用编码。")
        if current and current["kind"] not in TUBE_KINDS:
            raise ValueError("此编码已作为其他物料建档，不能重复作为管件引用。")
        if not current:
            if conn.execute("SELECT 1 FROM material_items WHERE code=? COLLATE NOCASE", (code,)).fetchone():
                raise ValueError("此编码已有待核对的历史资料。")
            save_part(conn, dict(code=code, name=data["tube_kind"], kind=data["tube_kind"]))
            current = part(conn, code)
    if action in ("attach", "usage"):
        if not parent or not current or current["kind"] == PLATE:
            raise ValueError("请选择已有子件。")
        if action == "attach":
            if current["kind"] in TUBE_KINDS and data.get("tube_code") != code:
                raise ValueError("管件必须引用管件资料中已登记的启用编码。")
            add_link(conn, parent["code"], current["code"], data.get("qty"))
        else:
            original = conn.execute("SELECT updated_at FROM bom_links WHERE parent=? AND child=?",
                                    (parent["code"], current["code"])).fetchone()
            if data.get("revision") and (not original or original[0] != data["revision"]):
                raise ValueError("装配关系已变更，请重新核对。")
            cursor = conn.execute("""UPDATE bom_links SET qty=?,active=?,updated_at=? WHERE parent=? AND child=?""",
                (positive(data.get("qty"), "装配用量"), int(data.get("active") == "1"), revision_time(), parent["code"], current["code"]))
            if not cursor.rowcount:
                raise ValueError("装配关系已变更，请刷新。")
        return
    if action not in ("create", "edit", "copy"):
        raise ValueError("未知操作。")
    if action == "edit":
        if not current or current["kind"] == PLATE:
            raise ValueError("请选择有效物料。")
        kind = current["kind"]
    else:
        if not code or len(code) > 120 or code.startswith("板料/"):
            raise ValueError("请填写有效的新物料编码。")
        if current or conn.execute("SELECT 1 FROM material_items WHERE code=? COLLATE NOCASE OR model=? COLLATE NOCASE",
                                   (code, code)).fetchone():
            raise ValueError("编码已存在，请添加已有子件或先核对历史资料。")
        kind = data.get("kind")
    source = part(conn, data.get("source", "")) if action == "copy" else None
    if action == "copy" and (not source or source["kind"] != kind):
        raise ValueError("请选择有效的复制来源。")
    name = str(data.get("name", "")).strip()
    if kind not in KINDS or not name:
        raise ValueError("请填写物料类型和名称。")
    if action in ("create", "copy") and kind in TUBE_KINDS and data.get("tube_code") != code:
        raise ValueError("请先建立管件资料，再在 BOM 中引用其编码。")
    values: dict = dict(code=code, name=name, model=str(data.get("model", "")).strip(), kind=kind)
    if kind == STAMP:
        grade = material_grade_for_code(code)
        dims = _parse_material_spec_text(data.get("spec"))
        plate = stock(conn, grade, dims)
        pieces = positive(data.get("pieces"), "下料只数")
        net = validated_net_weight(dict(thickness=dims[0], width=dims[1], length=dims[2], pieces=pieces), data.get("net_weight"))
        values.update(plate=plate, pieces=pieces, net_weight=net)
    elif kind == ASSEMBLY:
        values["net_weight"] = validated_part_weight(data.get("net_weight"))
    save_part(conn, values)
    if source and data.get("copy_children") == "1":
        for row in conn.execute("SELECT * FROM bom_links WHERE parent=?", (source["code"],)).fetchall():
            add_link(conn, code, row["child"], row["qty"], row["active"])
    if parent and action != "edit":
        add_link(conn, parent["code"], code, data.get("qty"))

def validate_graph(conn):
    """Check the merged local graph after applying a cross-device package."""
    rows = {r["code"]: dict(r) for r in conn.execute("SELECT * FROM bom_parts")}
    for code, r in rows.items():
        if not code.strip() or not r["name"] or r["kind"] not in (*KINDS, PLATE):
            raise ValueError("BOM 物料编码、名称或类型无效。")
        if r["kind"] == PLATE:
            for field in ("thickness", "width", "length"):
                positive(r[field], "板料尺寸")
            if code != plate_code(r["grade"], r):
                raise ValueError("板料编码与规格不一致。")
        if r["kind"] == STAMP:
            p = rows.get(r["plate"])
            if not p or p["kind"] != PLATE or p["grade"] != material_grade_for_code(code):
                raise ValueError("冲压件的板料缺失或牌号不一致。")
            positive(r["pieces"], "下料只数")
            validated_net_weight(dict(p, pieces=r["pieces"]), r["net_weight"])
        elif r["plate"] or r["pieces"] is not None:
            raise ValueError("非冲压件不能指定下料只数或板料。")
    graph = defaultdict(list)
    for r in conn.execute("SELECT * FROM bom_links"):
        a, b = r["parent"], r["child"]
        if (a not in rows or b not in rows or rows[a]["kind"] != ASSEMBLY or rows[b]["kind"] == PLATE
                or link_id(a,b) != r["sync_id"] or r["active"] not in (0,1)):
            raise ValueError("BOM 关系缺少有效的母件或子件。")
        positive(r["qty"], "装配用量")
        graph[a].append(b)
    done = set()
    def visit(code, trail):
        if code in trail:
            raise ValueError("BOM 关系存在循环。")
        if code not in done:
            for child in graph.get(code, []):
                visit(child, trail | {code})
            done.add(code)
    for code in graph:
        visit(code, set())

    if conn.execute("""SELECT 1 FROM bom_source_aliases a LEFT JOIN bom_links b ON b.sync_id=a.relation
        LEFT JOIN material_items m ON m.sync_id=a.origin WHERE b.sync_id IS NULL OR m.id IS NULL
        OR m.model<>b.parent OR m.code<>b.child OR a.origin=b.origin LIMIT 1""").fetchone():
        raise ValueError("历史来源与 BOM 关系不一致。")
    for r in conn.execute("SELECT * FROM bom_removed_links"):
        if (r["parent"] not in rows or r["child"] not in rows or rows[r["parent"]]["kind"] != ASSEMBLY
                or rows[r["child"]]["kind"] == PLATE or link_id(r["parent"], r["child"]) != r["sync_id"]
                or r["active"] not in (0, 1) or not r["removed_at"] or not r["removed_by"]):
            raise ValueError("已移除 BOM 关系缺少有效资料。")
        positive(r["qty"], "装配用量")
        if conn.execute("SELECT 1 FROM bom_links WHERE sync_id=?", (r["sync_id"],)).fetchone():
            raise ValueError("同一 BOM 关系不能同时存在和标记为已移除。")
    if conn.execute("""SELECT 1 FROM bom_removed_sources s LEFT JOIN bom_removed_links b ON b.sync_id=s.relation
        LEFT JOIN material_items m ON m.sync_id=s.origin WHERE b.sync_id IS NULL OR m.id IS NULL
        OR m.model<>b.parent OR m.code<>b.child LIMIT 1""").fetchone():
        raise ValueError("已移除关系与历史来源不一致。")
