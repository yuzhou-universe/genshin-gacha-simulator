"""Jinja2 模板环境（页面与片段共用）。"""

from __future__ import annotations

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.models.enums import STAR_COLORS, CurrencyType, PoolType

templates = Jinja2Templates(directory=str(settings.templates_dir))

templates.env.globals.update(
    app_name=settings.app_name,
    star_colors=STAR_COLORS,
    currency_labels={
        CurrencyType.PRIMOGEMS.value: CurrencyType.PRIMOGEMS.label,
        CurrencyType.INTERTWINED_FATE.value: CurrencyType.INTERTWINED_FATE.label,
        CurrencyType.ACQUAINT_FATE.value: CurrencyType.ACQUAINT_FATE.label,
    },
    pool_type_labels={
        PoolType.CHARACTER.value: "角色活动祈愿",
        PoolType.WEAPON.value: "武器活动祈愿",
        PoolType.STANDARD.value: "常驻祈愿",
        PoolType.BEGINNER.value: "新手祈愿",
    },
    default_5star_image="/static/img/placeholder-5star.svg",
)


def render(request: Request, template: str, context: dict | None = None):
    """统一的模板渲染入口。

    自动注入 ``request`` 与当前用户，避免每个路由重复传参。
    """
    ctx = {"request": request, "current_user": getattr(request.state, "user", None)}
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template, ctx)
