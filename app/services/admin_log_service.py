"""管理员操作日志。

记录管理员对卡池、素材、全局配置的每一次改动：操作人、时间、内容、来源 IP。
用户名以快照保存，即使管理员账号被删，日志依然可读。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminLog, User

#: 单条日志正文上限，避免把整份卡池配置塞进去把行撑爆
DETAIL_LIMIT = 2000


def diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """只记录真正发生变化的字段。

    全量记录会让日志迅速失去可读性 —— 改一个价格却打印 37 个字段，
    管理员根本看不出改了什么。
    """
    changed: dict[str, Any] = {}
    for key, new_value in after.items():
        old_value = before.get(key, "<不存在>")
        if old_value != new_value:
            changed[key] = {"from": _shrink(old_value), "to": _shrink(new_value)}
    return changed


def _shrink(value: Any, limit: int = 120) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


async def log_action(
    db: AsyncSession,
    *,
    admin: User,
    action: str,
    target_type: str = "",
    target_id: str | int | None = None,
    detail: dict[str, Any] | str | None = None,
    ip: str = "",
    commit: bool = True,
) -> AdminLog:
    """写一条操作日志。"""
    if detail is None:
        payload = ""
    elif isinstance(detail, str):
        payload = detail
    else:
        payload = json.dumps(detail, ensure_ascii=False, default=str)

    entry = AdminLog(
        admin_id=admin.id,
        admin_username=admin.username,
        action=action,
        target_type=target_type,
        target_id="" if target_id is None else str(target_id),
        detail=payload[:DETAIL_LIMIT],
        ip=ip[:64],
    )
    db.add(entry)
    if commit:
        await db.commit()
    return entry


async def query_logs(
    db: AsyncSession,
    *,
    action: str | None = None,
    admin_username: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AdminLog], int]:
    """分页查询操作日志（时间倒序）。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    stmt = select(AdminLog)
    count_stmt = select(func.count()).select_from(AdminLog)

    if action:
        stmt = stmt.where(AdminLog.action == action)
        count_stmt = count_stmt.where(AdminLog.action == action)
    if admin_username:
        stmt = stmt.where(AdminLog.admin_username == admin_username)
        count_stmt = count_stmt.where(AdminLog.admin_username == admin_username)

    total = int((await db.execute(count_stmt)).scalar_one())
    stmt = stmt.order_by(AdminLog.created_at.desc(), AdminLog.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def list_actions(db: AsyncSession) -> list[str]:
    """已出现过的操作类型，供日志筛选下拉使用。"""
    result = await db.execute(select(AdminLog.action).distinct().order_by(AdminLog.action))
    return [row for row in result.scalars().all() if row]
