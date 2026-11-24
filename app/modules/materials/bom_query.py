from __future__ import annotations

from collections import Counter, defaultdict
from math import ceil
from .specification import _parse_material_spec_query

from .bom_store import ASSEMBLY, PLATE, STAMP, exact_spec, pending_rows
from .weights import costing_material


class MaterialBOM:
    def __init__(self, conn):
        owned = not conn.in_transaction
        if owned:
            conn.execute("BEGIN")
        try:
            self._read(conn)
        finally:
            if owned:
                conn.rollback()

    def _read(self, conn):
        self.parts = {r["code"]: dict(r) for r in conn.execute("SELECT * FROM bom_parts ORDER BY code")}
        self.children = defaultdict(list)
        self.parents = defaultdict(list)
        self.legacy = pending_rows(conn)
        duplicates = Counter((r["model"], r["code"]) for r in self.legacy)
        for r in self.legacy:
            r["duplicate_count"] = duplicates[r["model"], r["code"]]
        self.source_aliases = defaultdict(list)
        for r in conn.execute("SELECT origin,relation FROM bom_source_aliases"):
            self.source_aliases[r["relation"]].append(r["origin"])
        self.sources = {r["sync_id"]: dict(r) for r in conn.execute(
            "SELECT id,sync_id,source_row,car,category FROM material_items")}
        for row in conn.execute("SELECT * FROM bom_links ORDER BY parent,child"):
            link = dict(row)
            self.children[link["parent"]].append(link)
            self.parents[link["child"]].append(link)
        for p in self.parts.values():
            if p["kind"] == PLATE:
                p["spec"] = exact_spec(p)
        for p in self.parts.values():
            plate = self.parts.get(p["plate"])
            if plate:
                p["spec"] = plate["spec"]
                p["grade"] = plate["grade"]

    def page(self, query, status, page, view, selected):
        groups = []
        needle = query.strip().casefold().replace("*", "×")
        dims = _parse_material_spec_query(query)
        def matches(p):
            spec = p.get("spec")
            if dims and spec:
                numbers = list(map(float, spec.split("×")))
                if any(numbers[i:i+len(dims)] == dims for i in range(3)):
                    return True
            return any(needle in str(p.get(k) or "").casefold() for k in ("code", "name", "kind", "spec", "grade"))

        def tree(code, trail):
            p = self.parts[code]
            nested = []
            if code in trail:
                raise ValueError("BOM 存在循环，请核对装配关系。")
            for link in self.children[code]:
                if status == "active" and not link["active"]:
                    continue
                if status == "inactive" and link["active"] and self.parts[link["child"]]["kind"] != ASSEMBLY:
                    continue
                node = tree(link["child"], trail | {code})
                if status == "inactive" and link["active"] and not node["nodes"]:
                    continue
                nested.append(dict(node, link=link))
            hits = sum(1 for n in nested if n["hit"])
            return dict(p, nodes=nested, hit=matches(p) or hits > 0, matched=hits, own_hit=matches(p))

        for code, p in self.parts.items():
            if view == "library":
                if matches(p):
                    groups.append(p)
            elif view == "list":
                for link in self.children.get(code, ()):
                    if (status == "active" and not link["active"]) or (status == "inactive" and link["active"]):
                        continue
                    if matches(p) or matches(self.parts[link["child"]]):
                        groups.append(link)
            elif p["kind"] == ASSEMBLY:
                node = tree(code, set())
                if node["hit"] and (status != "inactive" or node["nodes"]):
                    groups.append(node)
        detail = self.parts.get(selected)
        weights = None
        if detail and detail["kind"] == STAMP:
            weights = costing_material(self.stamp_row(detail))
        pages = max(1, ceil(len(groups) / 30))
        page = min(max(1, page), pages)
        return dict(groups=groups[(page-1)*30:page*30], total=len(groups), pages=pages, page=page,
                    parts=self.parts, children=self.children, parents=self.parents,
                    used_by_plate=[p for p in self.parts.values() if detail and p["plate"] == detail["code"]],
                    detail=detail, weights=weights, legacy=self.legacy, q=query, status=status, view=view)

    def stamp_row(self, p, link=None):
        plate = self.parts[p["plate"]]
        link = link or {}
        origin = link.get("origin")
        source = self.sources.get(origin, {})
        return dict(thickness=plate["thickness"], width=plate["width"], length=plate["length"],
                    blank_spec=plate["spec"], material_grade=plate["grade"], code=p["code"], part=p["name"], pieces=p["pieces"],
                    net_weight=p["net_weight"], spec_text=plate["spec"], sync_id=origin or "bom/"+p["code"],
                    model=link.get("parent", ""), car=source.get("car", ""), category=source.get("category", ""),
                    source_row=source.get("source_row", 0))

    def active_stamps(self):
        for r in self.legacy:
            if r["active"]:
                yield r
        for links in self.children.values():
            for link in links:
                p = self.parts[link["child"]]
                if link["active"] and p["kind"] == STAMP:
                    yield dict(self.stamp_row(p, link), id=self.sources.get(link["origin"], {}).get("id"),
                               master=p["code"], aliases=self.source_aliases[link["sync_id"]])

    def costing(self, code):
        rows = []
        for p in self.parts.values():
            if p["kind"] == STAMP and p["code"].casefold() == code.casefold():
                active = [r for r in self.parents[p["code"]] if r["active"]]
                if active or not self.parents[p["code"]]:
                    rows.append(self.stamp_row(p, active[0] if active else None))
        rows.extend(r for r in self.legacy if r["active"] and str(r["code"]).casefold() == code.casefold())
        return [c for r in rows if (c := costing_material(r))]

    def production(self):
        result = defaultdict(list)
        unresolved = defaultdict(list)
        for r in self.legacy:
            if r["active"]:
                unresolved[r["model"]].append(r)

        def expand(code, factor, trail):
            if code in trail:
                raise ValueError(f"母件 {code} 存在循环 BOM。")
            rows = [dict(r, assembly_qty=factor) for r in unresolved[code]]
            for link in self.children[code]:
                if not link["active"]:
                    continue
                p = self.parts[link["child"]]
                qty = factor * link["qty"]
                if p["kind"] == ASSEMBLY:
                    rows.extend(expand(p["code"], qty, trail | {code}))
                elif p["kind"] == STAMP:
                    rows.append(dict(self.stamp_row(p, link), assembly_qty=qty, component_kind=STAMP))
                else:
                    rows.append(dict(kind=p["kind"], code=p["code"], part=p["name"], assembly_qty=qty))
            return rows

        for model in sorted(set(unresolved) | set(self.children)):
            result[model] = expand(model, 1, set())
        return result
