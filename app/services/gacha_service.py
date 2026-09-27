"""抽卡服务：把纯逻辑引擎接到数据库、货币与事务上。

事务顺序（每一步都不能调换）：

1. 校验卡池开放状态与抽数合法性
2. **先扣费** —— 余额不足时直接失败，此时尚未消耗任何随机数、未推进任何保底、
   未写入任何记录。放在最后扣费会出现"抽完了才发现钱不够"的半成品状态。
3. 逐抽结算（读写实时的保底状态，绝不快照）
4. 写 PullRecord + 更新保底/定轨 + 记消耗
5. 一次性提交

任何一步抛异常都会整体回滚。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadPullCount,
    EpitomizedDisabled,
    InsufficientCurrency,
    NotAFeaturedWeapon,
    PoolClosed,
    PoolNotFound,
    PullCapExceeded,
)
from app.db.base import ensure_utc, utcnow
from app.models import (
    PRIMOGEMS_PER_FATE,
    CurrencyType,
    Pool,
    PoolItem,
    PullRecord,
    User,
    UserCurrency,
    UserEpitomizedPath,
    UserPity,
)
from app.schemas.pull import EpitomizedOut, PityOut, PullItemOut, PullResultOut
from app.services.gacha_engine import (
    EpitomizedState,
    GachaEngine,
    PityState,
    apply_banner_end_reset,
)
from app.services.pool_rules import PoolRules
from app.services.rng import RandomSource, SystemRandomSource

#: 允许的抽数白名单。不设上限的话 count=100000 就是一次写放大 DoS。
ALLOWED_COUNTS = (1, 10)


# ---------------------------------------------------------------------------
# 卡池开放判定
# ---------------------------------------------------------------------------
def pool_is_open(pool: Pool, now=None) -> bool:
    """卡池当前是否可抽。

    需要同时满足：管理员启用 + 标记为当前开放 + 处于开放时间窗内。
    """
    if not pool.is_active or not pool.is_current:
        return False
    now = now or utcnow()
    open_at = ensure_utc(pool.open_at)
    close_at = ensure_utc(pool.close_at)
    if open_at is not None and now < open_at:
        return False
    return not (close_at is not None and now >= close_at)


async def get_pool(db: AsyncSession, pool_id: int) -> Pool:
    pool = await db.get(Pool, pool_id)
    if pool is None:
        raise PoolNotFound
    return pool


async def list_open_pools(db: AsyncSession) -> list[Pool]:
    """用户端可见的卡池：启用 + 当前开放 + 在时间窗内。"""
    result = await db.execute(select(Pool).order_by(Pool.sort_order, Pool.id))
    now = utcnow()
    return [p for p in result.scalars().all() if pool_is_open(p, now)]


async def list_all_pools(db: AsyncSession) -> list[Pool]:
    result = await db.execute(select(Pool).order_by(Pool.sort_order, Pool.id))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 货币
# ---------------------------------------------------------------------------
def compute_cost(pool: Pool, count: int) -> int:
    """本批抽卡的道具消耗量。

    新手池的十连是 8 个相遇之缘（8 折），所以不能简单地用单价 × 数量。
    """
    if count == 10 and pool.cost_per_ten is not None:
        return pool.cost_per_ten
    return pool.cost_per_pull * count


def _debit(currency: UserCurrency, cost_currency: str, amount: int) -> None:
    """扣除货币。道具不足时自动按 160:1 用原石补足（与线上一致）。"""
    if cost_currency == CurrencyType.PRIMOGEMS:
        if currency.primogems < amount:
            raise InsufficientCurrency(f"原石不足，需要 {amount}，当前 {currency.primogems}")
        currency.primogems -= amount
        return

    field = {
        CurrencyType.INTERTWINED_FATE: "intertwined_fate",
        CurrencyType.ACQUAINT_FATE: "acquaint_fate",
    }.get(cost_currency)
    if field is None:  # pragma: no cover - 枚举已封闭
        raise InsufficientCurrency(f"未知的消耗货币类型: {cost_currency}")

    balance = getattr(currency, field)
    if balance >= amount:
        setattr(currency, field, balance - amount)
        return

    shortfall = amount - balance
    gems_needed = shortfall * PRIMOGEMS_PER_FATE
    if currency.primogems < gems_needed:
        label = CurrencyType(cost_currency).label
        raise InsufficientCurrency(
            f"{label}不足，需要 {amount}（缺 {shortfall} 个，"
            f"折算需 {gems_needed} 原石），当前 {balance} 个、{currency.primogems} 原石"
        )
    setattr(currency, field, 0)
    currency.primogems -= gems_needed


async def get_or_create_currency(db: AsyncSession, user_id: int) -> UserCurrency:
    currency = await db.get(UserCurrency, user_id)
    if currency is None:
        currency = UserCurrency(user_id=user_id)
        db.add(currency)
        await db.flush()
    return currency


# ---------------------------------------------------------------------------
# 保底状态
# ---------------------------------------------------------------------------
async def get_or_create_pity(db: AsyncSession, user_id: int, pity_group: str) -> UserPity:
    result = await db.execute(
        select(UserPity).where(UserPity.user_id == user_id, UserPity.pity_group == pity_group)
    )
    pity = result.scalar_one_or_none()
    if pity is None:
        pity = UserPity(user_id=user_id, pity_group=pity_group)
        db.add(pity)
        await db.flush()
    return pity


async def get_or_create_epitomized(
    db: AsyncSession, user_id: int, pool_id: int
) -> UserEpitomizedPath:
    result = await db.execute(
        select(UserEpitomizedPath).where(
            UserEpitomizedPath.user_id == user_id,
            UserEpitomizedPath.pool_id == pool_id,
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = UserEpitomizedPath(user_id=user_id, pool_id=pool_id, fate_points=0)
        db.add(state)
        await db.flush()
    return state


async def count_pulls_on_pool(db: AsyncSession, user_id: int, pool_id: int) -> int:
    """该用户在此卡池的累计抽数（新手池的 20 抽终身上限靠它）。

    用 COUNT(*) 实时统计而不是维护一个冗余计数器：永远不会漂移，
    也不需要担心"删了记录但计数器没减"。
    """
    result = await db.execute(
        select(func.count())
        .select_from(PullRecord)
        .where(PullRecord.user_id == user_id, PullRecord.pool_id == pool_id)
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# 抽卡
# ---------------------------------------------------------------------------
class GachaService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def pull(
        self,
        user: User,
        pool_id: int,
        count: int,
        *,
        rng: RandomSource | None = None,
    ) -> PullResultOut:
        if count not in ALLOWED_COUNTS:
            raise BadPullCount(f"只支持 {' 或 '.join(map(str, ALLOWED_COUNTS))} 连抽")

        now = utcnow()
        pool = await get_pool(self.db, pool_id)
        rules = PoolRules.from_pool(pool)

        if not pool_is_open(pool, now):
            raise PoolClosed(f"「{pool.name}」当前未开放")
        if count == 1 and not pool.allows_single_pull:
            raise BadPullCount(f"「{pool.name}」不支持单抽")
        if count == 10 and not pool.allows_ten_pull:
            raise BadPullCount(f"「{pool.name}」不支持十连")

        # ---- 终身抽数上限（新手池 20 抽）----
        already = await count_pulls_on_pool(self.db, user.id, pool.id)
        if pool.lifetime_pull_cap is not None:
            remaining = pool.lifetime_pull_cap - already
            if remaining <= 0:
                raise PullCapExceeded(f"「{pool.name}」已达抽取上限 {pool.lifetime_pull_cap} 抽")
            if count > remaining:
                raise PullCapExceeded(f"「{pool.name}」剩余可抽 {remaining} 抽，不足 {count} 抽")

        # ---- 先扣费：失败就不消耗随机数、不推进保底、不留下半份记录 ----
        cost = compute_cost(pool, count)
        currency = await get_or_create_currency(self.db, user.id)
        _debit(currency, pool.cost_currency, cost)

        # ---- 载入状态并做惰性卡池结束重置 ----
        pity_row = await get_or_create_pity(self.db, user.id, pool.pity_group)
        epi_row: UserEpitomizedPath | None = None
        if rules.epitomized_enabled:
            epi_row = await get_or_create_epitomized(self.db, user.id, pool.id)
        apply_banner_end_reset(epi_row, pool, now)  # type: ignore[arg-type]

        pity_state = PityState(
            pity_5=pity_row.pity_5,
            pity_4=pity_row.pity_4,
            guaranteed_5=pity_row.guaranteed_5,
            guaranteed_up_4=pity_row.guaranteed_up_4,
            radiance_streak=pity_row.radiance_streak,
        )
        epi_state = (
            EpitomizedState(
                charted_item_id=epi_row.charted_item_id, fate_points=epi_row.fate_points
            )
            if epi_row is not None
            else None
        )

        engine = GachaEngine(
            pool=pool,
            rules=rules,
            pity=pity_state,
            epitomized=epi_state,
            rng=rng or SystemRandomSource(),
            now=now,
        )

        batch_id = str(uuid.uuid4())
        outcomes = [engine.pull_once() for _ in range(count)]

        # ---- 持久化 ----
        # 消耗只记在批次的第一条记录上，其余记 0。
        # 原因是新手池的十连是 8 个相遇之缘（八折），摊到每抽是 0.8，无法用整数
        # 表示；而"每行都写批次总消耗"会让 SUM(cost_amount) 把十连的消耗重复计
        # 十次。记在首行既保证 SUM 等于真实总消耗，也不会有小数。
        for outcome in outcomes:
            item: PoolItem = outcome.item
            self.db.add(
                PullRecord(
                    user_id=user.id,
                    pool_id=pool.id,
                    pool_name_snapshot=pool.name,
                    pool_type_snapshot=pool.pool_type,
                    item_id=item.id,
                    item_name_snapshot=item.name,
                    item_type_snapshot=item.item_type,
                    item_image_snapshot=item.image_url,
                    star=outcome.star,
                    is_up=outcome.is_up,
                    is_pity=outcome.is_pity,
                    source=outcome.source,
                    pity_5_before=outcome.pity_5_before,
                    pity_4_before=outcome.pity_4_before,
                    rate_5_used=outcome.rate_5_used,
                    batch_id=batch_id,
                    pull_index_in_batch=outcome.index,
                    cost_currency=pool.cost_currency,
                    cost_amount=cost if outcome.index == 1 else 0,
                    created_at=now,
                )
            )

        pity_row.pity_5 = pity_state.pity_5
        pity_row.pity_4 = pity_state.pity_4
        pity_row.guaranteed_5 = pity_state.guaranteed_5
        pity_row.guaranteed_up_4 = pity_state.guaranteed_up_4
        pity_row.radiance_streak = pity_state.radiance_streak

        if epi_row is not None and epi_state is not None:
            epi_row.charted_item_id = epi_state.charted_item_id
            epi_row.fate_points = epi_state.fate_points

        await self.db.commit()

        return PullResultOut(
            batch_id=batch_id,
            pool_id=pool.id,
            pool_name=pool.name,
            pool_type=pool.pool_type,
            count=count,
            items=[self._to_item_out(o) for o in outcomes],
            cost_currency=pool.cost_currency,
            cost_amount=cost,
            currency=currency,
            pity=self._to_pity_out(pity_row, rules),
            epitomized=(
                self._to_epitomized_out(pool, rules, epi_row) if epi_row is not None else None
            ),
            has_five_star=any(o.star == 5 for o in outcomes),
        )

    # ------------------------------------------------------------------
    def _to_item_out(self, outcome) -> PullItemOut:
        item: PoolItem = outcome.item
        return PullItemOut(
            index=outcome.index,
            item_id=item.id,
            name=item.name,
            star=outcome.star,
            item_type=item.item_type,
            is_up=outcome.is_up,
            is_pity=outcome.is_pity,
            source=outcome.source,
            image_url=item.image_url,
            icon_url=item.icon_url,
        )

    def _to_pity_out(self, pity: UserPity, rules: PoolRules) -> PityOut:
        return PityOut(
            pity_5=pity.pity_5,
            pity_4=pity.pity_4,
            guaranteed_5=pity.guaranteed_5,
            until_hard_pity_5=max(0, rules.hard_pity_5 - pity.pity_5),
            # 仅用于展示，clamp 到 0；存储值本身允许超过 hard_pity_4
            until_pity_4=max(0, rules.hard_pity_4 - pity.pity_4),
            hard_pity_5=rules.hard_pity_5,
            hard_pity_4=rules.hard_pity_4,
        )

    def _to_epitomized_out(
        self, pool: Pool, rules: PoolRules, state: UserEpitomizedPath
    ) -> EpitomizedOut:
        options = [
            {"id": i.id, "name": i.name, "image_url": i.image_url}
            for i in pool.up_items(5)
            if i.item_type == "weapon"
        ]
        charted_name = next((o["name"] for o in options if o["id"] == state.charted_item_id), None)
        return EpitomizedOut(
            enabled=rules.epitomized_enabled,
            charted_item_id=state.charted_item_id,
            charted_item_name=charted_name,
            fate_points=state.fate_points,
            max_points=rules.epitomized_max_points,
            options=options,
        )

    # ------------------------------------------------------------------
    async def set_epitomized_path(
        self, user: User, pool_id: int, weapon_item_id: int | None
    ) -> EpitomizedOut:
        """定轨：选武器、换武器、取消。

        三种情况的命定值处理不同，且都是官方规则：
        - 取消（传 None）→ 归零
        - **换成另一把** → 归零
        - **重选同一把** → 保持不变（否则 UI 上双击确认就会清掉已攒的命定值）
        """
        pool = await get_pool(self.db, pool_id)
        rules = PoolRules.from_pool(pool)
        if not rules.epitomized_enabled:
            raise EpitomizedDisabled(f"「{pool.name}」未开启定轨")

        state = await get_or_create_epitomized(self.db, user.id, pool.id)
        apply_banner_end_reset(state, pool, utcnow())

        if weapon_item_id is None:
            state.charted_item_id = None
            state.fate_points = 0
        else:
            valid = {i.id for i in pool.up_items(5) if i.item_type == "weapon"}
            if weapon_item_id not in valid:
                raise NotAFeaturedWeapon("只能定轨当前卡池的 UP 五星武器")
            if state.charted_item_id != weapon_item_id:
                state.charted_item_id = weapon_item_id
                state.fate_points = 0
            # 重选同一把：什么都不做，保留已积累的命定值

        await self.db.commit()
        return self._to_epitomized_out(pool, rules, state)

    async def get_epitomized(self, user: User, pool: Pool) -> EpitomizedOut | None:
        rules = PoolRules.from_pool(pool)
        if not rules.epitomized_enabled:
            return None
        state = await get_or_create_epitomized(self.db, user.id, pool.id)
        if apply_banner_end_reset(state, pool, utcnow()):
            await self.db.commit()
        return self._to_epitomized_out(pool, rules, state)
