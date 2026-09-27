"""SQLAlchemy 2.0 声明式基类与公共列类型。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

# 统一命名约定，使 Alembic 在 SQLite 上也能稳定地 batch_alter 约束。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """时区感知的当前 UTC 时间。

    全项目禁止使用 `datetime.utcnow()`（naive），因为卡池结束时间的比较
    决定命定值是否归零，naive 与 aware 相比会在抽卡中途抛 TypeError。
    """
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """把从数据库读回的时间归一到 UTC aware。

    SQLite 不存储时区信息，读回来的是 naive datetime，必须在此统一补上 UTC，
    否则后续比较会崩。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# 所有时间列统一使用带时区的 DateTime
TZDateTime = DateTime(timezone=True)
