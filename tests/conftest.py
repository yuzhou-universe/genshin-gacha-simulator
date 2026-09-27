"""测试夹具。

引擎层的测试**不需要数据库**：``FakePool`` / ``FakeItem`` 提供了引擎真正用到的
那几个字段，因此保底、大保底、定轨这些状态机可以脱离持久化被快速、确定地验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from app.models.enums import CurrencyType, ItemType, PoolType
from app.services.gacha_engine import EpitomizedState, GachaEngine, PityState
from app.services.pool_rules import PoolRules
from app.services.rng import ScriptedRandomSource


@dataclass
class FakeItem:
    id: int
    name: str
    star: int
    item_type: str = ItemType.CHARACTER
    is_up: bool = False
    weight: float = 1.0
    is_active: bool = True

    def __repr__(self) -> str:
        return f"<{self.name} {self.star}★{'UP' if self.is_up else ''}>"


@dataclass
class FakePool:
    pool_type: str = PoolType.CHARACTER
    items: list[FakeItem] = field(default_factory=list)
    close_at: datetime | None = None
    open_at: datetime | None = None


# ---------------------------------------------------------------------------
# 物品工厂：构造各卡池所需的物品集合
# ---------------------------------------------------------------------------
def character_items() -> list[FakeItem]:
    """角色池：2 个 UP 五星角色 + 2 个常驻五星角色 + 4★ + 3★。"""
    return [
        FakeItem(1, "胡桃", 5, ItemType.CHARACTER, is_up=True),
        FakeItem(2, "夜兰", 5, ItemType.CHARACTER, is_up=True),
        FakeItem(3, "琴", 5, ItemType.CHARACTER),
        FakeItem(4, "迪卢克", 5, ItemType.CHARACTER),
        FakeItem(10, "行秋", 4, ItemType.CHARACTER, is_up=True),
        FakeItem(11, "香菱", 4, ItemType.CHARACTER, is_up=True),
        FakeItem(12, "北斗", 4, ItemType.CHARACTER),
        FakeItem(13, "祭礼剑", 4, ItemType.WEAPON),
        FakeItem(20, "冷刃", 3, ItemType.WEAPON),
        FakeItem(21, "魔导绪论", 3, ItemType.WEAPON),
    ]


def weapon_items() -> list[FakeItem]:
    """武器池：2 把 UP 五星武器 + 常驻五星**武器**（歪池必须是武器）。"""
    return [
        FakeItem(30, "护摩之杖", 5, ItemType.WEAPON, is_up=True),
        FakeItem(31, "若水", 5, ItemType.WEAPON, is_up=True),
        FakeItem(32, "天空之刃", 5, ItemType.WEAPON),
        FakeItem(33, "狼的末路", 5, ItemType.WEAPON),
        FakeItem(40, "匣里灭辰", 4, ItemType.WEAPON, is_up=True),
        FakeItem(41, "西风剑", 4, ItemType.WEAPON),
        FakeItem(20, "冷刃", 3, ItemType.WEAPON),
    ]


def standard_items() -> list[FakeItem]:
    """常驻池：无 UP。"""
    return [
        FakeItem(3, "琴", 5, ItemType.CHARACTER),
        FakeItem(4, "迪卢克", 5, ItemType.CHARACTER),
        FakeItem(32, "天空之刃", 5, ItemType.WEAPON),
        FakeItem(12, "北斗", 4, ItemType.CHARACTER),
        FakeItem(41, "西风剑", 4, ItemType.WEAPON),
        FakeItem(20, "冷刃", 3, ItemType.WEAPON),
    ]


# ---------------------------------------------------------------------------
# 引擎工厂
# ---------------------------------------------------------------------------
def make_engine(
    *,
    pool_type: str = PoolType.CHARACTER,
    rules: PoolRules | None = None,
    items: list[FakeItem] | None = None,
    pity: PityState | None = None,
    epitomized: EpitomizedState | None = None,
    rng: ScriptedRandomSource | None = None,
    close_at: datetime | None = None,
) -> GachaEngine:
    """构造一个可直接 pull_once() 的引擎。"""
    if items is None:
        items = {
            PoolType.CHARACTER: character_items,
            PoolType.WEAPON: weapon_items,
        }.get(pool_type, standard_items)()

    if rules is None:
        rules = {
            PoolType.CHARACTER: PoolRules.character,
            PoolType.WEAPON: PoolRules.weapon,
            PoolType.BEGINNER: PoolRules.beginner,
        }.get(pool_type, PoolRules.standard)()

    return GachaEngine(
        pool=FakePool(pool_type=pool_type, items=items, close_at=close_at),
        rules=rules,
        pity=pity or PityState(),
        epitomized=epitomized,
        rng=rng or ScriptedRandomSource(),
    )


@pytest.fixture
def rng() -> ScriptedRandomSource:
    return ScriptedRandomSource()


# ---------------------------------------------------------------------------
# 数据库夹具：真实 SQLite 文件（非内存），每个测试一个全新的库
# ---------------------------------------------------------------------------
@pytest.fixture
async def db_engine(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.base import Base
    from app.models import (  # noqa: F401  确保所有表都注册进 metadata
        AdminLog,
        AppSetting,
        LoginAttempt,
        Pool,
        PoolItem,
        PullRecord,
        User,
        UserCurrency,
        UserEpitomizedPath,
        UserPity,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_async_engine(url, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def seeded_db(db_session):
    """写入四个默认卡池。"""
    from app.services.seed_service import ensure_seed_pools

    await ensure_seed_pools(db_session)
    return db_session


@pytest.fixture
async def user(seeded_db):
    """一个已注册的普通用户，带初始货币。"""
    from app.services import auth_service

    return await auth_service.register(seeded_db, "tester", "Tester123")


@pytest.fixture
async def pool_id(seeded_db):
    """默认角色活动祈愿的 id。"""
    from sqlalchemy import select

    from app.models import Pool, PoolType

    result = await seeded_db.execute(select(Pool).where(Pool.pool_type == PoolType.CHARACTER))
    return result.scalars().first().id


__all__ = [
    "CurrencyType",
    "EpitomizedState",
    "FakeItem",
    "FakePool",
    "PityState",
    "character_items",
    "make_engine",
    "standard_items",
    "weapon_items",
]
