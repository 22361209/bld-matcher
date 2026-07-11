from __future__ import annotations

import re
from functools import wraps

from flask import g, request

from .api_errors import ApiError


def if_match_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        value = request.headers.get("If-Match", "").strip()
        if not value:
            raise ApiError(
                "precondition.required",
                "修订资源必须提供 If-Match 版本。",
                428,
            )
        match = re.fullmatch(r'(?:W/)?"?(\d+)"?', value)
        if not match or int(match.group(1)) < 1:
            raise ApiError(
                "precondition.invalid",
                "If-Match 必须是有效的资源版本号。",
                400,
            )
        g.expected_version = int(match.group(1))
        return fn(*args, **kwargs)

    setattr(wrapper, "__if_match_required__", True)
    return wrapper


def expected_version() -> int:
    version = getattr(g, "expected_version", None)
    if not isinstance(version, int) or version < 1:
        raise RuntimeError("expected_version requires if_match_required.")
    return version
