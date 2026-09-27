"""卡池规则：值对象 + 校验。

``PoolRules`` 是从 ORM 的 ``Pool`` 抽出来的不可变值对象，让抽卡引擎与数据库
解耦 —— 单元测试可以直接 ``PoolRules.character()`` 构造规则，不必建库建表。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from app.core.exceptions import PoolConfigError
from app.models.enums import ItemType, PoolType


@dataclass(frozen=True, slots=True)
class PoolRules:
    base_rate_5: float = 0.006
    base_rate_4: float = 0.051
    hard_pity_5: int = 90
    hard_pity_4: int = 10
    soft_pity_enabled: bool = True
    soft_pity_start: int = 74
    soft_pity_step: float = 0.06
    up_rate_5: float = 0.5
    up_rate_4: float = 0.5
    has_5050: bool = True
    epitomized_enabled: bool = False
    epitomized_max_points: int = 1
    capturing_radiance_enabled: bool = False

    # ---------------- 构造 ----------------
    @classmethod
    def from_pool(cls, pool: Any) -> PoolRules:
        names = {f.name for f in fields(cls)}
        return cls(**{name: getattr(pool, name) for name in names})

    @classmethod
    def _build(cls, defaults: dict[str, Any], overrides: dict[str, Any]) -> PoolRules:
        """默认值字典与覆盖项合并后再构造。

        不能直接 ``cls(field=value, **overrides)`` —— 那样只要调用方覆盖了
        任何一个已显式写出的字段，就会抛 "got multiple values for keyword
        argument"。合并字典才能真正支持"任意字段可覆盖"。
        """
        unknown = set(overrides) - {f.name for f in fields(cls)}
        if unknown:
            raise TypeError(f"未知的规则字段: {', '.join(sorted(unknown))}")
        return cls(**{**defaults, **overrides})

    @classmethod
    def character(cls, **overrides: Any) -> PoolRules:
        """角色活动祈愿：0.6% / 90 保底 / 74 起软保底 / 50-50 大保底。"""
        return cls._build(
            {
                "base_rate_5": 0.006,
                "base_rate_4": 0.051,
                "hard_pity_5": 90,
                "hard_pity_4": 10,
                "soft_pity_enabled": True,
                "soft_pity_start": 74,
                "soft_pity_step": 0.06,
                "up_rate_5": 0.5,
                "up_rate_4": 0.5,
                "has_5050": True,
            },
            overrides,
        )

    @classmethod
    def weapon(cls, **overrides: Any) -> PoolRules:
        """武器活动祈愿：0.7% / 80 保底 / 63 起软保底 / 75% UP / 定轨。"""
        return cls._build(
            {
                "base_rate_5": 0.007,
                # 武器池 4★ 基础概率是 6.0%，不是角色池的 5.1%
                "base_rate_4": 0.060,
                "hard_pity_5": 80,
                "hard_pity_4": 10,
                "soft_pity_enabled": True,
                "soft_pity_start": 63,
                "soft_pity_step": 0.07,
                "up_rate_5": 0.75,  # 75% 出两把 UP 之一，各 37.5%
                "up_rate_4": 0.75,
                "has_5050": True,
                "epitomized_enabled": True,
                "epitomized_max_points": 1,  # 5.0 起官方由 2 降为 1
            },
            overrides,
        )

    @classmethod
    def standard(cls, **overrides: Any) -> PoolRules:
        """常驻祈愿：无 UP，无 50-50。"""
        return cls._build(
            {
                "base_rate_5": 0.006,
                "base_rate_4": 0.051,
                "hard_pity_5": 90,
                "hard_pity_4": 10,
                "soft_pity_enabled": True,
                "soft_pity_start": 74,
                "soft_pity_step": 0.06,
                "has_5050": False,
            },
            overrides,
        )

    @classmethod
    def beginner(cls, **overrides: Any) -> PoolRules:
        """新手祈愿：角色池同款概率，但仅 20 抽且只有角色。"""
        return cls._build(
            {
                "base_rate_5": 0.006,
                "base_rate_4": 0.051,
                "hard_pity_5": 90,  # 20 抽上限下永远够不到，与线上一致
                "hard_pity_4": 10,
                "soft_pity_enabled": True,
                "soft_pity_start": 74,
                "soft_pity_step": 0.06,
                "has_5050": False,
            },
            overrides,
        )

    # ---------------- 校验 ----------------
    def validate(self) -> None:
        """规则自洽性校验。不自洽的配置一旦入库会让引擎进入未定义行为，
        因此必须在管理员保存时就拒绝，而不是等玩家抽卡时才炸。"""
        if self.hard_pity_5 < 1:
            raise PoolConfigError("5★ 硬保底抽数必须 >= 1")
        if self.hard_pity_4 < 1:
            raise PoolConfigError("4★ 硬保底抽数必须 >= 1")
        if not 0 < self.base_rate_5 <= 1:
            raise PoolConfigError("5★ 基础概率必须在 (0, 1] 之间")
        if not 0 <= self.base_rate_4 <= 1:
            raise PoolConfigError("4★ 基础概率必须在 [0, 1] 之间")
        if not 0 <= self.up_rate_5 <= 1:
            raise PoolConfigError("5★ UP 概率必须在 [0, 1] 之间")
        if not 0 <= self.up_rate_4 <= 1:
            raise PoolConfigError("4★ UP 概率必须在 [0, 1] 之间")

        if self.soft_pity_enabled:
            if self.soft_pity_start < 1:
                raise PoolConfigError("软保底起始抽数必须 >= 1")
            if self.soft_pity_start > self.hard_pity_5:
                # 这种配置下软保底永远不会生效（硬保底先到），是个静默失效的坑
                raise PoolConfigError(
                    f"软保底起始抽数（{self.soft_pity_start}）不能晚于 "
                    f"5★ 硬保底（{self.hard_pity_5}），否则软保底永不生效"
                )
            if self.soft_pity_step < 0:
                raise PoolConfigError("软保底概率增量不能为负")

        if self.epitomized_enabled and self.epitomized_max_points < 1:
            raise PoolConfigError("定轨命定值上限必须 >= 1")

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def validate_pool(pool: Any) -> None:
    """规则 + 物品的完整性校验，供管理员保存卡池时调用。

    除了 :meth:`PoolRules.validate` 的规则自洽性，还要确认抽卡时需要的每个
    物品集合都非空、类型正确、权重可计算 —— 否则引擎会在运行时才抛错。
    """
    PoolRules.from_pool(pool).validate()

    if pool.open_at and pool.close_at and pool.close_at <= pool.open_at:
        raise PoolConfigError("结束时间必须晚于开始时间")

    if pool.lifetime_pull_cap is not None and pool.lifetime_pull_cap < 1:
        raise PoolConfigError("抽卡次数上限必须 >= 1")

    if not pool.allows_single_pull and not pool.allows_ten_pull:
        raise PoolConfigError("单抽与十连不能同时禁用")

    # 十连折扣价必须低于按单抽累加的价格，否则是标价错误
    if (
        pool.cost_per_ten is not None
        and pool.cost_per_ten >= pool.cost_per_pull * 10
        and pool.allows_single_pull
    ):
        raise PoolConfigError(
            f"十连价格（{pool.cost_per_ten}）不应高于单抽累加价"
            f"（{pool.cost_per_pull} × 10 = {pool.cost_per_pull * 10}）"
        )

    items = [i for i in pool.items if i.is_active]
    if not items:
        raise PoolConfigError("卡池至少要有一个启用中的物品")

    _check_group(pool, items, star=5, label="五星")
    _check_group(pool, items, star=4, label="四星")

    rules = PoolRules.from_pool(pool)

    # ---- 保底目标必须存在 ----
    if not [i for i in items if i.star == 3]:
        raise PoolConfigError("卡池缺少三星物品：未中 4★/5★ 时无处可掉")

    # ---- UP 规则 ----
    if rules.has_5050:
        up5 = [i for i in items if i.star == 5 and i.is_up]
        if not up5:
            raise PoolConfigError("启用了 UP 规则，但卡池里没有任何 UP 五星")

        off5 = [i for i in items if i.star == 5 and not i.is_up]
        if not off5:
            raise PoolConfigError(
                "启用了 UP 规则，但没有非 UP 五星作为歪池：" "歪了之后无处可掉，引擎会在运行时抛错"
            )

        # 歪池必须与 UP 池同类型，否则角色池会掉武器、武器池会掉角色
        want_type = ItemType.WEAPON if pool.pool_type == PoolType.WEAPON else ItemType.CHARACTER
        mismatched = [i for i in off5 if i.item_type != want_type]
        if mismatched:
            names = "、".join(i.name for i in mismatched[:3])
            raise PoolConfigError(
                f"非 UP 五星的物品类型必须与卡池类型一致（应为{want_type}），"
                f"但以下物品类型不符：{names}"
            )

        if rules.epitomized_enabled and len(up5) != 2:
            raise PoolConfigError(
                f"启用定轨的卡池必须恰好有 2 把 UP 五星武器，当前有 {len(up5)} 把"
            )

    # ---- 权重 ----
    if any(i.weight < 0 for i in items):
        raise PoolConfigError("物品权重不能为负")


def _check_group(pool: Any, items: list[Any], *, star: int, label: str) -> None:
    group = [i for i in items if i.star == star]
    if not group:
        raise PoolConfigError(f"卡池缺少{label}物品")
    if sum(i.weight for i in group) <= 0:
        raise PoolConfigError(f"{label}物品的权重之和必须大于 0，否则无法抽取")
