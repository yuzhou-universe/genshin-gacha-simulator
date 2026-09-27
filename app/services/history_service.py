"""历史记录查询、统计与导出。"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    PoolType,
    PullRecord,
    UserEpitomizedPath,
    UserPity,
)
from app.schemas.history import (
    PageOut,
    PoolProgressOut,
    PullRecordOut,
    StatsOut,
)
from app.services.gacha_service import list_all_pools
from app.services.pool_rules import PoolRules

# CSV 导出上限，防止一次导出把内存打满
EXPORT_LIMIT = 50_000


@dataclass(slots=True)
class HistoryFilter:
    """历史筛选条件。"""

    pool_id: int | None = None
    star: int | None = None
    is_up: bool | None = None
    date_from: date | None = None
    date_to: date | None = None
    #: 只看某个批次
    batch_id: str | None = None
    extra_pool_ids: list[int] = field(default_factory=list)

    def applied(self) -> dict:
        out: dict = {}
        if self.pool_id is not None:
            out["pool_id"] = self.pool_id
        if self.star is not None:
            out["star"] = self.star
        if self.is_up is not None:
            out["is_up"] = self.is_up
        if self.date_from is not None:
            out["date_from"] = self.date_from.isoformat()
        if self.date_to is not None:
            out["date_to"] = self.date_to.isoformat()
        return out


def _apply_filters(stmt: Select, user_id: int, f: HistoryFilter) -> Select:
    stmt = stmt.where(PullRecord.user_id == user_id)
    if f.pool_id is not None:
        # 记录了 pool_id 可能为 NULL（卡池被删除），此时用快照名兜底匹配
        stmt = stmt.where(PullRecord.pool_id == f.pool_id)
    if f.star is not None:
        stmt = stmt.where(PullRecord.star == f.star)
    if f.is_up is not None:
        stmt = stmt.where(PullRecord.is_up.is_(f.is_up))
    if f.batch_id:
        stmt = stmt.where(PullRecord.batch_id == f.batch_id)
    if f.date_from is not None:
        start = datetime.combine(f.date_from, time.min, tzinfo=UTC)
        stmt = stmt.where(PullRecord.created_at >= start)
    if f.date_to is not None:
        # 结束日期按"包含当天"处理：< 次日零点
        end = datetime.combine(f.date_to + timedelta(days=1), time.min, tzinfo=UTC)
        stmt = stmt.where(PullRecord.created_at < end)
    return stmt


def _to_out(row: PullRecord) -> PullRecordOut:
    return PullRecordOut(
        id=row.id,
        pool_id=row.pool_id,
        pool_name=row.pool_name_snapshot,
        pool_type=row.pool_type_snapshot,
        item_id=row.item_id,
        item_name=row.item_name_snapshot,
        item_type=row.item_type_snapshot,
        item_image=row.item_image_snapshot,
        star=row.star,
        is_up=row.is_up,
        is_pity=row.is_pity,
        source=row.source,
        # 该次出金实际消耗的抽数：出金前已积累了 pity_5_before 抽，本轮再加 1
        pulls_used=row.pity_5_before + 1,
        batch_id=row.batch_id,
        created_at=row.created_at,
    )


async def query_history(
    db: AsyncSession,
    user_id: int,
    f: HistoryFilter,
    *,
    page: int = 1,
    page_size: int | None = None,
) -> PageOut:
    """分页查询历史记录，按时间倒序。"""
    page_size = page_size or settings.history_page_size
    page = max(1, page)
    page_size = max(1, min(page_size, settings.history_max_page_size))

    count_stmt = _apply_filters(select(func.count()).select_from(PullRecord), user_id, f)
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = _apply_filters(select(PullRecord), user_id, f)
    # id 作为次级排序键：同批次内多条记录的 created_at 完全相同，
    # 只按时间排序会导致翻页时记录重复或遗漏。
    stmt = stmt.order_by(PullRecord.created_at.desc(), PullRecord.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(stmt)).scalars().all()
    pages = (total + page_size - 1) // page_size if total else 0

    return PageOut(
        items=[_to_out(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


async def compute_stats(db: AsyncSession, user_id: int, f: HistoryFilter) -> StatsOut:
    """按筛选条件统计。"""
    base = _apply_filters(select(PullRecord), user_id, f).subquery()

    # 用 CASE WHEN 而不是 func.iif：iif 是 SQLite 专有函数，
    # PostgreSQL 上不存在，会让"开发用 SQLite、生产用 PG"的切换直接崩掉。
    def tally(condition) -> object:
        return func.sum(case((condition, 1), else_=0))

    row = (
        await db.execute(
            select(
                func.count().label("total"),
                tally(base.c.star == 5).label("five"),
                tally(base.c.star == 4).label("four"),
                tally(base.c.star == 3).label("three"),
                tally(base.c.is_up).label("up"),
                tally((base.c.star == 5) & base.c.is_up).label("five_up"),
            ).select_from(base)
        )
    ).one()

    total = int(row.total or 0)
    five = int(row.five or 0)
    four = int(row.four or 0)
    three = int(row.three or 0)
    up_count = int(row.up or 0)
    five_up = int(row.five_up or 0)

    # 平均出金抽数：对每个五星"实际消耗的抽数"求平均（pity_5_before + 1），
    # 而不是 total / five —— 后者会把当前未出金的垫抽也算进去，偏低。
    avg_stmt = _apply_filters(
        select(
            func.avg(PullRecord.pity_5_before + 1),
            func.max(PullRecord.pity_5_before + 1),
            func.min(PullRecord.pity_5_before + 1),
        ),
        user_id,
        f,
    ).where(PullRecord.star == 5)
    avg_row = (await db.execute(avg_stmt)).one()

    # 歪了几次：五星里非 UP 的数量
    lost = five - five_up

    return StatsOut(
        total_pulls=total,
        five_star_count=five,
        four_star_count=four,
        three_star_count=three,
        five_star_up_count=five_up,
        up_count=up_count,
        average_pulls_per_five_star=round(float(avg_row[0]), 2) if avg_row[0] else None,
        five_star_rate=round(five / total, 5) if total else 0.0,
        max_pulls_for_five_star=int(avg_row[1]) if avg_row[1] else None,
        min_pulls_for_five_star=int(avg_row[2]) if avg_row[2] else None,
        lost_5050_count=lost,
        pool_progress=await compute_pool_progress(db, user_id),
        applied_filters=f.applied(),
    )


async def compute_pool_progress(db: AsyncSession, user_id: int) -> list[PoolProgressOut]:
    """每个卡池的保底进度。

    包含管理员已停用的卡池：玩家的保底计数还在，进度应当能看到。
    """
    pools = await list_all_pools(db)
    if not pools:
        return []

    pity_rows = (
        (await db.execute(select(UserPity).where(UserPity.user_id == user_id))).scalars().all()
    )
    pity_by_group = {p.pity_group: p for p in pity_rows}

    epi_rows = (
        (await db.execute(select(UserEpitomizedPath).where(UserEpitomizedPath.user_id == user_id)))
        .scalars()
        .all()
    )
    epi_by_pool = {e.pool_id: e for e in epi_rows}

    counts = dict(
        (
            await db.execute(
                select(PullRecord.pool_id, func.count())
                .where(PullRecord.user_id == user_id, PullRecord.pool_id.isnot(None))
                .group_by(PullRecord.pool_id)
            )
        ).all()
    )

    out: list[PoolProgressOut] = []
    for pool in pools:
        rules = PoolRules.from_pool(pool)
        pity = pity_by_group.get(pool.pity_group)
        epi = epi_by_pool.get(pool.id)
        charted_name = None
        if epi and epi.charted_item_id:
            charted_name = next((i.name for i in pool.items if i.id == epi.charted_item_id), None)

        out.append(
            PoolProgressOut(
                pool_id=pool.id,
                pool_name=pool.name,
                pool_type=pool.pool_type,
                pity_5=pity.pity_5 if pity else 0,
                pity_4=pity.pity_4 if pity else 0,
                hard_pity_5=rules.hard_pity_5,
                hard_pity_4=rules.hard_pity_4,
                guaranteed_5=pity.guaranteed_5 if pity else False,
                total_pulls=int(counts.get(pool.id, 0)),
                charted_item_name=charted_name,
                fate_points=epi.fate_points if epi else 0,
                epitomized_max_points=(
                    rules.epitomized_max_points if rules.epitomized_enabled else 0
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# CSV 导出
# ---------------------------------------------------------------------------
CSV_HEADER = [
    "时间",
    "卡池",
    "卡池类型",
    "物品",
    "类型",
    "星级",
    "是否UP",
    "是否保底",
    "出金来源",
    "本抽消耗抽数",
    "批次ID",
]

POOL_TYPE_LABELS = {
    PoolType.CHARACTER: "角色活动祈愿",
    PoolType.WEAPON: "武器活动祈愿",
    PoolType.STANDARD: "常驻祈愿",
    PoolType.BEGINNER: "新手祈愿",
}

SOURCE_LABELS = {
    "base": "基础概率",
    "soft_pity": "软保底",
    "hard_pity": "硬保底",
    "guarantee": "大保底",
    "epitomized": "定轨",
    "radiance": "捕获明光",
}


def _csv_rows(rows: list[PullRecord]) -> list[list[str]]:
    out = []
    for r in rows:
        out.append(
            [
                r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
                r.pool_name_snapshot,
                POOL_TYPE_LABELS.get(r.pool_type_snapshot, r.pool_type_snapshot),
                r.item_name_snapshot,
                "角色" if r.item_type_snapshot == "character" else "武器",
                str(r.star),
                "是" if r.is_up else "否",
                "是" if r.is_pity else "否",
                SOURCE_LABELS.get(r.source, r.source),
                str(r.pity_5_before + 1) if r.star == 5 else "",
                r.batch_id,
            ]
        )
    return out


async def export_csv(db: AsyncSession, user_id: int, f: HistoryFilter) -> AsyncIterator[str]:
    """流式导出 CSV（时间倒序）。

    带 UTF-8 BOM：否则 Excel 在中文 Windows 上会用 GBK 解码，中文全变乱码。
    """
    stmt = _apply_filters(select(PullRecord), user_id, f)
    stmt = stmt.order_by(PullRecord.created_at.desc(), PullRecord.id.desc())
    stmt = stmt.limit(EXPORT_LIMIT)
    rows = (await db.execute(stmt)).scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    yield "﻿"  # BOM
    writer.writerow(CSV_HEADER)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for chunk in _csv_rows(rows):
        writer.writerow(chunk)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
