"""全局配置键值表。

存放管理员可在后台调整、但不需要独立建模的配置：新用户初始货币、
抽卡动画参数、AI 提示词模板等。值统一以 JSON 文本存储，读写走
``app/services/setting_service.py``（带默认值回退）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TZDateTime, utcnow


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<AppSetting {self.key!r}>"
