"""历史记录与统计的响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PullRecordOut(BaseModel):
    id: int
    pool_id: int | None
    pool_name: str
    pool_type: str
    item_id: int | None
    item_name: str
    item_type: str
    item_image: str
    star: int
    is_up: bool
    is_pity: bool
    source: str
    #: 该次出金实际消耗的抽数（仅五星有意义）
    pulls_used: int
    batch_id: str
    created_at: datetime


class PageOut(BaseModel):
    items: list[PullRecordOut]
    total: int
    page: int
    page_size: int
    pages: int


class PoolProgressOut(BaseModel):
    """各卡池的保底进度。"""

    pool_id: int | None
    pool_name: str
    pool_type: str
    pity_5: int
    pity_4: int
    hard_pity_5: int
    hard_pity_4: int
    guaranteed_5: bool
    total_pulls: int
    #: 定轨状态（仅武器池）
    charted_item_name: str | None = None
    fate_points: int = 0
    epitomized_max_points: int = 0


class StatsOut(BaseModel):
    total_pulls: int
    five_star_count: int
    four_star_count: int
    three_star_count: int
    #: 五星中 UP 的数量
    five_star_up_count: int
    #: 所有星级中的 UP 数量
    up_count: int
    #: 平均多少抽出一金（按每个五星实际消耗的抽数求平均）
    average_pulls_per_five_star: float | None
    #: 综合出金率
    five_star_rate: float
    #: 最非 / 最欧的一次出金
    max_pulls_for_five_star: int | None
    min_pulls_for_five_star: int | None
    #: 歪了多少次
    lost_5050_count: int
    pool_progress: list[PoolProgressOut] = []
    #: 当前使用的筛选条件回显
    applied_filters: dict = {}
