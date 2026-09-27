"""历史记录 API：分页、筛选、统计、CSV 导出。

所有查询都强制带上 ``user_id`` 过滤，用户之间不可能看到彼此的记录。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.deps import DbSession, RequiredUser
from app.schemas.history import PageOut, StatsOut
from app.services import history_service
from app.services.history_service import HistoryFilter

router = APIRouter(prefix="/api/history", tags=["history"])

#: 导出文件名里的时间戳
_FILENAME_TS = "%Y%m%d_%H%M%S"


def parse_date(value: str | None, *, end_of_day: bool = False) -> date | None:
    """接受 ``YYYY-MM-DD`` 或完整 ISO 时间，返回日期。"""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def build_filter(
    pool_id: int | None,
    star: int | None,
    is_up: bool | None,
    date_from: str | None,
    date_to: str | None,
    batch_id: str | None,
) -> HistoryFilter:
    return HistoryFilter(
        pool_id=pool_id,
        star=star,
        is_up=is_up,
        date_from=parse_date(date_from),
        date_to=parse_date(date_to),
        batch_id=batch_id,
    )


@router.get("", response_model=PageOut)
async def list_history(
    db: DbSession,
    user: RequiredUser,
    pool_id: Annotated[int | None, Query(ge=1)] = None,
    star: Annotated[int | None, Query(ge=1, le=5)] = None,
    is_up: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    batch_id: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> PageOut:
    """分页查询当前用户的抽卡记录（时间倒序）。"""
    f = build_filter(pool_id, star, is_up, date_from, date_to, batch_id)
    return await history_service.query_history(db, user.id, f, page=page, page_size=page_size)


@router.get("/stats", response_model=StatsOut)
async def get_stats(
    db: DbSession,
    user: RequiredUser,
    pool_id: Annotated[int | None, Query(ge=1)] = None,
    star: Annotated[int | None, Query(ge=1, le=5)] = None,
    is_up: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> StatsOut:
    """按同样的筛选条件统计。"""
    f = build_filter(pool_id, star, is_up, date_from, date_to, None)
    return await history_service.compute_stats(db, user.id, f)


@router.get("/export")
async def export_history(
    db: DbSession,
    user: RequiredUser,
    pool_id: Annotated[int | None, Query(ge=1)] = None,
    star: Annotated[int | None, Query(ge=1, le=5)] = None,
    is_up: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> StreamingResponse:
    """导出当前用户的抽卡记录为 CSV（时间倒序）。

    带 UTF-8 BOM：否则 Excel 在中文 Windows 上按 GBK 解码，中文全是乱码。
    """
    f = build_filter(pool_id, star, is_up, date_from, date_to, None)
    stamp = datetime.now().strftime(_FILENAME_TS)
    filename = f"gacha-history-{stamp}.csv"
    return StreamingResponse(
        history_service.export_csv(db, user.id, f),
        media_type="text/csv; charset=utf-8",
        headers={
            # 同时给出 ASCII 回退名与 RFC 5987 的 UTF-8 名
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
