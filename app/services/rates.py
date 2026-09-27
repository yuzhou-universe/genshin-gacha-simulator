"""概率曲线（纯函数，不依赖数据库与随机源）。

抽数编号 ``n`` 一律是 **1-based 的"本抽是本周期的第几抽"**，
即 ``n = pity_5 + 1``。这一点必须先约定清楚，否则会出现经典的差一错误：
"第 90 抽必出"意味着出金前 ``pity_5 == 89``。
"""

from __future__ import annotations

from typing import Protocol


class _HasRules(Protocol):
    """只要求提供这几个字段，便于传入 PoolRules 或 ORM 的 Pool。"""

    base_rate_5: float
    base_rate_4: float
    hard_pity_5: int
    hard_pity_4: int
    soft_pity_enabled: bool
    soft_pity_start: int
    soft_pity_step: float


def rate_5(rules: _HasRules, n: int) -> float:
    """第 n 抽的 5★ 概率。

    三段式：
    - ``n >= hard_pity_5`` → 1.0（硬保底）
    - ``n < soft_pity_start`` 或未启用软保底 → 基础概率
    - 否则按 ``base + (n - start + 1) * step`` 线性爬升

    以角色池为例（base 0.006 / start 74 / step 0.06）：
    第 73 抽 0.6%，第 74 抽 6.6%，第 75 抽 12.6%，第 89 抽 96.6%，第 90 抽 100%。

    **硬保底必须显式短路返回 1.0**，不能只靠公式：公式在 n=90 时算出 1.026，
    若改成浮点累加（如 ``base + 17*0.06``）则可能得到 0.9999999999999999，
    导致"第 90 抽必出"偶发失败。
    """
    if n >= rules.hard_pity_5:
        return 1.0
    if not rules.soft_pity_enabled or n < rules.soft_pity_start:
        return rules.base_rate_5
    ramped = rules.base_rate_5 + (n - rules.soft_pity_start + 1) * rules.soft_pity_step
    # 软保底爬升可能先于硬保底到达 100%（武器池默认第 77 抽即满），此处封顶
    return min(1.0, ramped)


def rate_4(rules: _HasRules, n: int) -> float:
    """第 n 抽的 4★ 概率。4★ 只有硬保底，没有软保底爬升。"""
    if n >= rules.hard_pity_4:
        return 1.0
    return rules.base_rate_4


def soft_pity_cap_pull(rules: _HasRules) -> int | None:
    """软保底爬升到达 100% 的抽数；未启用或永远到不了则返回 None。

    武器池默认参数下这个值是 77，早于硬保底 80 —— 意味着管理员把硬保底从 80
    往 77 以上调是没有实际效果的。后台界面据此提示管理员，避免"调了没反应"。
    """
    if not rules.soft_pity_enabled:
        return None
    if rules.soft_pity_step <= 0:
        return None
    remaining = 1.0 - rules.base_rate_5
    if remaining <= 0:
        return rules.soft_pity_start
    steps_needed = int(-(-remaining // rules.soft_pity_step))  # 向上取整
    cap = rules.soft_pity_start + steps_needed - 1
    return cap if cap < rules.hard_pity_5 else None


def consolidate_rate(rules: _HasRules, samples: int = 200_000, seed: int = 20240927) -> float:
    """模拟计算含保底的综合出金率，用于后台展示与测试对照。

    固定种子 → 结果是常量，不会让测试偶发失败。
    """
    import random

    rng = random.Random(seed)
    total = 0
    hits = 0
    pity = 0
    while total < samples:
        pity += 1
        total += 1
        if rng.random() < rate_5(rules, pity):
            hits += 1
            pity = 0
    return hits / total
