"""抽卡结算引擎（纯逻辑，不碰数据库）。

把引擎与持久化分开，是为了让"保底 / 大保底 / 定轨 / 4★ 计数器"这些最容易
写错的状态机能够脱离数据库被单独测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import PoolConfigError
from app.db.base import ensure_utc, utcnow
from app.models.enums import ItemType, PoolType
from app.services.pool_rules import PoolRules
from app.services.rates import rate_4, rate_5
from app.services.rng import RandomSource

# ---- 出金来源标记 ----
SOURCE_BASE = "base"
SOURCE_SOFT_PITY = "soft_pity"
SOURCE_HARD_PITY = "hard_pity"
SOURCE_GUARANTEE = "guarantee"
SOURCE_EPITOMIZED = "epitomized"
SOURCE_RADIANCE = "radiance"

#: 捕获明光：连续歪多少次之后，下一次 50-50 强制命中 UP
RADIANCE_THRESHOLD = 3


class PityState:
    """可变保底状态。为了让引擎脱离 ORM 可测，这里只暴露用到的字段。"""

    __slots__ = (
        "pity_5",
        "pity_4",
        "guaranteed_5",
        "guaranteed_up_4",
        "radiance_streak",
    )

    def __init__(
        self,
        pity_5: int = 0,
        pity_4: int = 0,
        guaranteed_5: bool = False,
        guaranteed_up_4: bool = False,
        radiance_streak: int = 0,
    ) -> None:
        self.pity_5 = pity_5
        self.pity_4 = pity_4
        self.guaranteed_5 = guaranteed_5
        self.guaranteed_up_4 = guaranteed_up_4
        self.radiance_streak = radiance_streak


class EpitomizedState:
    __slots__ = ("charted_item_id", "fate_points")

    def __init__(self, charted_item_id: int | None = None, fate_points: int = 0) -> None:
        self.charted_item_id = charted_item_id
        self.fate_points = fate_points


@dataclass(slots=True)
class PullOutcome:
    """单次抽卡的结果。"""

    index: int  # 本批次内的序号，从 1 开始
    item: Any
    star: int
    is_up: bool
    is_pity: bool
    source: str
    pity_5_before: int
    pity_4_before: int
    rate_5_used: float
    fate_points_after: int = 0


@dataclass(slots=True)
class EngineState:
    """一次抽卡批次结束后的状态快照，便于断言与持久化。"""

    pity_5: int = 0
    pity_4: int = 0
    guaranteed_5: bool = False
    guaranteed_up_4: bool = False
    radiance_streak: int = 0
    fate_points: int = 0
    charted_item_id: int | None = None
    outcomes: list[PullOutcome] = field(default_factory=list)


def weighted_pick(items: list[Any], u: float) -> Any:
    """按权重做累积扫描抽取。

    **刻意不用 ``random.choices``**：它消耗底层 Mersenne Twister 流的方式
    在不同 CPython 版本间不保证稳定，会让"固定种子 → 固定结果"的重放失效，
    也会让脚本化随机源的抽数对应关系错乱。
    """
    if not items:
        raise PoolConfigError("抽卡目标集合为空")
    total = sum(float(i.weight) for i in items)
    if total <= 0:
        raise PoolConfigError("抽卡目标集合的权重之和必须大于 0")
    threshold = u * total
    acc = 0.0
    for item in items:
        acc += float(item.weight)
        if threshold < acc:
            return item
    return items[-1]  # 浮点误差兜底


def apply_banner_end_reset(epitomized: EpitomizedState | None, pool: Any, now=None) -> bool:
    """卡池已结束则清零命定值。

    **惰性判定而非定时任务**：定时任务一旦失败，陈旧的命定值会让玩家白拿
    一把定轨武器。放在每次读取和每次抽卡前判定，就永远不会漏。
    返回是否真的发生了重置。
    """
    if epitomized is None:
        return False
    close_at = ensure_utc(getattr(pool, "close_at", None))
    if close_at is None:
        return False
    now = now or utcnow()
    if now < close_at:
        return False
    if epitomized.charted_item_id is None and epitomized.fate_points == 0:
        return False
    epitomized.charted_item_id = None
    epitomized.fate_points = 0
    return True


class GachaEngine:
    """把一个卡池的规则、物品集合与用户状态绑在一起，逐抽结算。"""

    def __init__(
        self,
        *,
        pool: Any,
        rules: PoolRules,
        pity: PityState,
        epitomized: EpitomizedState | None,
        rng: RandomSource,
        now=None,
    ) -> None:
        self.pool = pool
        self.rules = rules
        self.pity = pity
        self.epitomized = epitomized
        self.rng = rng
        self.now = now or utcnow()
        self._index = 0
        self._prepare_items()

    # ------------------------------------------------------------------
    # 物品集合预计算。抽卡过程中不再查询数据库，也不重新过滤。
    # ------------------------------------------------------------------
    def _prepare_items(self) -> None:
        pool = self.pool
        items = [i for i in pool.items if i.is_active]

        self.items_5_all = [i for i in items if i.star == 5]
        self.items_4_all = [i for i in items if i.star == 4]
        self.items_3_all = [i for i in items if i.star == 3]
        self.items_by_id = {i.id: i for i in items}

        if not self.items_3_all:
            raise PoolConfigError("卡池缺少三星物品，未中 4★/5★ 时无处可掉")

        self.up_5 = [i for i in self.items_5_all if i.is_up]
        self.up_4 = [i for i in self.items_4_all if i.is_up]

        # 歪池必须与卡池类别一致：角色池歪了不能掉武器，武器池歪了不能掉角色。
        # 4★ 歪池不做类型限制 —— 线上角色池的 4★ 歪池本来就同时包含角色与武器。
        off_type = None
        if self.rules.has_5050:
            off_type = ItemType.WEAPON if pool.pool_type == PoolType.WEAPON else ItemType.CHARACTER
        self.off_5 = (
            [i for i in self.items_5_all if not i.is_up and i.item_type == off_type]
            if off_type
            else [i for i in self.items_5_all if not i.is_up]
        )
        self.off_4 = [i for i in self.items_4_all if not i.is_up]

        if self.rules.has_5050:
            if not self.up_5:
                raise PoolConfigError("启用了 UP 规则但卡池没有 UP 五星")
            if not self.off_5:
                raise PoolConfigError(
                    "启用了 UP 规则但没有非 UP 五星作为歪池，" "歪了之后引擎无处可掉"
                )

    # ------------------------------------------------------------------
    @property
    def charted_item(self) -> Any | None:
        e = self.epitomized
        if e is None or e.charted_item_id is None:
            return None
        return self.items_by_id.get(e.charted_item_id)

    def _epitomized_forced(self) -> bool:
        """定轨是否已满值，下一个 5★ 是否必为定轨武器。"""
        e = self.epitomized
        if e is None or not self.rules.epitomized_enabled:
            return False
        if e.charted_item_id is None:
            return False
        return e.fate_points >= self.rules.epitomized_max_points

    # ------------------------------------------------------------------
    def pull_once(self) -> PullOutcome:
        r = self.rules
        p = self.pity

        n5 = p.pity_5 + 1
        n4 = p.pity_4 + 1

        # 固定消耗 4 个随机数，顺序与 slot 常量一致。即便某步结果已由保底确定，
        # 也照样取数 —— 这是脚本化随机源与重放能成立的前提。
        u5 = self.rng.random()
        u4 = self.rng.random()
        u_branch = self.rng.random()
        u_item = self.rng.random()

        rate5 = rate_5(r, n5)
        hard5 = n5 >= r.hard_pity_5
        is5 = hard5 or u5 < rate5

        pity_5_before = p.pity_5
        pity_4_before = p.pity_4

        if is5:
            item, is_up, source = self._resolve_five_star(u_branch, u_item, n5, hard5)
            p.pity_5 = 0
            # ★ 铁律一：出 5★ 不重置 4★ 计数器。
            #   pity_4 的语义是"距上一个 4★ 的抽数"，不是"距上一个 4★ 或以上"。
            #   本次 5★ 只是"满足"了本抽的 4★-或以上保底，该保底顺延到下一抽。
            p.pity_4 = n4
            is_pity = source in (
                SOURCE_HARD_PITY,
                SOURCE_GUARANTEE,
                SOURCE_EPITOMIZED,
                SOURCE_RADIANCE,
            )
        else:
            force4 = n4 >= r.hard_pity_4 or p.guaranteed_up_4
            is4 = force4 or u4 < rate_4(r, n4)
            if is4:
                item, is_up = self._resolve_four_star(u_branch, u_item)
                p.pity_4 = 0
                source = SOURCE_HARD_PITY if n4 >= r.hard_pity_4 else SOURCE_BASE
            else:
                item = weighted_pick(self.items_3_all, u_item)
                is_up = False
                p.pity_4 = n4
                source = SOURCE_BASE
            p.pity_5 = n5
            is_pity = is4 and force4

        self._index += 1
        return PullOutcome(
            index=self._index,
            item=item,
            star=item.star,
            is_up=is_up,
            is_pity=is_pity,
            source=source,
            pity_5_before=pity_5_before,
            pity_4_before=pity_4_before,
            rate_5_used=rate5,
            fate_points_after=self.epitomized.fate_points if self.epitomized else 0,
        )

    # ------------------------------------------------------------------
    def _resolve_five_star(
        self, u_branch: float, u_item: float, n5: int, hard5: bool
    ) -> tuple[Any, bool, str]:
        r = self.rules
        p = self.pity
        natural_source = self._natural_source(n5, hard5)

        # ---- 1. 定轨强制（必须排在其它分支之前）----
        if self._epitomized_forced():
            item = self.charted_item
            if item is None:  # pragma: no cover - 定轨物品被删除的兜底
                raise PoolConfigError("定轨武器已不存在，请在卡池界面重新定轨")
            # ★ 铁律二：必须清掉大保底。
            #   否则"先歪（大保底=真，命定值=1）→ 定轨强制出金"这条路径会留下
            #   陈旧的大保底，导致再下一个 5★ 又白送 UP —— 双重白嫖。
            p.guaranteed_5 = False
            self._bookkeep_epitomized(item)
            return item, True, SOURCE_EPITOMIZED

        # ---- 2. 无 UP 规则的卡池（常驻 / 新手）----
        if not r.has_5050:
            item = weighted_pick(self.items_5_all, u_item)
            p.guaranteed_5 = False
            return item, bool(item.is_up), natural_source

        # ---- 3. 大保底：上一次歪了，这次必出 UP ----
        if p.guaranteed_5:
            item = weighted_pick(self.up_5, u_item)
            p.guaranteed_5 = False
            p.radiance_streak = 0
            self._bookkeep_epitomized(item)
            return item, True, SOURCE_GUARANTEE

        # ---- 4. 新鲜的 UP 判定（捕获明光可能把它变成必胜）----
        radiance = r.capturing_radiance_enabled and p.radiance_streak >= RADIANCE_THRESHOLD
        win = radiance or u_branch < r.up_rate_5
        if win:
            item = weighted_pick(self.up_5, u_item)
            p.guaranteed_5 = False
            # 自然命中 UP → 说明"第一个 5★ 就中了"，连击清零
            p.radiance_streak = 0
            self._bookkeep_epitomized(item)
            return item, True, SOURCE_RADIANCE if radiance else natural_source

        # ---- 5. 歪了 ----
        item = weighted_pick(self.off_5, u_item)
        p.guaranteed_5 = True
        # 又歪一次 → 下一次的 UP 要靠大保底（即"第二个 5★ 才中"），连击 +1
        p.radiance_streak += 1
        self._bookkeep_epitomized(item)
        return item, False, natural_source

    def _resolve_four_star(self, u_branch: float, u_item: float) -> tuple[Any, bool]:
        r = self.rules
        p = self.pity

        # 无 UP 规则，或该卡池没有配 UP/歪池 4★ → 全部一起按权重抽
        if not r.has_5050 or not self.up_4 or not self.off_4:
            item = weighted_pick(self.items_4_all, u_item)
            return item, bool(item.is_up)

        if p.guaranteed_up_4 or u_branch < r.up_rate_4:
            item = weighted_pick(self.up_4, u_item)
            p.guaranteed_up_4 = False
            return item, True

        item = weighted_pick(self.off_4, u_item)
        p.guaranteed_up_4 = True
        return item, False

    # ------------------------------------------------------------------
    def _bookkeep_epitomized(self, item: Any) -> None:
        """定轨记账。

        官方规则：**只要拿到了非定轨的那把 5★ 武器，命定值就 +1** ——
        不只是"歪了"才加。因此武器池抽到另一把 UP 武器同样累计命定值。
        拿到定轨武器本身则归零。
        """
        e = self.epitomized
        if e is None or not self.rules.epitomized_enabled:
            return
        if e.charted_item_id is None:
            return
        if item.id == e.charted_item_id:
            e.fate_points = 0
        else:
            e.fate_points = min(e.fate_points + 1, self.rules.epitomized_max_points)

    def _natural_source(self, n5: int, hard5: bool) -> str:
        """区分自然命中是发生在基础段还是软保底爬升段。

        ``n5`` 由调用方显式传入，不在此处用 ``pity.pity_5 + 1`` 反推 ——
        后者依赖"必须在 pity_5 归零之前调用"这一隐含时序，太脆弱。
        """
        if hard5:
            return SOURCE_HARD_PITY
        r = self.rules
        if r.soft_pity_enabled and n5 >= r.soft_pity_start:
            return SOURCE_SOFT_PITY
        return SOURCE_BASE

    # ------------------------------------------------------------------
    def snapshot(self) -> EngineState:
        p, e = self.pity, self.epitomized
        return EngineState(
            pity_5=p.pity_5,
            pity_4=p.pity_4,
            guaranteed_5=p.guaranteed_5,
            guaranteed_up_4=p.guaranteed_up_4,
            radiance_streak=p.radiance_streak,
            fate_points=e.fate_points if e else 0,
            charted_item_id=e.charted_item_id if e else None,
        )


__all__ = [
    "RADIANCE_THRESHOLD",
    "SOURCE_BASE",
    "SOURCE_EPITOMIZED",
    "SOURCE_GUARANTEE",
    "SOURCE_HARD_PITY",
    "SOURCE_RADIANCE",
    "SOURCE_SOFT_PITY",
    "EngineState",
    "EpitomizedState",
    "GachaEngine",
    "PityState",
    "PullOutcome",
    "apply_banner_end_reset",
    "weighted_pick",
]
