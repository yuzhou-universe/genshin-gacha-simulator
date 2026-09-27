"""卡池 API（用户端）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import DbSession, RequiredUser
from app.models import Pool
from app.schemas.pool import PoolItemOut, PoolOut
from app.services import gacha_service
from app.services.pool_rules import PoolRules
from app.services.rates import soft_pity_cap_pull

router = APIRouter(prefix="/api/pools", tags=["pools"])


def pool_to_out(pool: Pool, pulls_done: int = 0) -> PoolOut:
    """把 ORM 卡池转成前端需要的完整视图（含概率说明与 UP 内容）。"""
    rules = PoolRules.from_pool(pool)
    remaining = None
    if pool.lifetime_pull_cap is not None:
        remaining = max(0, pool.lifetime_pull_cap - pulls_done)

    def to_items(star: int) -> list[PoolItemOut]:
        return [
            PoolItemOut(
                id=i.id,
                name=i.name,
                item_type=i.item_type,
                star=i.star,
                is_up=i.is_up,
                image_url=i.image_url,
                icon_url=i.icon_url,
            )
            for i in pool.up_items(star)
        ]

    return PoolOut(
        id=pool.id,
        key=pool.key,
        name=pool.name,
        pool_type=pool.pool_type,
        description=pool.description,
        background_image_url=pool.background_image_url,
        icon_url=pool.icon_url,
        banner_url=pool.banner_url,
        animation_key=pool.animation_key,
        is_main=pool.is_main,
        sort_order=pool.sort_order,
        open_at=pool.open_at.isoformat() if pool.open_at else None,
        close_at=pool.close_at.isoformat() if pool.close_at else None,
        cost_currency=pool.cost_currency,
        cost_per_pull=pool.cost_per_pull,
        cost_per_ten=pool.cost_per_ten,
        allows_single_pull=pool.allows_single_pull,
        allows_ten_pull=pool.allows_ten_pull,
        lifetime_pull_cap=pool.lifetime_pull_cap,
        pulls_done=pulls_done,
        remaining_pulls=remaining,
        base_rate_5=pool.base_rate_5,
        base_rate_4=pool.base_rate_4,
        hard_pity_5=pool.hard_pity_5,
        hard_pity_4=pool.hard_pity_4,
        soft_pity_enabled=pool.soft_pity_enabled,
        soft_pity_start=pool.soft_pity_start,
        soft_pity_step=pool.soft_pity_step,
        up_rate_5=pool.up_rate_5,
        has_5050=pool.has_5050,
        epitomized_enabled=pool.epitomized_enabled,
        epitomized_max_points=pool.epitomized_max_points,
        capturing_radiance_enabled=pool.capturing_radiance_enabled,
        soft_pity_cap_pull=soft_pity_cap_pull(rules),
        up_items_5=to_items(5),
        up_items_4=to_items(4),
    )


@router.get("", response_model=list[PoolOut])
async def list_pools(db: DbSession, user: RequiredUser) -> list[PoolOut]:
    """只返回启用且当前开放的卡池。"""
    pools = await gacha_service.list_open_pools(db)
    out: list[PoolOut] = []
    for pool in pools:
        done = 0
        if pool.lifetime_pull_cap is not None:
            done = await gacha_service.count_pulls_on_pool(db, user.id, pool.id)
        out.append(pool_to_out(pool, done))
    return out
