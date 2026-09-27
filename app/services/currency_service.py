"""用户货币的读取与变更。

集中在一处：抽卡扣费、原石自助增加都走这里。货币是**唯一**会被并发修改的
用户资产（抽卡扣费、用户自己点"+"加原石），把增减逻辑放在同一个模块里，
才能保证上限判定、自动兑换这些规则不会被写两遍而写歪。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InsufficientCurrency
from app.models import PRIMOGEMS_PER_FATE, CurrencyType, UserCurrency


async def get_or_create_currency(db: AsyncSession, user_id: int) -> UserCurrency:
    currency = await db.get(UserCurrency, user_id)
    if currency is None:
        currency = UserCurrency(user_id=user_id)
        db.add(currency)
        await db.flush()
    return currency


def debit(currency: UserCurrency, cost_currency: str, amount: int) -> None:
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


# ---------------------------------------------------------------------------
# 原石自助增加
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TopUpOutcome:
    primogems: int
    #: 本次实际增加的数量。达到上限时会小于请求值。
    added: int
    requested: int
    cap: int

    @property
    def capped(self) -> bool:
        """本次是否被上限截断。"""
        return self.added < self.requested

    @property
    def at_cap(self) -> bool:
        """余额是否已经顶到上限。"""
        return self.primogems >= self.cap


async def topup_primogems(
    db: AsyncSession, user_id: int, amount: int, *, cap: int | None = None
) -> TopUpOutcome:
    """给用户增加原石，余额在 ``cap`` 处截断。

    **在达到上限时截断而不是报错**：如果直接拒绝，用户余额停在 499,999 时
    任何档位（最小 10）都会超出上限，就会永久卡死、再也加不了。
    截断后返回实际增加量，界面可以如实告知"本次只加了多少"。

    上限判定用**数据库层的条件更新**，而不是"先读出来判断再写回去"：
    后者在两次并发请求下会双双通过检查，把余额顶到上限之上。
    ``case()`` 而不是 ``func.min()`` 是因为后者在 PostgreSQL 上叫 ``LEAST``，
    跨库不可移植。
    """
    cap = settings.primogem_topup_cap if cap is None else cap
    amount = max(0, amount)

    currency = await get_or_create_currency(db, user_id)
    # 仅用于算出"本次实际加了多少"回报给界面。
    # 上限判定不依赖它，否则并发下会双双通过检查。
    before = currency.primogems

    target = UserCurrency.primogems + amount
    statement = (
        update(UserCurrency)
        .where(UserCurrency.user_id == user_id)
        .values(
            primogems=case(
                # 余额已在或超过上限（例如管理员调低了上限、或历史数据）：
                # **原样不动**。少了这一支，"截断到上限"会把 16000 直接调成 1000 ——
                # 加原石反而把原石扣走了。
                (UserCurrency.primogems >= cap, UserCurrency.primogems),
                (target > cap, cap),
                else_=target,
            )
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(statement)
    await db.commit()
    # 上面的 UPDATE 绕过了 ORM 的工作单元，session 里还是旧值，必须回读
    await db.refresh(currency)

    return TopUpOutcome(
        primogems=currency.primogems,
        added=currency.primogems - before,
        requested=amount,
        cap=cap,
    )
