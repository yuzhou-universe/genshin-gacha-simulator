"""首次启动时写入默认卡池。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.seed import build_default_pools
from app.models import Pool, PoolItem


async def ensure_seed_pools(db: AsyncSession) -> int:
    """库中没有任何卡池时写入默认卡池，返回新建的卡池数量。

    只在**完全为空**时写入，避免覆盖管理员已有的配置。
    """
    existing = await db.execute(select(func.count()).select_from(Pool))
    if int(existing.scalar_one()) > 0:
        return 0

    created = 0
    for spec in build_default_pools():
        payload = dict(spec)
        items = payload.pop("items")
        rules = payload.pop("rules", {})
        pool = Pool(**payload, **rules)
        for item in items:
            pool.items.append(PoolItem(**item))
        db.add(pool)
        created += 1

    await db.commit()
    return created
