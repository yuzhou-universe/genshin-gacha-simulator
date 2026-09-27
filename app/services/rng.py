"""随机源抽象。

抽卡引擎只通过 ``RandomSource.random()`` 取随机数，绝不直接使用全局
``random`` 模块。这样带来两个关键能力：

1. **可测**：测试用 ``ScriptedRandomSource`` 精确指定"第几抽出金、是否歪"，
   完全不需要 monkeypatch 引擎内部实现。
2. **可复现**：线上用带种子的 ``SystemRandomSource``，出问题时能按种子重放。

**契约：每抽固定消耗 4 个随机数**（5★判定 / 4★判定 / 分支判定 / 物品选取），
即使某一步的结果已经由保底确定，也照样消耗。这是脚本化随机源能够按"抽数"
下达指令的前提；一旦某处提前 return 跳过取数，所有测试的抽数对应关系都会错位。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

#: 每个单抽消耗的随机数个数
SLOTS_PER_PULL = 4

SLOT_RATE_5 = 0
SLOT_RATE_4 = 1
SLOT_BRANCH = 2
SLOT_ITEM = 3

#: 一个"永远不中"的兜底值。0.999999 大于任何真实概率（软保底最高 0.987），
#: 因此未指定指令的抽数会稳定地不出 5★ —— 但硬保底与保底强制分支不受影响，
#: 因为它们由显式布尔判断决定，不依赖随机数。
NEUTRAL = 0.999999


@runtime_checkable
class RandomSource(Protocol):
    def random(self) -> float:  # pragma: no cover - 协议定义
        ...


class SystemRandomSource:
    """生产环境随机源。可选种子，便于复现问题。"""

    __slots__ = ("_rng", "seed_label")

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self.seed_label = str(seed) if seed is not None else None

    def random(self) -> float:
        return self._rng.random()


class ScriptedRandomSource:
    """测试用随机源：按"第几抽 + 哪个槽位"下达指令。

    用法::

        rng = (
            ScriptedRandomSource()
            .five_star(90)            # 第 90 抽必出金
            .lose_5050(90)            # 且这一金是歪的
            .pick(90, 0.3)            # 且抽中累积权重 0.3 处的物品
        )
        result = await service.pull(user, pool, count=90, rng=rng)

    未下指令的抽数一律返回 ``NEUTRAL``，即"自然概率不中、非保底"。
    """

    __slots__ = ("_default", "_plan", "calls")

    def __init__(self, default: float = NEUTRAL) -> None:
        self._default = default
        self._plan: dict[tuple[int, int], float] = {}
        self.calls = 0

    # ---------------- 指令 ----------------
    def at(self, pull_no: int, slot: int, value: float) -> ScriptedRandomSource:
        if pull_no < 1:
            raise ValueError("pull_no 从 1 开始")
        if not 0 <= slot < SLOTS_PER_PULL:
            raise ValueError(f"slot 必须在 0..{SLOTS_PER_PULL - 1} 之间")
        self._plan[(pull_no, slot)] = value
        return self

    def five_star(self, pull_no: int) -> ScriptedRandomSource:
        """让第 pull_no 抽命中 5★ 概率判定。"""
        return self.at(pull_no, SLOT_RATE_5, 0.0)

    def no_five_star(self, pull_no: int) -> ScriptedRandomSource:
        return self.at(pull_no, SLOT_RATE_5, self._default)

    def four_star(self, pull_no: int) -> ScriptedRandomSource:
        """让第 pull_no 抽命中 4★ 概率判定（仅在该抽未出 5★ 时有效）。"""
        return self.at(pull_no, SLOT_RATE_4, 0.0)

    def win_5050(self, pull_no: int) -> ScriptedRandomSource:
        """分支判定命中 UP 一侧（u < up_rate）。"""
        return self.at(pull_no, SLOT_BRANCH, 0.0)

    def lose_5050(self, pull_no: int) -> ScriptedRandomSource:
        """分支判定落在非 UP 一侧。0.999 高于角色池 0.5 与武器池 0.75 两个阈值。"""
        return self.at(pull_no, SLOT_BRANCH, 0.999)

    def pick(self, pull_no: int, value: float) -> ScriptedRandomSource:
        """指定物品选取用的随机数（对权重做累积扫描）。"""
        return self.at(pull_no, SLOT_ITEM, value)

    # ---------------- 实现 ----------------
    def random(self) -> float:
        pull_no = self.calls // SLOTS_PER_PULL + 1
        slot = self.calls % SLOTS_PER_PULL
        self.calls += 1
        return self._plan.get((pull_no, slot), self._default)

    @property
    def pulls_consumed(self) -> int:
        return self.calls // SLOTS_PER_PULL


def u_for_item(items: Sequence[object], name: str) -> float:
    """算出恰好落在指定物品权重区间中点的随机数。

    配合 ``ScriptedRandomSource.pick()`` 使用，让测试能指定"这一抽具体掉哪件"。
    """
    total = sum(float(getattr(i, "weight", 1.0)) for i in items)
    if total <= 0:
        raise ValueError("权重之和必须大于 0")
    acc = 0.0
    for item in items:
        weight = float(getattr(item, "weight", 1.0))
        if getattr(item, "name", None) == name:
            return (acc + weight * 0.5) / total
        acc += weight
    raise KeyError(f"卡池中找不到物品: {name}")
