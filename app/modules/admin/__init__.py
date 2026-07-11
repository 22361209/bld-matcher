from __future__ import annotations

from .auth_web import register as register_auth
from .web import register as register_admin


def register(app) -> None:
    register_auth(app)
    register_admin(app)


__all__ = ["register"]
