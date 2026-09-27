"""管理员端请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PoolItemIn(BaseModel):
    #: 已存在的物品带上 id → 原地更新；不带的视为新增。
    #: 必须支持原地更新：整表重建会让每条物品换新 id，而
    #: UserEpitomizedPath.charted_item_id 是 ON DELETE SET NULL，
    #: 结果就是管理员改一下卡池描述也会把玩家的定轨选择清空。
    id: int | None = None

    name: str = Field(min_length=1, max_length=128)
    item_type: str = Field(default="character", pattern="^(character|weapon)$")
    star: int = Field(ge=3, le=5)
    is_up: bool = False
    weight: float = Field(default=1.0, ge=0)
    image_url: str = Field(default="", max_length=512)
    icon_url: str = Field(default="", max_length=512)
    is_active: bool = True
    sort_order: int = 0


class PoolIn(BaseModel):
    """新建 / 编辑卡池。

    规则字段全部可选：编辑时只传要改的字段，未传的保持原值。
    """

    key: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    pool_type: str | None = Field(default=None, pattern="^(character|weapon|standard|beginner)$")
    #: 保底组。留空则默认等于 pool_type —— 但**新手池必须显式指定**，
    #: 绝不能落到 standard 组，否则 8 折的相遇之缘会免费推进常驻池保底。
    pity_group: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=2000)

    banner_url: str | None = Field(default=None, max_length=512)
    background_image_url: str | None = Field(default=None, max_length=512)
    icon_url: str | None = Field(default=None, max_length=512)
    animation_key: str | None = Field(default=None, max_length=64)

    is_active: bool | None = None
    is_current: bool | None = None
    is_main: bool | None = None
    sort_order: int | None = None
    open_at: datetime | None = None
    close_at: datetime | None = None

    cost_currency: str | None = Field(
        default=None, pattern="^(primogems|intertwined_fate|acquaint_fate)$"
    )
    cost_per_pull: int | None = Field(default=None, ge=0, le=1000)
    cost_per_ten: int | None = Field(default=None, ge=0, le=10000)
    lifetime_pull_cap: int | None = Field(default=None, ge=1, le=100000)
    allows_single_pull: bool | None = None
    allows_ten_pull: bool | None = None

    base_rate_5: float | None = Field(default=None, gt=0, le=1)
    base_rate_4: float | None = Field(default=None, ge=0, le=1)
    hard_pity_5: int | None = Field(default=None, ge=1, le=1000)
    hard_pity_4: int | None = Field(default=None, ge=1, le=1000)
    soft_pity_enabled: bool | None = None
    soft_pity_start: int | None = Field(default=None, ge=1, le=1000)
    soft_pity_step: float | None = Field(default=None, ge=0, le=1)

    up_rate_5: float | None = Field(default=None, ge=0, le=1)
    up_rate_4: float | None = Field(default=None, ge=0, le=1)
    has_5050: bool | None = None

    epitomized_enabled: bool | None = None
    epitomized_max_points: int | None = Field(default=None, ge=1, le=10)
    capturing_radiance_enabled: bool | None = None

    items: list[PoolItemIn] | None = None

    @field_validator("key")
    @classmethod
    def _key_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return v
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]{1,62}", v):
            raise ValueError("标识只能包含小写字母、数字、连字符与下划线，且以字母或数字开头")
        return v


class PoolAdminOut(BaseModel):
    """后台用的卡池完整视图（含未启用、含全部物品）。"""

    id: int
    key: str
    name: str
    pool_type: str
    pity_group: str
    description: str
    background_image_url: str
    banner_url: str
    icon_url: str
    animation_key: str
    is_active: bool
    is_current: bool
    is_main: bool
    sort_order: int
    open_at: datetime | None
    close_at: datetime | None
    cost_currency: str
    cost_per_pull: int
    cost_per_ten: int | None
    lifetime_pull_cap: int | None
    allows_single_pull: bool
    allows_ten_pull: bool
    base_rate_5: float
    base_rate_4: float
    hard_pity_5: int
    hard_pity_4: int
    soft_pity_enabled: bool
    soft_pity_start: int
    soft_pity_step: float
    up_rate_5: float
    up_rate_4: float
    has_5050: bool
    epitomized_enabled: bool
    epitomized_max_points: int
    capturing_radiance_enabled: bool
    updated_at: datetime
    items: list[dict[str, Any]] = []


class GlobalStatsOut(BaseModel):
    """全局统计。"""

    total_users: int
    active_users: int
    admin_count: int
    total_pulls: int
    total_five_stars: int
    total_four_stars: int
    five_star_rate: float
    pulls_today: int
    pulls_last_7_days: int
    top_items: list[dict[str, Any]] = []
    pool_breakdown: list[dict[str, Any]] = []


class UploadOut(BaseModel):
    url: str
    size: int
    content_type: str
    original_name: str


class SettingsIn(BaseModel):
    initial_primogems: int | None = Field(default=None, ge=0, le=10_000_000)
    initial_intertwined_fate: int | None = Field(default=None, ge=0, le=100_000)
    initial_acquaint_fate: int | None = Field(default=None, ge=0, le=100_000)
    animation: dict[str, Any] | None = None
    prompt_templates: dict[str, str] | None = None
