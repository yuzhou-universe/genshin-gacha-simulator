"""卡池与卡池物品。

设计要点：所有抽卡规则都是**显式类型化列**，不是路由里的魔法数字，也不是
无人校验的 JSON blob。管理员通过后台表单直接编辑这些列，Alembic 能追踪，
数据库层有默认值与非空约束。校验逻辑集中在 ``app/services/pool_rules.py``。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TZDateTime, utcnow
from app.models.enums import CurrencyType, ItemType, PoolType

if TYPE_CHECKING:
    pass

# 说明：下列规则列同时给出 Python 侧 ``default`` 与 ``server_default``。
# 只给 Python 侧默认值的话，DDL 里这些列是可空的且默认 NULL —— 任何绕过 ORM 的
# INSERT（种子脚本、回填迁移、手工 SQL）都会写进 NULL，随后 rate_5() 会在
# `None > x` 上抛 TypeError。规则既然要"放在数据库里"，就应当由数据库自己兜底。


class Pool(Base):
    __tablename__ = "pools"
    __table_args__ = (UniqueConstraint("key", name="uq_pools_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ---- 以下 4 列是唯一必填项，其余列全部由数据库默认值兜底 ----
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    pool_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 保底组。故意不给默认值：强制调用方显式声明归属，这正是防住"新手池被映射到
    # 常驻池保底组"（8 折相遇之缘免费推进常驻池保底）那个经济漏洞的关键。
    pity_group: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    description: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )

    # ---- 素材 ----
    banner_url: Mapped[str] = mapped_column(
        String(512), default="", server_default="", nullable=False
    )
    background_image_url: Mapped[str] = mapped_column(
        String(512), default="", server_default="", nullable=False
    )
    icon_url: Mapped[str] = mapped_column(
        String(512), default="", server_default="", nullable=False
    )
    # 预留：抽卡动画固定为同一套，此字段仅存储，不驱动前端分支。
    animation_key: Mapped[str] = mapped_column(
        String(64), default="default", server_default="default", nullable=False
    )

    # ---- 开放状态 ----
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    is_main: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    open_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    close_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    # ---- 消耗 ----
    cost_currency: Mapped[str] = mapped_column(
        String(32),
        default=CurrencyType.INTERTWINED_FATE,
        server_default=CurrencyType.INTERTWINED_FATE.value,
        nullable=False,
    )
    cost_per_pull: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    cost_per_ten: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 新手池上限 20 抽；为 None 表示不限
    lifetime_pull_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allows_single_pull: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    allows_ten_pull: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )

    # ---- 概率与保底规则 ----
    base_rate_5: Mapped[float] = mapped_column(
        Float, default=0.006, server_default="0.006", nullable=False
    )
    base_rate_4: Mapped[float] = mapped_column(
        Float, default=0.051, server_default="0.051", nullable=False
    )
    hard_pity_5: Mapped[int] = mapped_column(
        Integer, default=90, server_default="90", nullable=False
    )
    hard_pity_4: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10", nullable=False
    )
    soft_pity_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    soft_pity_start: Mapped[int] = mapped_column(
        Integer, default=74, server_default="74", nullable=False
    )
    soft_pity_step: Mapped[float] = mapped_column(
        Float, default=0.06, server_default="0.06", nullable=False
    )

    # ---- UP 规则 ----
    # 角色池 0.5，武器池 0.75，常驻池无 UP（has_5050=False 时该值不参与结算）
    up_rate_5: Mapped[float] = mapped_column(
        Float, default=0.5, server_default="0.5", nullable=False
    )
    up_rate_4: Mapped[float] = mapped_column(
        Float, default=0.5, server_default="0.5", nullable=False
    )
    has_5050: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )

    # ---- 定向定轨（武器池） ----
    epitomized_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # 5.0（2024-08-28）官方把命定值从 2 降为 1，默认对齐当前线上，可改回 2。
    epitomized_max_points: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    # ---- 捕获明光（5.0 新增，仅角色池生效）----
    capturing_radiance_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )

    items: Mapped[list[PoolItem]] = relationship(
        back_populates="pool",
        cascade="all, delete-orphan",
        order_by="PoolItem.sort_order",
        lazy="selectin",
    )

    # ------------------------------------------------------------------
    # 便捷分组：抽卡引擎只认这些，不关心具体物品
    # ------------------------------------------------------------------
    @property
    def is_beginner(self) -> bool:
        return self.pool_type == PoolType.BEGINNER

    def items_by_star(self, star: int) -> list[PoolItem]:
        return [i for i in self.items if i.star == star and i.is_active]

    def up_items(self, star: int) -> list[PoolItem]:
        return [i for i in self.items if i.star == star and i.is_up and i.is_active]

    def offbanner_items(self, star: int, item_type: str | None = None) -> list[PoolItem]:
        """非 UP 池。``item_type`` 用于把歪池限定在正确的类别上——
        角色池歪了不能掉武器，武器池歪了不能掉角色。"""
        result = [i for i in self.items if i.star == star and not i.is_up and i.is_active]
        if item_type is not None:
            result = [i for i in result if i.item_type == item_type]
        return result

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Pool id={self.id} key={self.key!r} type={self.pool_type}>"


class PoolItem(Base):
    __tablename__ = "pool_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    item_type: Mapped[str] = mapped_column(
        String(16),
        default=ItemType.CHARACTER,
        server_default=ItemType.CHARACTER.value,
        nullable=False,
    )
    star: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    is_up: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False, index=True
    )
    weight: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )
    image_url: Mapped[str] = mapped_column(
        String(512), default="", server_default="", nullable=False
    )
    icon_url: Mapped[str] = mapped_column(
        String(512), default="", server_default="", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    pool: Mapped[Pool] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<PoolItem id={self.id} {self.name!r} {self.star}star up={self.is_up}>"
