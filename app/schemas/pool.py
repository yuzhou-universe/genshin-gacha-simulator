"""卡池相关的响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class PoolItemOut(BaseModel):
    id: int
    name: str
    item_type: str
    star: int
    is_up: bool
    image_url: str
    icon_url: str


class PoolOut(BaseModel):
    """用户端可见的卡池。技能与规则一并下发，前端据此展示概率说明与保底进度。"""

    id: int
    key: str
    name: str
    pool_type: str
    description: str
    background_image_url: str
    icon_url: str
    banner_url: str
    animation_key: str
    is_main: bool
    sort_order: int
    #: 时间窗
    open_at: str | None = None
    close_at: str | None = None
    #: 抽卡限制
    cost_currency: str
    cost_per_pull: int
    cost_per_ten: int | None
    allows_single_pull: bool
    allows_ten_pull: bool
    lifetime_pull_cap: int | None
    pulls_done: int = 0
    remaining_pulls: int | None = None
    #: 概率说明
    base_rate_5: float
    base_rate_4: float
    hard_pity_5: int
    hard_pity_4: int
    soft_pity_enabled: bool
    soft_pity_start: int
    soft_pity_step: float
    up_rate_5: float
    has_5050: bool
    epitomized_enabled: bool
    epitomized_max_points: int
    capturing_radiance_enabled: bool
    #: 软保底到达 100% 的抽数（None 表示恰好落在硬保底上）
    soft_pity_cap_pull: int | None = None
    #: UP 内容
    up_items_5: list[PoolItemOut] = []
    up_items_4: list[PoolItemOut] = []
