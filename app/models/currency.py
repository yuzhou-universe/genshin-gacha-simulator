"""用户货币余额。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class UserCurrency(Base):
    __tablename__ = "user_currencies"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    primogems: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    intertwined_fate: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    acquaint_fate: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    user: Mapped[User] = relationship(back_populates="currency")

    def balance_of(self, currency: str) -> int:
        from app.models.enums import CurrencyType

        match currency:
            case CurrencyType.PRIMOGEMS:
                return self.primogems
            case CurrencyType.INTERTWINED_FATE:
                return self.intertwined_fate
            case CurrencyType.ACQUAINT_FATE:
                return self.acquaint_fate
            case _:  # pragma: no cover - 枚举已封闭
                raise ValueError(f"未知货币类型: {currency}")
