"""FastAPI 依赖：当前用户、权限校验、客户端 IP。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthRequired, Forbidden
from app.db.session import get_db
from app.models import User, UserRole

DbSession = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True, slots=True)
class UserView:
    """模板用的用户快照，**不持有任何 ORM 实例**。

    为什么必须要有它：``get_db`` 在异常时会 ``session.rollback()``，而 rollback
    会让 session 内所有 ORM 实例过期；随后依赖清理关闭 session，实例变成
    detached。异常处理器再渲染模板读 ``current_user.role`` 时就会触发属性刷新，
    抛 ``DetachedInstanceError``，于是"普通用户访问后台"返回的是 500 而不是 403。

    快照在 session 还活着的时候就取好，之后随便怎么 rollback / close 都不受影响。
    """

    id: int
    username: str
    role: str
    primogems: int = 0
    intertwined_fate: int = 0
    acquaint_fate: int = 0

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN


def to_user_view(user: User) -> UserView:
    currency = user.currency
    return UserView(
        id=user.id,
        username=user.username,
        role=user.role,
        primogems=currency.primogems if currency else 0,
        intertwined_fate=currency.intertwined_fate if currency else 0,
        acquaint_fate=currency.acquaint_fate if currency else 0,
    )


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
        request.state.user_view = None
        return None

    user = await db.get(User, int(user_id))
    if user is None or not user.is_active:
        request.session.clear()
        request.state.user = None
        request.state.user_view = None
        return None

    request.state.user = user
    # 趁 session 还活着取好快照，供模板（尤其是异常页）使用
    request.state.user_view = to_user_view(user)
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
