"""历史记录、统计与导出测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import PoolType, PullRecord
from app.services import history_service
from app.services.gacha_service import GachaService, get_or_create_currency
from app.services.history_service import HistoryFilter
from app.services.rng import ScriptedRandomSource


async def _seed_records(db, user, pool_id, batches=3, rng=None):
    """跑若干次十连，返回结果列表。"""
    currency = await get_or_create_currency(db, user.id)
    currency.intertwined_fate = 500
    currency.primogems = 500_000
    await db.commit()

    service = GachaService(db)
    out = []
    for _ in range(batches):
        out.append(await service.pull(user, pool_id, 10, rng=rng or ScriptedRandomSource()))
    return out


# ===========================================================================
# 分页与筛选
# ===========================================================================
async def test_history_is_paginated_and_time_descending(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=3)  # 30 条

    page1 = await history_service.query_history(
        seeded_db, user.id, HistoryFilter(), page=1, page_size=10
    )

    assert page1.total == 30
    assert page1.pages == 3
    assert len(page1.items) == 10
    # 时间倒序：同一批次内 created_at 完全相同，靠 id 兜底保证稳定
    ids = [i.id for i in page1.items]
    assert ids == sorted(ids, reverse=True)

    page2 = await history_service.query_history(
        seeded_db, user.id, HistoryFilter(), page=2, page_size=10
    )
    # 分页之间不能有重复
    assert not (set(ids) & {i.id for i in page2.items})


async def test_filter_by_star(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=3)

    f = HistoryFilter(star=3)
    page = await history_service.query_history(seeded_db, user.id, f, page_size=200)

    assert all(i.star == 3 for i in page.items)
    assert page.total == len(page.items)


async def test_filter_by_pool(seeded_db, user, pool_id):
    """筛选某个卡池时，其它卡池的记录不能混进来。"""
    from sqlalchemy import select as sa_select

    from app.models import Pool

    weapon = (
        (await seeded_db.execute(sa_select(Pool).where(Pool.pool_type == PoolType.WEAPON)))
        .scalars()
        .first()
    )

    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 500
    await seeded_db.commit()

    service = GachaService(seeded_db)
    await service.pull(user, pool_id, 10, rng=ScriptedRandomSource())
    await service.pull(user, weapon.id, 10, rng=ScriptedRandomSource())

    page = await history_service.query_history(
        seeded_db, user.id, HistoryFilter(pool_id=pool_id), page_size=200
    )

    assert page.total == 10
    assert all(i.pool_id == pool_id for i in page.items)


async def test_filter_by_date_range(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=1)

    today = datetime.now(UTC).date()

    hit = await history_service.query_history(
        seeded_db, user.id, HistoryFilter(date_from=today, date_to=today), page_size=200
    )
    assert hit.total == 10, "结束日期应当包含当天"

    past = await history_service.query_history(
        seeded_db,
        user.id,
        HistoryFilter(date_from=today - timedelta(days=5), date_to=today - timedelta(days=3)),
        page_size=200,
    )
    assert past.total == 0


async def test_filter_by_is_up(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=2)

    page = await history_service.query_history(
        seeded_db, user.id, HistoryFilter(is_up=True), page_size=200
    )
    assert all(i.is_up for i in page.items)

    page = await history_service.query_history(
        seeded_db, user.id, HistoryFilter(is_up=False), page_size=200
    )
    assert all(not i.is_up for i in page.items)


# ===========================================================================
# 统计
# ===========================================================================
async def test_stats_on_constructed_data(seeded_db, user, pool_id):
    """用人工构造的记录做精确断言，不依赖随机结果。"""
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    rows = [
        # 第 5 抽出一金（UP）
        {"star": 3, "is_up": False, "pity_5_before": 0},
        {"star": 3, "is_up": False, "pity_5_before": 1},
        {"star": 4, "is_up": True, "pity_5_before": 2},
        {"star": 3, "is_up": False, "pity_5_before": 3},
        {"star": 5, "is_up": True, "pity_5_before": 4},
        # 第 3 抽出一金（歪）
        {"star": 3, "is_up": False, "pity_5_before": 0},
        {"star": 4, "is_up": False, "pity_5_before": 1},
        {"star": 5, "is_up": False, "pity_5_before": 2},
        {"star": 3, "is_up": False, "pity_5_before": 0},
    ]
    for n, spec in enumerate(rows):
        seeded_db.add(
            PullRecord(
                user_id=user.id,
                pool_id=pool_id,
                pool_name_snapshot="角色活动祈愿",
                pool_type_snapshot=PoolType.CHARACTER,
                item_name_snapshot=f"物品{n}",
                item_type_snapshot="character",
                batch_id="b1",
                pull_index_in_batch=n + 1,
                created_at=base + timedelta(seconds=n),
                **spec,
            )
        )
    await seeded_db.commit()

    stats = await history_service.compute_stats(seeded_db, user.id, HistoryFilter())

    assert stats.total_pulls == 9
    assert stats.five_star_count == 2
    assert stats.four_star_count == 2
    assert stats.three_star_count == 5
    assert stats.five_star_up_count == 1
    assert stats.up_count == 2
    assert stats.lost_5050_count == 1
    # 两次出金分别用了 5 抽与 3 抽 → 平均 4.0
    assert stats.average_pulls_per_five_star == 4.0
    assert stats.max_pulls_for_five_star == 5
    assert stats.min_pulls_for_five_star == 3
    assert stats.five_star_rate == pytest.approx(2 / 9, abs=1e-5)


async def test_stats_respect_filters(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=2)

    all_stats = await history_service.compute_stats(seeded_db, user.id, HistoryFilter())
    star3 = await history_service.compute_stats(seeded_db, user.id, HistoryFilter(star=3))

    assert star3.total_pulls == all_stats.three_star_count
    assert star3.total_pulls < all_stats.total_pulls


async def test_stats_handles_zero_pulls(seeded_db, user):
    """没有任何记录时不能除零崩溃。"""
    stats = await history_service.compute_stats(seeded_db, user.id, HistoryFilter())

    assert stats.total_pulls == 0
    assert stats.five_star_count == 0
    assert stats.average_pulls_per_five_star is None
    assert stats.five_star_rate == 0.0


async def test_average_pulls_ignores_unresolved_pity(seeded_db, user, pool_id):
    """平均出金抽数只统计已出金的抽，不把当前垫的抽算进分母。

    用 total/five 计算会把垫抽算进去导致偏低，这里锁死正确口径。
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 一次 90 抽出金，外加 50 抽未出金的垫抽
    for n in range(90):
        seeded_db.add(
            PullRecord(
                user_id=user.id,
                pool_id=pool_id,
                pool_name_snapshot="角色活动祈愿",
                pool_type_snapshot=PoolType.CHARACTER,
                item_name_snapshot="x",
                star=5 if n == 89 else 3,
                batch_id="b",
                pull_index_in_batch=n + 1,
                pity_5_before=n if n == 89 else n,
                created_at=base + timedelta(seconds=n),
            )
        )
    for n in range(50):
        seeded_db.add(
            PullRecord(
                user_id=user.id,
                pool_id=pool_id,
                pool_name_snapshot="角色活动祈愿",
                pool_type_snapshot=PoolType.CHARACTER,
                item_name_snapshot="y",
                star=3,
                batch_id="c",
                pull_index_in_batch=n + 1,
                pity_5_before=n,
                created_at=base + timedelta(seconds=200 + n),
            )
        )
    await seeded_db.commit()

    stats = await history_service.compute_stats(seeded_db, user.id, HistoryFilter())

    assert stats.total_pulls == 140
    assert stats.five_star_count == 1
    # 该金用掉 90 抽 → 平均就是 90，而不是 140/1
    assert stats.average_pulls_per_five_star == 90.0


async def test_pool_progress_reports_each_banner(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=2)

    progress = await history_service.compute_pool_progress(seeded_db, user.id)
    by_type = {p.pool_type: p for p in progress}

    assert PoolType.CHARACTER in by_type
    assert by_type[PoolType.CHARACTER].pity_5 == 20
    assert by_type[PoolType.CHARACTER].total_pulls == 20
    # 其它卡池没有被抽过
    assert by_type[PoolType.STANDARD].total_pulls == 0


async def test_pool_progress_includes_epitomized_state(seeded_db, user):
    from sqlalchemy import select as sa_select

    from app.models import Pool
    from app.services.gacha_service import get_or_create_epitomized

    weapon = (
        (await seeded_db.execute(sa_select(Pool).where(Pool.pool_type == PoolType.WEAPON)))
        .scalars()
        .first()
    )
    target = next(i for i in weapon.items if i.star == 5 and i.is_up)

    service = GachaService(seeded_db)
    await service.set_epitomized_path(user, weapon.id, target.id)

    state = await get_or_create_epitomized(seeded_db, user.id, weapon.id)
    state.fate_points = 1
    await seeded_db.commit()

    progress = await history_service.compute_pool_progress(seeded_db, user.id)
    weapon_progress = next(p for p in progress if p.pool_type == PoolType.WEAPON)

    assert weapon_progress.charted_item_name == target.name
    assert weapon_progress.fate_points == 1
    assert weapon_progress.epitomized_max_points == weapon.epitomized_max_points


# ===========================================================================
# CSV 导出
# ===========================================================================
async def test_csv_export_has_bom_and_header(seeded_db, user, pool_id):
    """导出必须以 UTF-8 BOM 开头，否则 Excel 在中文 Windows 上全是乱码。"""
    await _seed_records(seeded_db, user, pool_id, batches=1)

    chunks = [c async for c in history_service.export_csv(seeded_db, user.id, HistoryFilter())]
    text = "".join(chunks)

    assert text.startswith("﻿"), "缺少 UTF-8 BOM"
    assert "时间" in text and "卡池" in text and "星级" in text

    lines = [ln for ln in text.split("\n") if ln.strip()]
    assert len(lines) == 1 + 10, "表头 + 10 条记录"


async def test_csv_export_is_time_descending(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=2)

    chunks = [c async for c in history_service.export_csv(seeded_db, user.id, HistoryFilter())]
    lines = [ln for ln in "".join(chunks).split("\n") if ln.strip()][1:]

    times = [ln.split(",")[0] for ln in lines]
    assert times == sorted(times, reverse=True)


async def test_csv_export_respects_filters(seeded_db, user, pool_id):
    await _seed_records(seeded_db, user, pool_id, batches=2)

    chunks = [
        c async for c in history_service.export_csv(seeded_db, user.id, HistoryFilter(star=5))
    ]
    lines = [ln for ln in "".join(chunks).split("\n") if ln.strip()][1:]

    # 中性随机源下 20 抽内不会出金
    assert lines == []


# ===========================================================================
# 越权
# ===========================================================================
async def test_cannot_see_other_users_records(seeded_db, user, pool_id):
    from app.services import auth_service

    await _seed_records(seeded_db, user, pool_id, batches=1)
    other = await auth_service.register(seeded_db, "intruder", "Intruder123")

    page = await history_service.query_history(seeded_db, other.id, HistoryFilter(), page_size=200)
    stats = await history_service.compute_stats(seeded_db, other.id, HistoryFilter())

    assert page.total == 0, "不能看到别人的记录"
    assert stats.total_pulls == 0


async def test_export_only_contains_own_records(seeded_db, user, pool_id):
    from app.services import auth_service

    await _seed_records(seeded_db, user, pool_id, batches=1)
    other = await auth_service.register(seeded_db, "intruder", "Intruder123")

    chunks = [c async for c in history_service.export_csv(seeded_db, other.id, HistoryFilter())]
    lines = [ln for ln in "".join(chunks).split("\n") if ln.strip()]

    assert len(lines) == 1, "别人的导出里只应有表头"


async def test_records_survive_item_deletion(seeded_db, user, pool_id):
    """物品快照保证历史在管理员删物品后依然可读。"""
    await _seed_records(seeded_db, user, pool_id, batches=1)

    rows = (await seeded_db.execute(select(PullRecord))).scalars().all()
    names_before = {r.item_name_snapshot for r in rows}
    assert all(names_before), "快照名不能为空"

    # 删掉卡池（级联删除物品）
    from app.models import Pool

    pool = await seeded_db.get(Pool, pool_id)
    await seeded_db.delete(pool)
    await seeded_db.commit()

    page = await history_service.query_history(seeded_db, user.id, HistoryFilter(), page_size=200)
    assert page.total == 10, "删池后历史记录必须仍在"
    assert {i.item_name for i in page.items} == names_before
