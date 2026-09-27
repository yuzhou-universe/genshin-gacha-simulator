"""默认卡池种子数据（占位，阶段 3 实现）。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_seed_pools(db: AsyncSession) -> int:
    """若库中没有任何卡池则写入默认卡池，返回新建数量。"""
    return 0
