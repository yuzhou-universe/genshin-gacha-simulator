"""FastAPI 依赖：当前用户、权限校验、客户端 IP。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthRequired, Forbidden
from app.db.session import get_db
from app.models import User, UserRole

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_client_ip(request: Request) -> str:
    """取客户端 IP。仅用于登录失败统计与操作日志，不写入任何日志正文的敏感字段。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


async def get_current_user(request: Request, db: DbSession) -> User | None:
    """从签名 Cookie 会话中解析当前用户。

    每次请求都回库读取，因此禁用账号、改角色都能立即生效。
    """
    user_id = request.session.get("user_id")
    if not user_id:
        request.state.user = None
        return None

    user = await db.get(User, int(user_id))
    if user is None or not user.is_active:
        request.session.clear()
        request.state.user = None
        return None

    request.state.user = user
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


async def require_user(user: CurrentUser) -> User:
    """必须登录。未登录抛 AuthRequired（页面跳登录，API 返回 401）。"""
    if user is None:
        raise AuthRequired
    return user


async def require_admin(user: CurrentUser) -> User:
    """必须是管理员。已登录但非管理员返回 403，未登录返回 401。"""
    if user is None:
        raise AuthRequired
    if user.role != UserRole.ADMIN:
        raise Forbidden("该功能仅管理员可用")
    return user


RequiredUser = Annotated[User, Depends(require_user)]
AdminUser = Annotated[User, Depends(require_admin)]
