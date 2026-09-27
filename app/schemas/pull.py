"""抽卡相关的响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.auth import CurrencyOut


class PullItemOut(BaseModel):
    """单个抽卡结果。五星物品会带上 image_url，供前端最后单独呈现。"""

    index: int
    item_id: int | None
    name: str
    star: int
    item_type: str
    is_up: bool
    is_pity: bool
    source: str
    image_url: str
    icon_url: str


class PityOut(BaseModel):
    pity_5: int
    pity_4: int
    guaranteed_5: bool
    #: 距离 5★ 硬保底还差多少抽
    until_hard_pity_5: int
    #: 距离 4★ 保底还差多少抽（用于展示，clamp 到 0）
    until_pity_4: int
    hard_pity_5: int
    hard_pity_4: int


class EpitomizedOut(BaseModel):
    enabled: bool
    charted_item_id: int | None = None
    charted_item_name: str | None = None
    fate_points: int = 0
    max_points: int = 1
    #: 可定轨的五星武器（前端下拉用）
    options: list[dict] = []


class PullResultOut(BaseModel):
    batch_id: str
    pool_id: int
    pool_name: str
    pool_type: str
    count: int
    items: list[PullItemOut]
    cost_currency: str
    cost_amount: int
    currency: CurrencyOut
    pity: PityOut
    epitomized: EpitomizedOut | None = None
    #: 本批是否含五星 —— 前端据此决定要不要播放五星专属片段
    has_five_star: bool
