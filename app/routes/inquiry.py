from __future__ import annotations

from app.modules.inquiry.alias_web import register as register_aliases
from app.modules.inquiry.download_web import register as register_downloads
from app.modules.inquiry.match_web import register as register_matches


def register(app) -> None:
    register_matches(app)
    register_downloads(app)
    register_aliases(app)
