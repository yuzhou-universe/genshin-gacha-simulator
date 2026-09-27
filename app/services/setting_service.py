"""全局配置读写（AppSetting 表）。

管理员可在后台修改这些值；未设置时回退到 .env / 代码默认值。
值统一以 JSON 文本存储，因此可以承载 int / str / dict。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AppSetting

# ---- 配置键 ----
KEY_INITIAL_PRIMOGEMS = "initial_primogems"
KEY_INITIAL_INTERTWINED = "initial_intertwined_fate"
KEY_INITIAL_ACQUAINT = "initial_acquaint_fate"
KEY_ANIMATION = "animation_config"
KEY_PROMPT_TEMPLATES = "prompt_templates"

#: 抽卡动画的全局配置。所有卡池共用同一套动画，不随卡池变化，
#: 这里只允许调整时长与是否可跳过。
DEFAULT_ANIMATION: dict[str, Any] = {
    "enabled": True,
    "reveal_delay_ms": 220,
    "five_star_intro_ms": 1600,
    "skippable": True,
}


async def get_raw(db: AsyncSession, key: str) -> Any | None:
    row = await db.get(AppSetting, key)
    if row is None or not row.value:
        return None
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return None


async def set_raw(db: AsyncSession, key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=payload))
    else:
        row.value = payload


async def get_int(db: AsyncSession, key: str, default: int) -> int:
    value = await get_raw(db, key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


async def get_dict(db: AsyncSession, key: str, default: dict[str, Any]) -> dict[str, Any]:
    value = await get_raw(db, key)
    if not isinstance(value, dict):
        return dict(default)
    merged = dict(default)
    merged.update(value)
    return merged


async def get_initial_currency(db: AsyncSession) -> tuple[int, int, int]:
    """新用户初始货币 (原石, 纠缠之缘, 相遇之缘)。"""
    return (
        await get_int(db, KEY_INITIAL_PRIMOGEMS, settings.initial_primogems),
        await get_int(db, KEY_INITIAL_INTERTWINED, settings.initial_intertwined_fate),
        await get_int(db, KEY_INITIAL_ACQUAINT, settings.initial_acquaint_fate),
    )


async def get_animation_config(db: AsyncSession) -> dict[str, Any]:
    return await get_dict(db, KEY_ANIMATION, DEFAULT_ANIMATION)
