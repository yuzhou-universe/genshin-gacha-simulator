"""AI 相关 API。

抽卡接口不经过这里 —— 本模块的端点都是由前端在拿到抽卡结果之后**另行**
异步请求的。所以模型超时、报错、未配置，都只影响那一句点评文案，
抽卡结果本身已经落库并完整呈现。

所有端点都不会因为 AI 失败而返回错误状态：拿不到模型输出时返回本地模板
文案，并用 ``from_model`` 字段标明来源。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.deps import DbSession, RequiredUser
from app.core.exceptions import NotFound
from app.models import PoolItem
from app.services import setting_service
from app.services.ai_service import get_ai_service
from app.services.gacha_service import get_pool

router = APIRouter(prefix="/api/ai", tags=["ai"])


class PullItemIn(BaseModel):
    """前端回传的抽卡结果。

    刻意只接受白名单字段：没有 user_id、没有批次外的任何信息。
    即使前端多传，Pydantic 也会丢弃。
    """

    name: str = Field(max_length=64)
    star: int = Field(ge=1, le=5)
    is_up: bool = False
    is_pity: bool = False


class CommentRequest(BaseModel):
    pool_id: int = Field(gt=0)
    count: int = Field(ge=1, le=100)
    items: list[PullItemIn] = Field(default_factory=list, max_length=100)
    until_hard_pity: int = Field(default=0, ge=0, le=200)
    guaranteed: bool = False


class AIResult(BaseModel):
    text: str
    #: True 表示来自模型，False 表示本地模板降级
    from_model: bool


@router.get("/status")
async def ai_status(user: RequiredUser) -> dict[str, Any]:
    """AI 可用状态。供后台与排障使用，不暴露任何密钥。"""
    from app.core.config import settings

    service = get_ai_service()
    return {
        "enabled": settings.ai_enabled,
        "available": service.available,
        "model": settings.deepseek_model,
        "base_url": settings.deepseek_base_url,
        "disable_thinking": settings.deepseek_disable_thinking,
        "last_error": service.last_error or None,
    }


@router.post("/comment", response_model=AIResult)
async def pull_comment(body: CommentRequest, db: DbSession, user: RequiredUser) -> AIResult:
    """抽卡点评。**永远不会因为 AI 失败而报错。**"""
    pool = await get_pool(db, body.pool_id)
    templates = await setting_service.get_dict(db, setting_service.KEY_PROMPT_TEMPLATES, {})

    items = [i.model_dump() for i in body.items]
    text, from_model = await get_ai_service().comment_on_pull(
        pool_name=pool.name,
        count=body.count,
        items=items,
        until_hard_pity=body.until_hard_pity,
        guaranteed=body.guaranteed,
        templates=templates,
    )
    return AIResult(text=text, from_model=from_model)


@router.get("/pool-intro/{pool_id}", response_model=AIResult)
async def pool_intro(pool_id: int, db: DbSession, user: RequiredUser) -> AIResult:
    """卡池介绍。结果按卡池缓存一小时。"""
    pool = await get_pool(db, pool_id)
    templates = await setting_service.get_dict(db, setting_service.KEY_PROMPT_TEMPLATES, {})

    service = get_ai_service()
    text = await service.pool_intro(pool, templates)
    return AIResult(text=text, from_model=service.last_error == "" and service.available)


@router.get("/history-summary", response_model=AIResult)
async def history_summary(db: DbSession, user: RequiredUser) -> AIResult:
    """对当前用户的整体抽卡历史做一句总结。"""
    from app.services import history_service
    from app.services.history_service import HistoryFilter

    stats = await history_service.compute_stats(db, user.id, HistoryFilter())
    templates = await setting_service.get_dict(db, setting_service.KEY_PROMPT_TEMPLATES, {})

    text, from_model = await get_ai_service().history_summary(stats, templates)
    return AIResult(text=text, from_model=from_model)


@router.get("/character-line/{item_id}", response_model=AIResult)
async def character_line(item_id: int, db: DbSession, user: RequiredUser) -> AIResult:
    """为某个物品生成一句角色台词。

    只把物品名送给模型 —— 物品是游戏内公开数据，不涉及用户隐私。
    """
    item = await db.get(PoolItem, item_id)
    if item is None:
        raise NotFound("物品不存在")

    text, from_model = await get_ai_service().character_line(item.name)
    return AIResult(text=text, from_model=from_model)
