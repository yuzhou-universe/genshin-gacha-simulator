"""HTML 页面路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.core.deps import CurrentUser, RequiredUser
from app.core.templating import render
from app.routers.auth import redirect_for

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(user: CurrentUser):
    """按角色分流：管理员进后台，普通用户进抽卡界面，未登录进登录页。"""
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse(redirect_for(user.role), status_code=302)


@router.get("/gacha")
async def gacha_page(request: Request, user: RequiredUser):
    # 当前用户由 render() 从 request.state 自动注入模板，无需显式传参
    return render(request, "gacha/index.html")


@router.get("/history")
async def history_page(request: Request, user: RequiredUser):
    return render(request, "history/index.html")
