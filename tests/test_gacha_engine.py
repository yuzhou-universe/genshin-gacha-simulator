"""抽卡引擎规则测试。

全部使用 ``ScriptedRandomSource`` 精确指定结果，**不依赖真实随机数**，
因此不存在偶发失败。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.base import utcnow
from app.models.enums import ItemType, PoolType
from app.services.gacha_engine import (
    SOURCE_EPITOMIZED,
    SOURCE_GUARANTEE,
    SOURCE_HARD_PITY,
    EpitomizedState,
    PityState,
    apply_banner_end_reset,
)
from app.services.pool_rules import PoolRules
from app.services.rng import NEUTRAL, ScriptedRandomSource, u_for_item
from tests.conftest import make_engine, weapon_items


# ===========================================================================
# 硬保底
# ===========================================================================
def test_hard_pity_fires_exactly_at_90():
    """角色池第 90 抽必出 5★，且之前一抽都不出。"""
    engine = make_engine(pool_type=PoolType.CHARACTER)

    outcomes = [engine.pull_once() for _ in range(90)]

    five_stars = [o for o in outcomes if o.star == 5]
    assert len(five_stars) == 1, "90 抽内应恰好出一金"
    assert five_stars[0].index == 90, "这一金必须落在第 90 抽"
    assert five_stars[0].source == SOURCE_HARD_PITY
    assert five_stars[0].pity_5_before == 89
    assert engine.pity.pity_5 == 0


def test_no_five_star_before_hard_pity_with_neutral_rng():
    """中性随机数（0.999999）下，89 抽内一金不出。"""
    engine = make_engine(pool_type=PoolType.CHARACTER)
    outcomes = [engine.pull_once() for _ in range(89)]

    assert all(o.star != 5 for o in outcomes)
    assert engine.pity.pity_5 == 89


def test_weapon_soft_pity_reaches_100_before_hard_pity():
    """武器池软保底 +7%/抽，第 77 抽即到 100%，硬保底 80 形同虚设。

    这不是 bug，是参数使然；但管理员必须知道，否则会以为"调硬保底没反应"。
    """
    engine = make_engine(pool_type=PoolType.WEAPON)
    outcomes = [engine.pull_once() for _ in range(80)]

    five_stars = [o for o in outcomes if o.star == 5]
    assert len(five_stars) == 1
    assert five_stars[0].index == 77, "武器池默认参数下应在第 77 抽到达 100%"


def test_soft_pity_can_be_disabled():
    """关闭软保底后，只有第 90 抽才出金。"""
    rules = PoolRules.character(soft_pity_enabled=False)
    engine = make_engine(rules=rules)
    outcomes = [engine.pull_once() for _ in range(90)]

    five_stars = [o for o in outcomes if o.star == 5]
    assert len(five_stars) == 1
    assert five_stars[0].index == 90


# ===========================================================================
# ★ 铁律一：4★ 计数器不被 5★ 重置
# ===========================================================================
def test_five_star_does_not_reset_four_star_counter():
    """出 5★ 时，4★ 计数器只 +1，不归零。

    pity_4 的语义是"距上一个 4★ 的抽数"，不是"距上一个 4★ 或以上"。
    本次 5★ 只是"满足"了本抽的 4★-或以上保底，该保底顺延到下一抽。
    """
    pity = PityState(pity_4=9)
    rng = ScriptedRandomSource().five_star(1)
    engine = make_engine(pity=pity, rng=rng)

    engine.pull_once()

    assert engine.pity.pity_4 == 10, (
        "出 5★ 后 4★ 计数器应为 10（9+1），而不是被重置为 0 —— "
        "重置会导致「出了金就吞掉一次四星保底」的经典 bug"
    )


def test_four_star_guarantee_deferred_past_a_five_star():
    """4★ 保底被 5★ 顺延后，下一抽必须兑现。"""
    pity = PityState(pity_4=9)
    rng = ScriptedRandomSource().five_star(1)
    engine = make_engine(pity=pity, rng=rng)

    first = engine.pull_once()
    assert first.star == 5

    second = engine.pull_once()
    assert second.star == 4, "被顺延的四星保底必须在下一次兑现"
    assert engine.pity.pity_4 == 0


def test_four_star_guarantee_deferred_within_a_ten_pull():
    """十连**内部**同样要顺延：第 1 抽出金，第 2 抽必须补上 4★。"""
    pity = PityState(pity_4=9)
    rng = ScriptedRandomSource().five_star(1)
    engine = make_engine(pity=pity, rng=rng)

    outcomes = [engine.pull_once() for _ in range(10)]

    assert outcomes[0].star == 5
    assert (
        outcomes[1].star == 4
    ), "十连内部若快照了保底状态，这里会退化成 3★ —— 每抽都必须读写实时状态"


def test_four_star_counter_can_exceed_hard_pity():
    """pity_4 允许超过 10：4★ 保底那一抽被 5★ 判定抢先命中时就会这样。

    因此比较一律用 >=，也不能断言或存储 clamp 到 10。
    """
    pity = PityState(pity_4=10)
    rng = ScriptedRandomSource().five_star(1)
    engine = make_engine(pity=pity, rng=rng)

    engine.pull_once()
    assert engine.pity.pity_4 == 11


# ===========================================================================
# 50-50 与大保底
# ===========================================================================
def test_losing_5050_grants_guarantee_and_marks_not_up():
    rng = ScriptedRandomSource().five_star(1).lose_5050(1)
    engine = make_engine(rng=rng)

    outcome = engine.pull_once()

    assert outcome.star == 5
    assert outcome.is_up is False
    assert outcome.item.item_type == ItemType.CHARACTER
    assert engine.pity.guaranteed_5 is True
    assert engine.pity.pity_5 == 0


def test_guarantee_yields_up_item_without_rolling():
    """大保底直接给 UP，且不消耗 50-50 判定（用发牌器验证）。"""
    pity = PityState(guaranteed_5=True)
    # 注意：这里故意把分支判定设成"歪"侧，若实现正确它应当被忽略
    rng = ScriptedRandomSource().five_star(1).lose_5050(1).pick(1, 0.0)
    engine = make_engine(pity=pity, rng=rng)

    outcome = engine.pull_once()

    assert outcome.is_up is True
    assert outcome.source == SOURCE_GUARANTEE
    assert engine.pity.guaranteed_5 is False


def test_lost_5050_offbanner_is_right_item_type():
    """角色池歪了只能掉角色，绝不能掉武器。"""
    rng = ScriptedRandomSource().five_star(1).lose_5050(1).pick(1, 0.99)
    engine = make_engine(pool_type=PoolType.CHARACTER, rng=rng)

    outcome = engine.pull_once()

    assert outcome.is_up is False
    assert outcome.item.item_type == ItemType.CHARACTER


def test_weapon_banner_offbanner_is_weapon_not_character():
    """武器池歪了只能掉武器。"""
    rng = ScriptedRandomSource().five_star(1).lose_5050(1).pick(1, 0.99)
    engine = make_engine(pool_type=PoolType.WEAPON, rng=rng)

    outcome = engine.pull_once()

    assert outcome.is_up is False
    assert outcome.item.item_type == ItemType.WEAPON


def test_standard_banner_has_no_up():
    """常驻池无 UP：即便把分支判定设成必胜，也不会走 UP 逻辑。"""
    rng = ScriptedRandomSource().five_star(1).win_5050(1).pick(1, 0.0)
    engine = make_engine(pool_type=PoolType.STANDARD, rng=rng)

    outcome = engine.pull_once()

    assert outcome.star == 5
    assert engine.pity.guaranteed_5 is False


# ===========================================================================
# 捕获明光
# ===========================================================================
def test_radiance_disabled_by_default_always_loses_when_scripted():
    rules = PoolRules.character(capturing_radiance_enabled=False)
    pity = PityState(radiance_streak=3)
    rng = ScriptedRandomSource().five_star(1).lose_5050(1)
    engine = make_engine(rules=rules, pity=pity, rng=rng)

    assert engine.pull_once().is_up is False


def test_radiance_forces_up_after_three_consecutive_losses():
    rules = PoolRules.character(capturing_radiance_enabled=True)
    pity = PityState(radiance_streak=3)
    rng = ScriptedRandomSource().five_star(1).lose_5050(1)
    engine = make_engine(rules=rules, pity=pity, rng=rng)

    outcome = engine.pull_once()

    assert outcome.is_up is True, "连歪 3 次后应被捕获明光强制拉回 UP"
    assert engine.pity.radiance_streak == 0


def test_radiance_streak_increments_on_loss():
    rules = PoolRules.character(capturing_radiance_enabled=True)
    rng = ScriptedRandomSource().five_star(1).lose_5050(1)
    engine = make_engine(rules=rules, rng=rng)

    engine.pull_once()
    assert engine.pity.radiance_streak == 1


# ===========================================================================
# 武器定轨
# ===========================================================================
@pytest.mark.parametrize("max_points", [1, 2])
def test_epitomized_path_full_cycle(max_points):
    """走满定轨：命定值累积到上限后，下一个 5★ 必为定轨武器。

    按真实玩家路径展开，而不是简单地重复"歪"：
    在 75/25 上歪一次会同时带来大保底与 +1 命定值，于是**下一个金必定是两把
    UP 之一**——若恰好是另一把 UP 武器，命定值再 +1（这正是官方规则：
    拿到非定轨的五星武器就加点，不只是歪了才加）。
    """
    rules = PoolRules.weapon(epitomized_max_points=max_points)
    items = weapon_items()
    charted, other_up = items[0], items[1]  # 护摩之杖 / 若水
    state = EpitomizedState(charted_item_id=charted.id, fate_points=0)
    engine = make_engine(pool_type=PoolType.WEAPON, rules=rules, items=items, epitomized=state)

    # ---- 第 1 金：在 75/25 上歪了 → 大保底置位 + 命定值 1 ----
    engine.rng = ScriptedRandomSource().five_star(1).lose_5050(1).pick(1, 0.0)
    first = engine.pull_once()

    assert first.is_up is False
    assert first.item.id not in (charted.id, other_up.id), "歪出来的应是常驻五星武器"
    assert engine.pity.guaranteed_5 is True
    assert state.fate_points == 1

    # ---- 第 2 金（仅 max_points=2 时需要）：大保底给的是**另一把** UP ----
    if max_points == 2:
        engine.rng = (
            ScriptedRandomSource().five_star(1).pick(1, u_for_item(engine.up_5, other_up.name))
        )
        second = engine.pull_once()

        assert second.item.id == other_up.id
        assert second.is_up is True
        assert engine.pity.guaranteed_5 is False, "大保底已被消费"
        assert state.fate_points == 2, "另一把 UP 武器同样累计命定值"

    # ---- 命定值已满 → 这一金必为定轨武器 ----
    # 故意把分支判定指向"歪"侧：定轨强制分支排在 UP 判定之前，应当忽略它。
    engine.rng = ScriptedRandomSource().five_star(1).lose_5050(1).pick(1, 0.99)
    forced = engine.pull_once()

    assert forced.source == SOURCE_EPITOMIZED
    assert forced.item.id == charted.id
    assert state.fate_points == 0, "拿到定轨武器后命定值必须归零"


def test_epitomized_forced_clears_stale_guarantee():
    """★ 铁律二：定轨强制分支必须清掉大保底。

    复现路径：先在 75/25 上歪了（大保底=真，命定值=1），随后命定值满值触发
    定轨强制。若不清掉大保底，**再下一个 5★ 又会白送一把 UP 武器**。
    """
    rules = PoolRules.weapon(epitomized_max_points=1)
    items = weapon_items()
    charted = items[0]
    state = EpitomizedState(charted_item_id=charted.id, fate_points=1)
    pity = PityState(guaranteed_5=True)  # 上一把歪了留下的大保底

    rng = ScriptedRandomSource().five_star(1).pick(1, 0.0)
    engine = make_engine(
        pool_type=PoolType.WEAPON,
        rules=rules,
        items=items,
        pity=pity,
        epitomized=state,
        rng=rng,
    )

    forced = engine.pull_once()
    assert forced.source == SOURCE_EPITOMIZED
    assert (
        engine.pity.guaranteed_5 is False
    ), "定轨强制出金后大保底必须被清掉，否则下一个 5★ 会再次白送 UP（双重白嫖）"

    # 第二个金：分支判定仍然落在"歪"侧。若大保底没被清掉，这里会错误地出 UP。
    engine.rng = ScriptedRandomSource().five_star(1).lose_5050(1).pick(1, 0.99)
    second = engine.pull_once()
    assert second.source != SOURCE_GUARANTEE
    assert second.is_up is False, "大保底已被定轨消费，这一金不该再白送"


def test_epitomized_points_accrue_on_the_other_up_weapon():
    """官方规则：拿到**非定轨的那把五星武器**就 +1 命定值，不只是歪了才加。

    武器池只歪 25% 太慢，实际多数命定值来自"抽到另一把 UP 武器"。
    """
    rules = PoolRules.weapon(epitomized_max_points=2)
    items = weapon_items()
    charted, other_up = items[0], items[1]  # 护摩之杖 / 若水
    state = EpitomizedState(charted_item_id=charted.id, fate_points=0)
    engine = make_engine(pool_type=PoolType.WEAPON, rules=rules, items=items, epitomized=state)

    pick_other = u_for_item(engine.up_5, other_up.name)
    engine.rng = ScriptedRandomSource().five_star(1).win_5050(1).pick(1, pick_other)

    outcome = engine.pull_once()

    assert outcome.item.id == other_up.id
    assert outcome.is_up is True
    assert state.fate_points == 1, "抽到另一把 UP 武器同样应累计命定值"


@pytest.mark.parametrize("max_points", [1, 2])
def test_banner_end_lazily_resets_fate_points(max_points):
    """卡池结束后命定值归零，且不依赖定时任务。"""
    closed = utcnow() - timedelta(seconds=1)
    state = EpitomizedState(charted_item_id=30, fate_points=max_points)
    pool = type("P", (), {"close_at": closed})()

    changed = apply_banner_end_reset(state, pool)

    assert changed is True
    assert state.fate_points == 0
    assert state.charted_item_id is None


def test_banner_end_reset_is_noop_when_still_open():
    state = EpitomizedState(charted_item_id=30, fate_points=1)
    pool = type("P", (), {"close_at": utcnow() + timedelta(days=1)})()

    assert apply_banner_end_reset(state, pool) is False
    assert state.fate_points == 1


def test_banner_end_reset_handles_naive_datetime_from_sqlite():
    """SQLite 读回的时间没有时区，若直接与 aware 比较会抛 TypeError。"""
    naive_closed = (utcnow() - timedelta(seconds=1)).replace(tzinfo=None)
    state = EpitomizedState(charted_item_id=30, fate_points=1)
    pool = type("P", (), {"close_at": naive_closed})()

    assert apply_banner_end_reset(state, pool) is True
    assert state.fate_points == 0


# ===========================================================================
# RNG 消耗契约
# ===========================================================================
def test_every_pull_consumes_exactly_four_random_numbers():
    """即使结果已由保底确定，也必须照常取数，否则抽数对应关系会错位。"""
    pity = PityState(pity_5=89)  # 下一抽是硬保底，结果已确定
    rng = ScriptedRandomSource()
    engine = make_engine(pity=pity, rng=rng)

    engine.pull_once()

    assert rng.calls == 4


def test_ten_pulls_consume_forty_random_numbers():
    rng = ScriptedRandomSource()
    engine = make_engine(rng=rng)

    for _ in range(10):
        engine.pull_once()

    assert rng.calls == 40


def test_neutral_default_never_hits_natural_five_star():
    """兜底值必须高于任何真实概率，否则未指定指令的抽数会意外出金。"""
    assert NEUTRAL > 0.99
    engine = make_engine(pool_type=PoolType.WEAPON)
    assert all(engine.pull_once().star != 5 for _ in range(76))
