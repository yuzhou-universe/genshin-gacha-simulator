"""抽卡记录。

关键设计：卡池名与物品名以**快照**形式冗余存储，外键使用 ``ON DELETE SET NULL``。
管理员删除卡池或物品后，历史记录依然完整可读、统计不失真，同时不会因为
``ON DELETE RESTRICT`` 而无法删除卡池。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TZDateTime, utcnow


class PullRecord(Base):
    __tablename__ = "pull_records"
    __table_args__ = (
        # 历史页按用户 + 时间倒序分页，这是最热的查询路径
        Index("ix_pull_records_user_created", "user_id", "created_at"),
        Index("ix_pull_records_user_pool_star", "user_id", "pool_id", "star"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ---- 卡池（可空 + 快照，允许管理员删池而不毁历史）----
    pool_id: Mapped[int | None] = mapped_column(
        ForeignKey("pools.id", ondelete="SET NULL"), nullable=True
    )
    pool_name_snapshot: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    pool_type_snapshot: Mapped[str] = mapped_column(String(16), default="", nullable=False)

    # ---- 物品（可空 + 快照）----
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("pool_items.id", ondelete="SET NULL"), nullable=True
    )
    item_name_snapshot: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    item_type_snapshot: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    item_image_snapshot: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    # ---- 结果 ----
    star: Mapped[int] = mapped_column(Integer, nullable=False)
    is_up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 是否由保底/大保底/定轨强制产生（而非自然概率命中）
    is_pity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 出金来源：base / soft_pity / hard_pity / guarantee / epitomized
    source: Mapped[str] = mapped_column(String(16), default="base", nullable=False)

    # ---- 结算时的计数器状态 ----
    #: 本抽之前距上一个 5★ 的抽数。出金时 ``pity_5_before + 1`` 即为该金的海抽数，
    #: 这正是"平均出金抽数"统计需要的量。
    pity_5_before: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pity_4_before: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 本抽结算后使用的 5★ 概率（便于核对概率表）
    rate_5_used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # ---- 批次 ----
    batch_id: Mapped[str] = mapped_column(String(36), default="", nullable=False, index=True)
    #: 本抽在十连中的序号，从 1 开始
    pull_index_in_batch: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # ---- 消耗 ----
    cost_currency: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    cost_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<PullRecord id={self.id} user={self.user_id} {self.item_name_snapshot!r} {self.star}star>"
