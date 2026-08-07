from __future__ import annotations

from .api import register as register_api
from .web import register as register_web


def register(app) -> None:
    from . import drawings_web as _drawings_web  # noqa: F401  # 导入即在 quote_web 蓝图上登记图纸关联路由

    register_web(app)
    register_api(app)
