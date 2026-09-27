"""保底状态与武器定轨状态。

**这两张表必须分开**，因为它们的生命周期完全不同：

- ``UserPity`` 按 ``(user, pity_group)`` 存储，**永久继承**，跨卡池、跨版本都不清零。
- ``UserEpitomizedPath`` 按 ``(user, pool_id)`` 存储，**卡池结束即归零**，绝不跨池继承。

合并成一张表几乎必然写错（要么命定值被错误继承而白送定轨武器，要么保底被错误清零）。
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserPity(Base):
    """用户在某个保底组下的保底计数与大保底标志。"""

    __tablename__ = "user_pities"
    __table_args__ = (UniqueConstraint("user_id", "pity_group", name="uq_user_pities_user_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pity_group: Mapped[str] = mapped_column(String(32), nullable=False)

    #: 距上一个 5★ 的抽数。出 5★ 后归零。
    pity_5: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    #: 距上一个 4★ 的抽数。**注意语义**：出 5★ 不会重置它。
    #: 它可能增长到超过 hard_pity_4（当 4★ 保底那一抽被 5★ 判定抢先命中时），
    #: 因此比较一律用 >=，且**不要**断言或存储 clamp 到 10。
    pity_4: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    #: 大保底：上一次 5★ 歪了，则下一次 5★ 必定是 UP。
    guaranteed_5: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    #: 4★ 大保底：上一次 4★ 歪了，则下一次 4★ 必定是 UP。
    guaranteed_up_4: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    #: 捕获明光计数：连续多少次"第二个 5★ 才中 UP"。达到 3 次后下次必不歪。
    radiance_streak: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<UserPity user={self.user_id} group={self.pity_group!r} "
            f"p5={self.pity_5} p4={self.pity_4} guar={self.guaranteed_5}>"
        )


class UserEpitomizedPath(Base):
    """武器池的神铸定轨状态（命定值）。"""

    __tablename__ = "user_epitomized_paths"
    __table_args__ = (UniqueConstraint("user_id", "pool_id", name="uq_user_epitomized_user_pool"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 定轨选中的武器（必须是该卡池的 UP 5★ 武器之一）
    charted_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("pool_items.id", ondelete="SET NULL"), nullable=True
    )
    #: 命定值。达到 epitomized_max_points 后，下一个 5★ 必为定轨武器。
    fate_points: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<UserEpitomizedPath user={self.user_id} pool={self.pool_id} "
            f"charted={self.charted_item_id} fate={self.fate_points}>"
        )
