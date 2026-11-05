"""Single-piece steel consumption and measured net weight, in kilograms."""
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, localcontext


_WEIGHT_STEP = Decimal("0.000000000000000001")
_MAX_WEIGHT = Decimal("1000000000000")


def _decimal_text(value: Decimal) -> str:
    if not value:
        return "0"
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -18:
        value = value.quantize(_WEIGHT_STEP)
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _gross_weight(row: Mapping[str, object]) -> Decimal | None:
    try:
        thickness, width, length, pieces = (
            Decimal(str(row.get(key))) for key in ("thickness", "width", "length", "pieces")
        )
    except InvalidOperation:
        return None
    if not all(v.is_finite() and _WEIGHT_STEP <= v <= _MAX_WEIGHT for v in (thickness, width, length, pieces)):
        return None
    with localcontext() as context:
        context.prec = 64
        result = thickness * width * length * Decimal("0.00000785") / pieces
        return result.quantize(_WEIGHT_STEP) if _WEIGHT_STEP <= result <= _MAX_WEIGHT else None


def validated_net_weight(row: Mapping[str, object], value: object) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    message = "净重必须为有限正数，最多保留 18 位小数。"
    if isinstance(value, bool) or len(str(value)) > 100:
        raise ValueError(message)
    try:
        net = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(message) from exc
    exponent = net.as_tuple().exponent
    if not net.is_finite() or not 0 < net <= _MAX_WEIGHT or not isinstance(exponent, int) or exponent < -18:
        raise ValueError(message)
    gross = _gross_weight(row)
    if gross is None:
        raise ValueError("请先核对规格尺寸和下料只数，再填写净重。")
    if net > gross:
        raise ValueError("净重不能大于单件下料重量。")
    return _decimal_text(net)


def costing_material(row: Mapping[str, object]) -> dict[str, str | None] | None:
    with localcontext() as context:
        context.prec = 64
        gross = _gross_weight(row)
        source_id = str(row.get("sync_id") or "").strip()
        if gross is None or not source_id:
            return None
        try:
            net = validated_net_weight(row, row.get("net_weight"))
        except ValueError:
            return None
        scrap = gross - Decimal(net) if net is not None else None
        return {
            "code": str(row["code"] or ""), "name": str(row["part"] or ""),
            "model": str(row["model"] or ""), "spec": str(row["spec_text"] or ""),
            "sync_id": source_id, "pieces": str(row["pieces"]),
            "gross_weight": _decimal_text(gross),
            "net_weight": net,
            "scrap_weight": _decimal_text(scrap) if scrap is not None else None,
            "scrap_percent": _decimal_text(scrap * 100 / gross) if scrap is not None else None,
        }
