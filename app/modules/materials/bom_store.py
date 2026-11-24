"""Shared material masters, blank stock and assembly relationships."""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from datetime import datetime
import json
from math import isfinite

from .domain import material_grade_for_code
from .specification import _parse_material_spec_text
from .weights import validated_net_weight


ASSEMBLY = "焊接件"
STAMP = "冲压件"
PLATE = "板料"
TUBE_KINDS = ("焊接管", "焊接座管", "轴类")
KINDS = (ASSEMBLY, STAMP, *TUBE_KINDS)
PART_FIELDS = ("code", "name", "kind", "plate", "pieces", "net_weight", "grade", "thickness", "width", "length")


class ExistingBomLink(ValueError):
    pass


def revision_time():
    return datetime.now().isoformat(sep=" ", timespec="microseconds")


def install(conn):
    conn.execute("""CREATE TABLE bom_parts (
        code TEXT PRIMARY KEY COLLATE NOCASE, name TEXT NOT NULL, kind TEXT NOT NULL,
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
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(material_items)")}
    if {"model", "code", "part", "pieces", "width", "length", "thickness", "sync_id",
        "active", "material_grade", "net_weight"} <= columns:
        migrate_rows(conn)


def positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是正数。") from None
    if not isfinite(number) or not 0 < number <= 1e12:
        raise ValueError(f"{label}超出有效范围。")
    return number


def exact_spec(row):
    return "×".join(str(float(row[k])).removesuffix(".0") for k in ("thickness", "width", "length"))


def part(conn, code):
    row = conn.execute("SELECT * FROM bom_parts WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def save_part(conn, values):
    conn.execute("""INSERT INTO bom_parts(code,name,kind,plate,pieces,net_weight,grade,thickness,width,length,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,
        plate=excluded.plate,pieces=excluded.pieces,net_weight=excluded.net_weight,updated_at=excluded.updated_at""",
        [*(values.get(k) for k in PART_FIELDS), revision_time()])


def stock(conn, grade, dimensions):
    dims = [positive(v, "板料尺寸") for v in dimensions]
    row = dict(zip(("thickness", "width", "length"), dims, strict=True))
    code = f"板料/{grade}/{exact_spec(row)}"
    if not part(conn, code):
        save_part(conn, {**row, "code": code, "name": grade, "grade": grade, "kind": PLATE})
    return code


def link_id(parent, child):
    return sha256(json.dumps([parent, child], ensure_ascii=False).encode()).hexdigest()


def add_link(conn, parent, child, qty=1, active=1, origin=None):
    if conn.execute("SELECT 1 FROM bom_links WHERE parent=? AND child=?", (parent, child)).fetchone():
        raise ExistingBomLink("该母件已有此子件，可在原关系上修改用量或启用。")
    if parent == child or conn.execute("""WITH RECURSIVE descendants(code) AS
        (SELECT child FROM bom_links WHERE parent=? UNION SELECT b.child FROM bom_links b
         JOIN descendants d ON d.code=b.parent) SELECT 1 FROM descendants WHERE code=?""", (child, parent)).fetchone():
        raise ValueError("这条关系会形成循环 BOM。")
    conn.execute("INSERT INTO bom_links VALUES (?,?,?,?,?,?,?)",
                 (link_id(parent, child), parent, child, positive(qty, "装配用量"), active, origin, revision_time()))


def pending_rows(conn):
    return [dict(r) for r in conn.execute("""SELECT m.* FROM material_items m
        LEFT JOIN bom_links b ON b.origin=m.sync_id LEFT JOIN bom_source_aliases a ON a.origin=m.sync_id
        WHERE b.sync_id IS NULL AND a.origin IS NULL ORDER BY m.model,m.code,m.id""")]


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
        expected_plate = f"板料/{grade}/{exact_spec(r)}"
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


def update_bom(conn, data):
    action = data.get("action")
    if action == "merge":
        resolve_duplicate(conn, data)
        return
    code = str(data.get("code", "")).strip()
    parent = part(conn, data.get("parent", ""))
    current = part(conn, code)
    if (action == "edit" and current and data.get("revision")
            and data["revision"] != current["updated_at"]):
        raise ValueError("物料资料已变更，请重新打开核对。")
    if action in ("attach", "usage", "create", "copy") and data.get("parent") and (not parent or parent["kind"] != ASSEMBLY):
        raise ValueError("请选择有效的装配母件。")
    if action == "attach" and parent and not current and data.get("tube_code") == code and data.get("tube_kind") in TUBE_KINDS:
        if conn.execute("SELECT 1 FROM material_items WHERE code=? COLLATE NOCASE", (code,)).fetchone():
            raise ValueError("此编码已有待核对的历史资料。")
        save_part(conn, dict(code=code, name=data["tube_kind"], kind=data["tube_kind"]))
        current = part(conn, code)
    if action in ("attach", "usage"):
        if not parent or not current or current["kind"] == PLATE:
            raise ValueError("请选择已有子件。")
        if action == "attach":
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
    values: dict = dict(code=code, name=name, kind=kind)
    if kind == STAMP:
        grade = material_grade_for_code(code)
        dims = _parse_material_spec_text(data.get("spec"))
        plate = stock(conn, grade, dims)
        pieces = positive(data.get("pieces"), "下料只数")
        net = validated_net_weight(dict(thickness=dims[0], width=dims[1], length=dims[2], pieces=pieces), data.get("net_weight"))
        values.update(plate=plate, pieces=pieces, net_weight=net)
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
            if code != f"板料/{r['grade']}/{exact_spec(r)}":
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
