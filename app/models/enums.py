"""领域枚举。全部使用 str 枚举，便于直接落库与 JSON 序列化。"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class PoolType(StrEnum):
    """卡池类型。同时作为保底组的默认值。"""

    CHARACTER = "character"  # 角色活动祈愿（限定 UP）
    WEAPON = "weapon"  # 武器活动祈愿（限定 UP + 定轨）
    STANDARD = "standard"  # 常驻祈愿（无 UP）
    BEGINNER = "beginner"  # 新手祈愿（限 20 抽）


class ItemType(StrEnum):
    CHARACTER = "character"
    WEAPON = "weapon"


class CurrencyType(StrEnum):
    PRIMOGEMS = "primogems"  # 原石
    INTERTWINED_FATE = "intertwined_fate"  # 纠缠之缘（角色/武器池）
    ACQUAINT_FATE = "acquaint_fate"  # 相遇之缘（常驻/新手池）

    @property
    def label(self) -> str:
        return {
            CurrencyType.PRIMOGEMS: "原石",
            CurrencyType.INTERTWINED_FATE: "纠缠之缘",
            CurrencyType.ACQUAINT_FATE: "相遇之缘",
        }[self]


#: 1 纠缠之缘 / 相遇之缘 == 160 原石
PRIMOGEMS_PER_FATE = 160

#: 星级对应的展示颜色（原神风格），供后端模板与前端共用
STAR_COLORS: dict[int, str] = {
    3: "#5e7bb8",
    4: "#a256e1",
    5: "#ffb13f",
}
