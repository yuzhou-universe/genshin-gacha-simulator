"""概率曲线测试。

分成两类：
- **解析式断言**：直接比对函数返回值，精确、无抖动。
- **固定种子的大样本校验**：种子固定 → 结果就是常量，断言不可能偶发失败；
  而且断言的是"逐抽 hazard 是否等于解析值"，比"平均值约等于 0.6%"强得多。
"""

from __future__ import annotations

import math

import pytest

from app.models.enums import PoolType
from app.services.pool_rules import PoolRules
from app.services.rates import rate_4, rate_5, soft_pity_cap_pull
from app.services.rng import SystemRandomSource
from tests.conftest import make_engine


# ===========================================================================
# 解析式断言
# ===========================================================================
@pytest.mark.parametrize(
    ("pull_no", "expected"),
    [
        (1, 0.006),  # 基础段
        (73, 0.006),  # 软保底前最后一抽，仍是基础概率
        (74, 0.066),  # 软保底第一抽 6.6%
        (75, 0.126),  # 12.6%
        (89, 0.966),  # 96.6%
        (90, 1.0),  # 硬保底，必须是精确的 1.0
    ],
)
def test_character_rate_curve(pull_no, expected):
    assert rate_5(PoolRules.character(), pull_no) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize(
    ("pull_no", "expected"),
    [
        (1, 0.007),
        (62, 0.007),  # 武器池软保底前最后一抽
        (63, 0.077),  # 7.7%
        (76, 0.987),  # 98.7%
        (77, 1.0),  # 软保底爬升到达 100%
        (80, 1.0),  # 硬保底（此时已无意义）
    ],
)
def test_weapon_rate_curve(pull_no, expected):
    assert rate_5(PoolRules.weapon(), pull_no) == pytest.approx(expected, rel=1e-9)


def test_hard_pity_returns_exact_one_not_float_noise():
    """硬保底必须精确返回 1.0。

    如果改用浮点累加计算（0.006 + 17*0.06 = 1.026 → 封顶），在 n=90 处可能
    得到 0.9999999999999999，使"第 90 抽必出"偶发失败。这里显式锁死。
    """
    assert rate_5(PoolRules.character(), 90) == 1.0
    assert rate_5(PoolRules.character(), 91) == 1.0
    assert rate_5(PoolRules.weapon(), 80) == 1.0


def test_soft_pity_disabled_is_flat_until_hard_pity():
    rules = PoolRules.character(soft_pity_enabled=False)
    for n in (1, 50, 74, 89):
        assert rate_5(rules, n) == 0.006
    assert rate_5(rules, 90) == 1.0


def test_four_star_rate_has_no_soft_pity():
    rules = PoolRules.character()
    assert rate_4(rules, 1) == 0.051
    assert rate_4(rules, 9) == 0.051
    assert rate_4(rules, 10) == 1.0


def test_weapon_four_star_base_is_six_percent_not_five():
    """武器池 4★ 基础概率是 6.0%，与角色池的 5.1% 不同 —— 容易写错。"""
    assert PoolRules.weapon().base_rate_4 == 0.06
    assert PoolRules.character().base_rate_4 == 0.051


def test_soft_pity_cap_pull_reports_dead_hard_pity_knob():
    """武器池默认参数下软保底第 77 抽就满，硬保底 80 是死参数。

    后台据此提示管理员，否则会出现"改了硬保底却没反应"的困惑。
    """
    assert soft_pity_cap_pull(PoolRules.weapon()) == 77
    # 角色池的爬升恰好落在硬保底上，不算"提前到顶"
    assert soft_pity_cap_pull(PoolRules.character()) is None
    assert soft_pity_cap_pull(PoolRules.character(soft_pity_enabled=False)) is None


# ===========================================================================
# 固定种子的大样本校验
# ===========================================================================
SAMPLES = 120_000
SEED = 20240927


@pytest.fixture(scope="module")
def character_run() -> list[int]:
    """跑 12 万抽，返回每个 5★ 出现时的"本周期的第几抽"。"""
    engine = make_engine(
        pool_type=PoolType.CHARACTER,
        rng=SystemRandomSource(seed=SEED),
    )
    hits: list[int] = []
    for _ in range(SAMPLES):
        outcome = engine.pull_once()
        if outcome.star == 5:
            hits.append(outcome.pity_5_before + 1)
    return hits


def _hazard(character_run: list[int], n: int, max_pull: int) -> tuple[int, int]:
    """返回 (第 n 抽出金的周期数, 到达第 n 抽的周期数)。

    **分母必须是"到达该抽的周期数"，不是周期总数** —— 绝大多数周期在第 74 抽
    之前就出金结束了，用总数当分母会把 hazard 系统性低估一半以上。
    """
    from collections import Counter

    buckets = Counter(character_run)
    hit = buckets[n]
    reached = sum(buckets[k] for k in range(n, max_pull + 1))
    return hit, reached


def test_base_rate_matches_analytic_within_four_sigma(character_run):
    """基础段每一抽的实测 hazard 都要贴合 0.6%。

    种子固定 → 确定性断言，不会偶发失败；但若有人把基础概率改成 0.7%，
    它会立刻报错。
    """
    p = 0.006
    checked = 0
    for n in range(1, 74):
        hit, reached = _hazard(character_run, n, 90)
        if reached < 200:
            continue
        observed = hit / reached
        sigma = math.sqrt(p * (1 - p) / reached)
        assert (
            abs(observed - p) < 4 * sigma
        ), f"第 {n} 抽实测 hazard {observed:.5f} 偏离解析值 {p} 超过 4σ（σ={sigma:.5f}）"
        checked += 1
    assert checked >= 60, "样本量不足以完成基础段校验"


def test_average_pulls_per_five_star_matches_published_rate(character_run):
    """平均出金抽数应贴近官方公布的综合出金率 1.6%（≈62.5 抽）。

    这是对整条软保底曲线最有说服力的端到端校验：软保底起点或斜率只要偏一点，
    这个均值就会明显跑偏。
    """
    average = SAMPLES / len(character_run)
    assert average == pytest.approx(
        62.5, abs=2.5
    ), f"平均 {average:.1f} 抽出一金，与官方综合出金率对应的 62.5 抽相差过大"


def test_no_five_star_ever_exceeds_hard_pity(character_run):
    """上界断言：任何金都不能超过第 90 抽。

    注意这里**不能**断言 max == 90。"第 90 抽必出"是硬保底，但要走到第 90 抽
    得连续躲过 85~89 共 5 次 70%+ 的判定，概率约 3e-5，12 万抽的样本下几乎
    不可能出现。硬保底本身由 test_gacha_engine.py 里的脚本化测试精确验证。
    """
    assert all(n <= 90 for n in character_run)
    assert min(character_run) >= 1


def test_empirical_hazard_matches_analytic_curve_in_soft_pity(character_run):
    """软保底区间逐抽 hazard 都要贴合解析曲线。

    比"平均值对得上"强得多：能抓出"软保底起点偏了一抽""斜率用错"这类错误。
    """
    rules = PoolRules.character()
    for n in (74, 75, 76, 77, 78, 79):
        hit, reached = _hazard(character_run, n, 90)
        assert reached >= 50, f"第 {n} 抽样本不足"
        observed = hit / reached
        expected = rate_5(rules, n)
        sigma = math.sqrt(expected * (1 - expected) / reached)
        assert abs(observed - expected) < 4 * sigma, (
            f"第 {n} 抽实测 hazard {observed:.4f} 与解析值 {expected:.4f} "
            f"相差超过 4σ（σ={sigma:.4f}）"
        )


def test_soft_pity_rate_is_monotonically_increasing(character_run):
    """软保底区间的 hazard 必须单调递增（斜率正、无断点）。"""
    from collections import Counter

    buckets = Counter(character_run)
    series = []
    for n in range(74, 84):
        reached = sum(buckets[k] for k in range(n, 91))
        series.append(buckets[n] / reached)
    # 允许抽样噪声，但整体趋势必须向上
    assert series[0] < series[3] < series[-1], f"软保底 hazard 未单调上升: {series}"
