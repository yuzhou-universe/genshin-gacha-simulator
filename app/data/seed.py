"""默认卡池种子数据。

数值与物品表对齐线上原神（核实于 2026-09）：

- 常驻五星角色 8 位、常驻五星武器 10 把，构成角色池与武器池的"歪池"。
- 角色池歪了只会掉**角色**，武器池歪了只会掉**武器**。
- 角色池 4★ 歪池同时包含角色与武器（与线上一致）；武器池 4★ 歪池只有武器。
"""

from __future__ import annotations

from typing import Any

from app.models.enums import CurrencyType, ItemType, PoolType

# ---------------------------------------------------------------------------
# 常驻池物品表
# ---------------------------------------------------------------------------
STANDARD_5_CHARACTERS: list[str] = [
    "琴",
    "迪卢克",
    "莫娜",
    "刻晴",
    "七七",
    "提纳里",
    "迪希雅",
    "梦见月瑞希",
]

STANDARD_5_WEAPONS: list[str] = [
    "阿莫斯之弓",
    "风鹰剑",
    "四风原典",
    "和璞鸢",
    "天空之卷",
    "天空之刃",
    "天空之翼",
    "天空之傲",
    "天空之脊",
    "狼的末路",
]

STANDARD_4_CHARACTERS: list[str] = [
    "行秋",
    "香菱",
    "北斗",
    "凝光",
    "重云",
    "砂糖",
    "诺艾尔",
    "班尼特",
    "菲谢尔",
    "雷泽",
]

STANDARD_4_WEAPONS: list[str] = [
    "祭礼剑",
    "匣里灭辰",
    "西风剑",
    "西风大剑",
    "西风长枪",
    "西风猎弓",
    "西风秘典",
    "弓藏",
    "钟剑",
    "雨裁",
]

STANDARD_3_WEAPONS: list[str] = [
    "冷刃",
    "黎明神剑",
    "飞天御剑",
    "沐浴龙血的剑",
    "以理服人",
    "铁影阔剑",
    "白缨枪",
    "黑缨枪",
    "鸦羽弓",
    "弹弓",
    "神射手之誓",
    "魔导绪论",
    "讨龙英杰谭",
    "翡玉法球",
]


def _items(
    *,
    five: list[tuple[str, str, bool]],
    four: list[tuple[str, str, bool]],
    three: list[str],
) -> list[dict[str, Any]]:
    """把 (名称, 类型, 是否UP) 三元组展开成物品字典列表。"""
    out: list[dict[str, Any]] = []
    order = 0
    for name, kind, is_up in five:
        out.append(
            {
                "name": name,
                "item_type": kind,
                "star": 5,
                "is_up": is_up,
                "weight": 1.0,
                "sort_order": order,
            }
        )
        order += 1
    for name, kind, is_up in four:
        out.append(
            {
                "name": name,
                "item_type": kind,
                "star": 4,
                "is_up": is_up,
                "weight": 1.0,
                "sort_order": order,
            }
        )
        order += 1
    for name in three:
        out.append(
            {
                "name": name,
                "item_type": ItemType.WEAPON,
                "star": 3,
                "is_up": False,
                "weight": 1.0,
                "sort_order": order,
            }
        )
        order += 1
    return out


# 角色池 UP 内容（示例卡池）
CHARACTER_UP_5 = ["胡桃", "夜兰"]
CHARACTER_UP_4 = ["行秋", "香菱", "北斗"]
CHARACTER_OFF_4_CHARS = [c for c in STANDARD_4_CHARACTERS if c not in CHARACTER_UP_4]

# 武器池 UP 内容（示例卡池）
WEAPON_UP_5 = ["护摩之杖", "若水"]
WEAPON_UP_4 = ["匣里灭辰", "西风剑", "弓藏"]
WEAPON_OFF_4 = [w for w in STANDARD_4_WEAPONS if w not in WEAPON_UP_4]


def build_default_pools() -> list[dict[str, Any]]:
    """四个默认卡池的完整定义（含规则与物品）。"""
    return [
        # ------------------------------------------------------------------
        # 1. 角色活动祈愿
        # ------------------------------------------------------------------
        {
            "key": "character-event",
            # 名称是「本期卡池的名字」，类型另由 pool_type 展示（界面上的类型标签）。
            # 另外三个默认卡池都遵循这个约定（神铸赋形 / 奔行世间 / 初行者推荐祈愿），
            # 这里若拿类型当名字，海报卡上的标签与标题就会重复。
            "name": "杯装之诗",
            "pool_type": PoolType.CHARACTER,
            "pity_group": PoolType.CHARACTER,
            "description": "限定五星角色概率 UP。保底计数与其它角色活动祈愿互通。",
            "sort_order": 10,
            "is_current": True,
            "is_main": True,
            "cost_currency": CurrencyType.INTERTWINED_FATE,
            "cost_per_pull": 1,
            "cost_per_ten": 10,
            "rules": {
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
                "epitomized_enabled": False,
                "capturing_radiance_enabled": False,
            },
            "items": _items(
                five=[(n, ItemType.CHARACTER, True) for n in CHARACTER_UP_5]
                + [(n, ItemType.CHARACTER, False) for n in STANDARD_5_CHARACTERS],
                four=[(n, ItemType.CHARACTER, True) for n in CHARACTER_UP_4]
                + [(n, ItemType.CHARACTER, False) for n in CHARACTER_OFF_4_CHARS]
                + [(n, ItemType.WEAPON, False) for n in STANDARD_4_WEAPONS],
                three=STANDARD_3_WEAPONS,
            ),
        },
        # ------------------------------------------------------------------
        # 2. 武器活动祈愿（含定轨）
        # ------------------------------------------------------------------
        {
            "key": "weapon-event",
            # 与实机一致：武器活动祈愿的标题固定是「神铸赋形」
            "name": "神铸赋形",
            "pool_type": PoolType.WEAPON,
            "pity_group": PoolType.WEAPON,
            "description": "限定五星武器概率 UP，可定轨指定武器。命定值满后必出定轨武器。",
            "sort_order": 20,
            "is_current": True,
            "is_main": False,
            "cost_currency": CurrencyType.INTERTWINED_FATE,
            "cost_per_pull": 1,
            "cost_per_ten": 10,
            "rules": {
                "base_rate_5": 0.007,
                "base_rate_4": 0.060,  # 武器池 4★ 基础概率是 6.0%
                "hard_pity_5": 80,
                "hard_pity_4": 10,
                "soft_pity_enabled": True,
                "soft_pity_start": 63,
                "soft_pity_step": 0.07,
                "up_rate_5": 0.75,  # 75% 出两把 UP 之一，各 37.5%
                "up_rate_4": 0.75,
                "has_5050": True,
                "epitomized_enabled": True,
                # 5.0（2024-08-28）官方把命定值从 2 降为 1，默认对齐当前线上
                "epitomized_max_points": 1,
                "capturing_radiance_enabled": False,
            },
            "items": _items(
                five=[(n, ItemType.WEAPON, True) for n in WEAPON_UP_5]
                + [(n, ItemType.WEAPON, False) for n in STANDARD_5_WEAPONS],
                four=[(n, ItemType.WEAPON, True) for n in WEAPON_UP_4]
                + [(n, ItemType.WEAPON, False) for n in WEAPON_OFF_4],
                three=STANDARD_3_WEAPONS,
            ),
        },
        # ------------------------------------------------------------------
        # 3. 常驻祈愿
        # ------------------------------------------------------------------
        {
            "key": "standard",
            "name": "奔行世间",
            "pool_type": PoolType.STANDARD,
            "pity_group": PoolType.STANDARD,
            "description": "常驻祈愿，无 UP。五星角色与武器均来自固定池。",
            "sort_order": 30,
            "is_current": True,
            "is_main": False,
            "cost_currency": CurrencyType.ACQUAINT_FATE,
            "cost_per_pull": 1,
            "cost_per_ten": 10,
            "rules": {
                "base_rate_5": 0.006,
                "base_rate_4": 0.051,
                "hard_pity_5": 90,
                "hard_pity_4": 10,
                "soft_pity_enabled": True,
                "soft_pity_start": 74,
                "soft_pity_step": 0.06,
                "has_5050": False,
                "epitomized_enabled": False,
                "capturing_radiance_enabled": False,
            },
            "items": _items(
                five=[(n, ItemType.CHARACTER, False) for n in STANDARD_5_CHARACTERS]
                + [(n, ItemType.WEAPON, False) for n in STANDARD_5_WEAPONS],
                four=[(n, ItemType.CHARACTER, False) for n in STANDARD_4_CHARACTERS]
                + [(n, ItemType.WEAPON, False) for n in STANDARD_4_WEAPONS],
                three=STANDARD_3_WEAPONS,
            ),
        },
        # ------------------------------------------------------------------
        # 4. 新手祈愿
        # ------------------------------------------------------------------
        {
            "key": "beginner",
            "name": "初行者推荐祈愿",
            "pool_type": PoolType.BEGINNER,
            "pity_group": PoolType.BEGINNER,
            # 单独一个保底组，绝不与常驻池共用：否则 8 折的相遇之缘会免费
            # 推进常驻池的保底计数，是一个经济漏洞。
            "description": "新手专属，八折十连，仅限 20 抽且只会获得角色。",
            "sort_order": 40,
            "is_current": True,
            "is_main": False,
            "cost_currency": CurrencyType.ACQUAINT_FATE,
            "cost_per_pull": 1,
            "cost_per_ten": 8,  # 八折
            "lifetime_pull_cap": 20,
            # 两个开关都显式写出：只写一个的话另一个会落到数据库列默认值，
            # 而"单抽与十连同时禁用"是非法的 —— 不该依赖隐式默认值
            "allows_single_pull": False,  # 与线上一致：只能十连
            "allows_ten_pull": True,
            "rules": {
                "base_rate_5": 0.006,
                "base_rate_4": 0.051,
                "hard_pity_5": 90,  # 20 抽上限下够不到
                "hard_pity_4": 10,
                "soft_pity_enabled": True,
                "soft_pity_start": 74,
                "soft_pity_step": 0.06,
                "has_5050": False,
                "epitomized_enabled": False,
                "capturing_radiance_enabled": False,
            },
            "items": _items(
                five=[(n, ItemType.CHARACTER, False) for n in STANDARD_5_CHARACTERS],
                four=[(n, ItemType.CHARACTER, False) for n in STANDARD_4_CHARACTERS + ["诺艾尔"]],
                three=STANDARD_3_WEAPONS,
            ),
        },
    ]
