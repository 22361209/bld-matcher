from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence


MATERIAL_IDENTITY_FIELDS = (
    "model",
    "code",
    "category",
    "car",
    "part",
    "spec_text",
    "pieces",
    "thickness",
    "width",
    "length",
)
MATERIAL_MATCH_FIELDS = ("model", "code", "category", "car", "part")
QUOTE_MATCH_FIELDS = (
    "customer_name",
    "bld_no",
    "customer_product_code",
    "product_model",
    "currency",
    "quote_date",
)


def stable_key(namespace: str, values: Mapping[str, object], fields: Sequence[str]) -> str:
    normalized = [str(values.get(field) or "").strip().upper() for field in fields]
    encoded = json.dumps([namespace, normalized], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_sync_id(namespace: str, key: str, ordinal: int) -> str:
    return hashlib.sha256(f"{namespace}|{key}|{ordinal}".encode("utf-8")).hexdigest()


def material_key(values: Mapping[str, object]) -> str:
    return stable_key("material", values, MATERIAL_IDENTITY_FIELDS)


def material_match_key(values: Mapping[str, object]) -> str:
    return stable_key("material-match", values, MATERIAL_MATCH_FIELDS)


def quote_match_key(values: Mapping[str, object]) -> str:
    return stable_key("quote", values, QUOTE_MATCH_FIELDS)
