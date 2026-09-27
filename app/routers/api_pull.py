"""抽卡 API。

抽卡接口**立即返回结果**，不等待 AI 点评。点评由前端拿到结果后另行请求
``/api/ai/comment`` —— 这样"AI 失败不影响抽卡"是结构上的保证，而不是靠
try/except 兜出来的。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.deps import DbSession, RequiredUser
from app.schemas.pull import EpitomizedOut, PullResultOut
from app.services.gacha_service import GachaService, get_or_create_pity, get_pool
from app.services.pool_rules import PoolRules

router = APIRouter(prefix="/api/pull", tags=["pull"])


class PullRequest(BaseModel):
    pool_id: int = Field(gt=0)
    count: int = Field(default=1, ge=1, le=10)


class EpitomizedRequest(BaseModel):
    pool_id: int = Field(gt=0)
    #: null 表示取消定轨
    item_id: int | None = None


async def _do_pull(db, user, pool_id: int, count: int) -> PullResultOut:
    return await GachaService(db).pull(user, pool_id, count)


@router.post("/single", response_model=PullResultOut)
async def pull_single(body: PullRequest, db: DbSession, user: RequiredUser) -> PullResultOut:
    return await _do_pull(db, user, body.pool_id, 1)


@router.post("/ten", response_model=PullResultOut)
async def pull_ten(body: PullRequest, db: DbSession, user: RequiredUser) -> PullResultOut:
    return await _do_pull(db, user, body.pool_id, 10)


@router.post("", response_model=PullResultOut)
async def pull(body: PullRequest, db: DbSession, user: RequiredUser) -> PullResultOut:
    """通用入口，``count`` 只接受 1 或 10。"""
    return await _do_pull(db, user, body.pool_id, body.count)


@router.post("/epitomized", response_model=EpitomizedOut)
async def set_epitomized(
    body: EpitomizedRequest, db: DbSession, user: RequiredUser
) -> EpitomizedOut:
    """设置 / 更换 / 取消武器池定轨。

    换武器与取消都会把命定值清零；**重选同一把则保留**已积累的命定值。
    """
    return await GachaService(db).set_epitomized_path(user, body.pool_id, body.item_id)


@router.get("/pity/{pool_id}")
async def get_pity(pool_id: int, db: DbSession, user: RequiredUser) -> dict:
    """查询某卡池的保底进度与定轨状态（不抽卡，不消耗）。"""
    pool = await get_pool(db, pool_id)
    service = GachaService(db)
    pity = await get_or_create_pity(db, user.id, pool.pity_group)
    rules = PoolRules.from_pool(pool)
    return {
        "pool_id": pool.id,
        "pity_5": pity.pity_5,
        "pity_4": pity.pity_4,
        "guaranteed_5": pity.guaranteed_5,
        "until_hard_pity_5": max(0, rules.hard_pity_5 - pity.pity_5),
        "until_pity_4": max(0, rules.hard_pity_4 - pity.pity_4),
        "epitomized": await service.get_epitomized(user, pool),
    }
