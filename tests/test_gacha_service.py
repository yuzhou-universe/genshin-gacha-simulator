"""抽卡服务集成测试：货币、事务、卡池限制、跨池保底继承、记录一致性。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.exceptions import (
    BadPullCount,
    EpitomizedDisabled,
    InsufficientCurrency,
    NotAFeaturedWeapon,
    PoolClosed,
    PullCapExceeded,
)
from app.models import PRIMOGEMS_PER_FATE, CurrencyType, PoolType, PullRecord
from app.services.gacha_service import GachaService, get_or_create_currency
from app.services.rng import ScriptedRandomSource


async def get_pool_by_type(db, pool_type: str):
    from app.models import Pool

    result = await db.execute(select(Pool).where(Pool.pool_type == pool_type))
    return result.scalars().first()


# ===========================================================================
# 货币与事务
# ===========================================================================
async def test_single_pull_deducts_one_fate(seeded_db, user, pool_id):
    service = GachaService(seeded_db)
    before = user.currency.intertwined_fate

    result = await service.pull(user, pool_id, 1)

    assert result.cost_amount == 1
    assert result.cost_currency == CurrencyType.INTERTWINED_FATE
    assert user.currency.intertwined_fate == before - 1
    assert user.currency.primogems == 16000


async def test_ten_pull_deducts_ten(seeded_db, user, pool_id):
    service = GachaService(seeded_db)
    before = user.currency.intertwined_fate

    await service.pull(user, pool_id, 10)

    assert user.currency.intertwined_fate == before - 10


async def test_insufficient_currency_changes_nothing(seeded_db, user, pool_id):
    """余额不足时：不扣钱、不产生记录、不推进保底、不消耗随机数。"""
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 0
    currency.primogems = 100  # 不够 160 换 1 个
    await seeded_db.commit()

    rng = ScriptedRandomSource()
    service = GachaService(seeded_db)
    with pytest.raises(InsufficientCurrency):
        await service.pull(user, pool_id, 1, rng=rng)

    records = await seeded_db.execute(
        select(func.count()).select_from(PullRecord).where(PullRecord.user_id == user.id)
    )
    assert int(records.scalar_one()) == 0, "失败的抽卡不能留下任何记录"
    assert rng.calls == 0, "扣费失败时不允许消耗随机数"

    from app.services.gacha_service import get_or_create_pity

    pity = await get_or_create_pity(seeded_db, user.id, PoolType.CHARACTER)
    assert pity.pity_5 == 0, "失败的抽卡不能推进保底"


async def test_primogems_auto_convert_when_fates_run_out(seeded_db, user, pool_id):
    """道具不足时按 160:1 自动用原石补足。"""
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 0
    currency.primogems = 160 * 3
    await seeded_db.commit()

    await GachaService(seeded_db).pull(user, pool_id, 1)

    assert currency.intertwined_fate == 0
    assert currency.primogems == 160 * 3 - PRIMOGEMS_PER_FATE


async def test_ten_pull_writes_ten_records_in_one_batch(seeded_db, user, pool_id):
    result = await GachaService(seeded_db).pull(user, pool_id, 10)

    rows = (
        (await seeded_db.execute(select(PullRecord).where(PullRecord.batch_id == result.batch_id)))
        .scalars()
        .all()
    )

    assert len(rows) == 10
    assert [r.pull_index_in_batch for r in rows] == list(range(1, 11))
    # 批次消耗只记在首行，因此 SUM 等于真实总消耗，而不是被重复计十次
    assert sum(r.cost_amount for r in rows) == 10
    assert rows[0].cost_amount == 10
    assert all(r.cost_amount == 0 for r in rows[1:])


async def test_records_snapshot_item_name(seeded_db, user, pool_id):
    """记录里存物品名快照，管理员改动物品后历史依然可读。"""
    result = await GachaService(seeded_db).pull(user, pool_id, 1)

    row = (
        await seeded_db.execute(select(PullRecord).where(PullRecord.batch_id == result.batch_id))
    ).scalar_one()

    assert row.item_name_snapshot == result.items[0].name
    assert row.pool_name_snapshot == "角色活动祈愿"


# ===========================================================================
# 抽数限制
# ===========================================================================
@pytest.mark.parametrize("count", [0, 2, 5, 11, 1000])
async def test_rejects_counts_outside_whitelist(seeded_db, user, pool_id, count):
    with pytest.raises(BadPullCount):
        await GachaService(seeded_db).pull(user, pool_id, count)


async def test_beginner_pool_rejects_single_pull(seeded_db, user):
    """新手池只能十连（与线上一致）。"""
    pool = await get_pool_by_type(seeded_db, PoolType.BEGINNER)
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.acquaint_fate = 100
    currency.primogems = 100_000
    await seeded_db.commit()

    with pytest.raises(BadPullCount):
        await GachaService(seeded_db).pull(user, pool.id, 1)


async def test_beginner_pool_ten_pull_costs_eight(seeded_db, user):
    """新手池十连八折：8 个相遇之缘。"""
    pool = await get_pool_by_type(seeded_db, PoolType.BEGINNER)
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.acquaint_fate = 100
    await seeded_db.commit()

    result = await GachaService(seeded_db).pull(user, pool.id, 10)

    assert result.cost_amount == 8
    assert currency.acquaint_fate == 92


async def test_beginner_pool_lifetime_cap_of_20(seeded_db, user):
    """新手池终身只能抽 20 次，第三次十连被拒。"""
    pool = await get_pool_by_type(seeded_db, PoolType.BEGINNER)
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.acquaint_fate = 100
    await seeded_db.commit()

    service = GachaService(seeded_db)
    await service.pull(user, pool.id, 10)
    await service.pull(user, pool.id, 10)

    with pytest.raises(PullCapExceeded):
        await service.pull(user, pool.id, 10)


async def test_beginner_pool_does_not_touch_standard_pity(seeded_db, user):
    """★ 新手池的保底必须独立。

    若共用保底组，8 折的相遇之缘会免费推进常驻池的保底计数 —— 经济漏洞。
    """
    beginner = await get_pool_by_type(seeded_db, PoolType.BEGINNER)
    standard = await get_pool_by_type(seeded_db, PoolType.STANDARD)
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.acquaint_fate = 100
    await seeded_db.commit()

    await GachaService(seeded_db).pull(user, beginner.id, 10)

    from app.services.gacha_service import get_or_create_pity

    standard_pity = await get_or_create_pity(seeded_db, user.id, standard.pity_group)
    beginner_pity = await get_or_create_pity(seeded_db, user.id, beginner.pity_group)

    assert beginner_pity.pity_5 == 10 or beginner_pity.pity_5 == 0
    assert standard_pity.pity_5 == 0, "常驻池保底不能被新手池推进"


async def test_closed_pool_rejects_pull(seeded_db, user, pool_id):
    from app.models import Pool

    pool = await seeded_db.get(Pool, pool_id)
    pool.is_current = False
    await seeded_db.commit()

    with pytest.raises(PoolClosed):
        await GachaService(seeded_db).pull(user, pool_id, 1)


# ===========================================================================
# 保底跨卡池继承
# ===========================================================================
async def test_pity_carries_across_pools_in_same_group(seeded_db, user, pool_id):
    """同保底组的两个卡池共享计数（角色活动祈愿之间互通）。"""
    from app.models import Pool

    original = await seeded_db.get(Pool, pool_id)
    clone = Pool(
        key="character-event-2",
        name="角色活动祈愿·其二",
        pool_type=PoolType.CHARACTER,
        pity_group=original.pity_group,  # 同一个保底组
        cost_currency=CurrencyType.INTERTWINED_FATE,
        is_current=True,
    )
    for item in original.items:
        clone.items.append(
            type(item)(
                name=item.name,
                item_type=item.item_type,
                star=item.star,
                is_up=item.is_up,
                weight=item.weight,
            )
        )
    seeded_db.add(clone)
    await seeded_db.commit()

    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 200
    await seeded_db.commit()

    service = GachaService(seeded_db)
    # 在第一个池抽 30 次（用中性随机源，不出金）
    for _ in range(3):
        await service.pull(user, pool_id, 10, rng=ScriptedRandomSource())

    from app.services.gacha_service import get_or_create_pity

    pity = await get_or_create_pity(seeded_db, user.id, original.pity_group)
    assert pity.pity_5 == 30

    # 换到第二个池再抽 60 次：计数在第 90 抽时触顶，硬保底必须在新卡池上兑现。
    # 5×10 = 50 抽时计数到 80，第 6 个十连里第 10 抽正好是第 90 抽。
    for _ in range(5):
        await service.pull(user, clone.id, 10, rng=ScriptedRandomSource())
    result = await service.pull(user, clone.id, 10, rng=ScriptedRandomSource())

    assert result.items[-1].star == 5, "第 90 抽的硬保底应在新卡池上兑现"
    assert result.items[-1].source == "hard_pity"

    # 保底计数是共享的，出金后同一个 UserPity 行被清零
    await seeded_db.refresh(pity)
    assert pity.pity_5 == 0


async def test_different_pity_groups_are_independent(seeded_db, user, pool_id):
    """角色池与武器池的保底计数各自独立推进，互不影响。"""
    weapon = await get_pool_by_type(seeded_db, PoolType.WEAPON)
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 200
    await seeded_db.commit()

    service = GachaService(seeded_db)
    rng = ScriptedRandomSource()  # 中性随机源：10 抽内不会自然出金

    # 先在角色池抽 10 次
    await service.pull(user, pool_id, 10, rng=rng)

    from app.services.gacha_service import get_or_create_pity

    char_pity = await get_or_create_pity(seeded_db, user.id, PoolType.CHARACTER)
    weapon_pity = await get_or_create_pity(seeded_db, user.id, PoolType.WEAPON)

    assert char_pity.pity_5 == 10
    assert weapon_pity.pity_5 == 0, "武器池保底不能被角色池的抽卡推进"

    # 再到武器池抽 10 次：两边各自累加，互不干扰
    await service.pull(user, weapon.id, 10, rng=rng)

    assert char_pity.pity_5 == 10, "角色池保底不应被武器池的抽卡改动"
    assert weapon_pity.pity_5 == 10


# ===========================================================================
# 定轨
# ===========================================================================
async def test_epitomized_disabled_on_character_pool(seeded_db, user, pool_id):
    with pytest.raises(EpitomizedDisabled):
        await GachaService(seeded_db).set_epitomized_path(user, pool_id, 1)


async def test_epitomized_rejects_non_featured_weapon(seeded_db, user):
    weapon = await get_pool_by_type(seeded_db, PoolType.WEAPON)
    off_banner = next(i for i in weapon.items if i.star == 5 and not i.is_up)

    with pytest.raises(NotAFeaturedWeapon):
        await GachaService(seeded_db).set_epitomized_path(user, weapon.id, off_banner.id)


async def test_epitomized_reselect_same_weapon_keeps_points(seeded_db, user):
    """重选同一把武器不重置命定值 —— 否则前端双击确认就会清空已攒的进度。"""
    weapon = await get_pool_by_type(seeded_db, PoolType.WEAPON)
    target = next(i for i in weapon.items if i.star == 5 and i.is_up)

    service = GachaService(seeded_db)
    await service.set_epitomized_path(user, weapon.id, target.id)

    from app.services.gacha_service import get_or_create_epitomized

    state = await get_or_create_epitomized(seeded_db, user.id, weapon.id)
    state.fate_points = 1
    await seeded_db.commit()

    result = await service.set_epitomized_path(user, weapon.id, target.id)

    assert result.fate_points == 1, "重选同一把应当保留命定值"


async def test_epitomized_switch_weapon_resets_points(seeded_db, user):
    weapon = await get_pool_by_type(seeded_db, PoolType.WEAPON)
    up = [i for i in weapon.items if i.star == 5 and i.is_up]

    service = GachaService(seeded_db)
    await service.set_epitomized_path(user, weapon.id, up[0].id)

    from app.services.gacha_service import get_or_create_epitomized

    state = await get_or_create_epitomized(seeded_db, user.id, weapon.id)
    state.fate_points = 1
    await seeded_db.commit()

    result = await service.set_epitomized_path(user, weapon.id, up[1].id)
    assert result.fate_points == 0, "换定轨武器必须清零命定值"


async def test_epitomized_cancel_resets_points(seeded_db, user):
    weapon = await get_pool_by_type(seeded_db, PoolType.WEAPON)
    target = next(i for i in weapon.items if i.star == 5 and i.is_up)

    service = GachaService(seeded_db)
    await service.set_epitomized_path(user, weapon.id, target.id)

    from app.services.gacha_service import get_or_create_epitomized

    state = await get_or_create_epitomized(seeded_db, user.id, weapon.id)
    state.fate_points = 1
    await seeded_db.commit()

    result = await service.set_epitomized_path(user, weapon.id, None)
    assert result.fate_points == 0
    assert result.charted_item_id is None


async def test_epitomized_forced_pull_yields_charted_weapon(seeded_db, user):
    """命定值满 → 下一个五星必为定轨武器，端到端验证。"""
    weapon = await get_pool_by_type(seeded_db, PoolType.WEAPON)
    target = next(i for i in weapon.items if i.star == 5 and i.is_up)
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 200
    await seeded_db.commit()

    service = GachaService(seeded_db)
    await service.set_epitomized_path(user, weapon.id, target.id)

    from app.services.gacha_service import get_or_create_epitomized

    state = await get_or_create_epitomized(seeded_db, user.id, weapon.id)
    state.fate_points = weapon.epitomized_max_points  # 直接置满
    await seeded_db.commit()

    rng = ScriptedRandomSource().five_star(1).lose_5050(1).pick(1, 0.99)
    result = await service.pull(user, weapon.id, 1, rng=rng)

    assert result.items[0].star == 5
    assert result.items[0].name == target.name
    assert result.items[0].source == "epitomized"

    await seeded_db.refresh(state)
    assert state.fate_points == 0


# ===========================================================================
# 计数器一致性
# ===========================================================================
async def test_pity_recomputed_from_history_matches_live_state(seeded_db, user, pool_id):
    """只凭 PullRecord 重算保底，必须与 UserPity 现值完全一致。

    这是捕捉计数器漂移最强的单条测试：任何"某条分支忘了更新计数器"的
    bug 都会让两者对不上。
    """
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 500
    await seeded_db.commit()

    service = GachaService(seeded_db)
    rng = ScriptedRandomSource()  # 中性随机源：只靠保底出金
    for _ in range(5):
        await service.pull(user, pool_id, 10, rng=rng)

    rows = (
        (
            await seeded_db.execute(
                select(PullRecord)
                .where(PullRecord.user_id == user.id, PullRecord.pool_id == pool_id)
                .order_by(PullRecord.id)
            )
        )
        .scalars()
        .all()
    )

    # 从记录重算
    pity_5 = 0
    pity_4 = 0
    for row in rows:
        if row.star == 5:
            pity_5 = 0
            pity_4 += 1  # ★ 出 5★ 不重置 4★ 计数器
        elif row.star == 4:
            pity_4 = 0
            pity_5 += 1
        else:
            pity_5 += 1
            pity_4 += 1

    from app.services.gacha_service import get_or_create_pity

    live = await get_or_create_pity(seeded_db, user.id, PoolType.CHARACTER)

    assert live.pity_5 == pity_5, f"5★ 计数器漂移：库里 {live.pity_5}，重算 {pity_5}"
    assert live.pity_4 == pity_4, f"4★ 计数器漂移：库里 {live.pity_4}，重算 {pity_4}"


async def test_pity_5_before_is_recorded_correctly(seeded_db, user, pool_id):
    """pity_5_before + 1 必须等于该金实际消耗的抽数（供平均出金统计使用）。"""
    currency = await get_or_create_currency(seeded_db, user.id)
    currency.intertwined_fate = 500
    await seeded_db.commit()

    service = GachaService(seeded_db)
    rng = ScriptedRandomSource()  # 中性随机源，只靠硬保底出金
    # 8 个十连 = 80 抽；第 9 个十连的第 10 抽正好是第 90 抽
    for _ in range(8):
        await service.pull(user, pool_id, 10, rng=rng)
    result = await service.pull(user, pool_id, 10, rng=rng)

    assert result.items[-1].star == 5, "第 90 抽必须出金"
    assert result.items[-1].index == 10
    assert result.items[-1].source == "hard_pity"

    row = (
        await seeded_db.execute(
            select(PullRecord).where(PullRecord.batch_id == result.batch_id, PullRecord.star == 5)
        )
    ).scalar_one()
    assert row.pity_5_before + 1 == 90, "第 90 抽出金时 pity_5_before 应为 89"
