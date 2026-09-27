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
    # 原石自助增加的档位与上限。由服务端下发而不是写死在前端，
    # 这样界面上的选项永远与服务端的白名单一致。
    primogem_topup_options=settings.topup_options,
    primogem_topup_cap=settings.primogem_topup_cap,
)


def render(
    request: Request,
    template: str,
    context: dict | None = None,
    *,
    status_code: int = 200,
):
    """统一的模板渲染入口。

    自动注入 ``request`` 与当前用户，避免每个路由重复传参。

    **用 UserView 快照而不是 ORM 实例**：异常处理器渲染错误页时，请求的数据库
    会话已经关闭，ORM 实例处于 detached + expired 状态，读任何属性都会抛
    ``DetachedInstanceError``。快照是纯数据，不受会话生命周期影响。

    ``status_code`` 必须由调用方显式传入：TemplateResponse 默认是 200，
    渲染错误页时若忘了传，就会出现"返回 403 页面但 HTTP 状态是 200"，
    前端与监控都会被骗过去。
    """
    ctx = {
        "request": request,
        "current_user": getattr(request.state, "user_view", None),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)
