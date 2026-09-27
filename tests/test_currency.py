"""原石自助增加的测试。

重点在两件事：**档位白名单**（客户端不能传任意数值）与**上限的原子性**
（并发请求不能把余额顶到上限之上）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services.currency_service import topup_primogems


async def register(client, username="traveler", password="Traveler123"):
    return await client.post("/auth/register", json={"username": username, "password": password})


# ===========================================================================
# 接口契约
# ===========================================================================
async def test_topup_options_endpoint_matches_settings(client, seeded_db):
    await register(client)
    data = (await client.get("/api/currency/topup-options")).json()

    assert data["options"] == settings.topup_options
    assert data["cap"] == settings.primogem_topup_cap
    assert data["options"] == [10, 160, 1600]


async def test_topup_requires_login(client, seeded_db):
    resp = await client.post("/api/currency/topup", json={"amount": 160})
    assert resp.status_code == 401


@pytest.mark.parametrize("amount", [10, 160, 1600])
async def test_topup_accepts_each_option(client, seeded_db, amount):
    await register(client)
    before = (await client.get("/api/pools")).json()  # 触发会话建立
    assert before is not None

    resp = await client.post("/api/currency/topup", json={"amount": amount})
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["added"] == amount
    assert data["requested"] == amount
    assert data["capped"] is False
    assert data["primogems"] == 16000 + amount


# ===========================================================================
# ★ 白名单：客户端不能传任意数值
# ===========================================================================
@pytest.mark.parametrize("amount", [1, 9, 11, 159, 161, 1000, 999999, 1000000])
async def test_topup_rejects_amounts_outside_whitelist(client, seeded_db, amount):
    """★ 只接受配置里的档位。

    如果允许客户端传任意数值，这个功能就等于"随便刷原石"，
    上限也就形同虚设（一次传 500000 即可）。
    """
    await register(client)
    resp = await client.post("/api/currency/topup", json={"amount": amount})

    assert resp.status_code == 422
    # 错误信息要是人话，而不是结构化数组
    detail = resp.json()["detail"]
    assert isinstance(detail, str), f"detail 应当是字符串，实际是 {type(detail).__name__}"
    assert "档位" in detail


@pytest.mark.parametrize("amount", [0, -10, -1600])
async def test_topup_rejects_non_positive(client, seeded_db, amount):
    await register(client)
    resp = await client.post("/api/currency/topup", json={"amount": amount})
    assert resp.status_code == 422


async def test_rejected_topup_does_not_change_balance(client, seeded_db):
    await register(client)
    await client.post("/api/currency/topup", json={"amount": 999999})

    data = (await client.post("/api/currency/topup", json={"amount": 10})).json()
    assert data["primogems"] == 16010, "被拒的请求不能改变余额"


# ===========================================================================
# ★ 上限
# ===========================================================================
async def test_topup_never_reduces_balance_when_already_above_cap(seeded_db, user):
    """★ 已经超过上限时，余额必须**原样不动**。

    "截断到上限"如果写成无条件 ``min(余额+增量, 上限)``，在余额已超上限时
    会把它调低 —— 初始 16000、上限 1000 的情况下，点一次"加原石"会把
    16000 变成 1000，加原石反而把原石扣走了。
    """
    outcome = await topup_primogems(seeded_db, user.id, 1600, cap=1000)

    assert outcome.primogems == 16000, "余额不应被调低"
    assert outcome.added == 0, "已经超过上限，本次加不进去"
    assert outcome.capped is True
    assert outcome.at_cap is True


async def test_topup_at_but_not_above_cap_is_noop(seeded_db, user):
    """刚好等于上限时也不动。"""
    from app.services.currency_service import get_or_create_currency

    currency = await get_or_create_currency(seeded_db, user.id)
    currency.primogems = 1000
    await seeded_db.commit()

    outcome = await topup_primogems(seeded_db, user.id, 1600, cap=1000)

    assert outcome.primogems == 1000
    assert outcome.added == 0
    assert outcome.at_cap is True


async def test_topup_partially_fills_up_to_cap(seeded_db, user):
    """离上限只差一点时，只加能装下的那部分。"""
    from app.services.currency_service import get_or_create_currency

    currency = await get_or_create_currency(seeded_db, user.id)
    currency.primogems = 500
    await seeded_db.commit()

    outcome = await topup_primogems(seeded_db, user.id, 1600, cap=1000)

    assert outcome.added == 500, "只应加能装下的 500"
    assert outcome.primogems == 1000
    assert outcome.capped is True
    assert outcome.at_cap is True


async def test_topup_when_already_at_cap_is_noop(seeded_db, user):
    from app.services.currency_service import get_or_create_currency

    currency = await get_or_create_currency(seeded_db, user.id)
    currency.primogems = 1000
    await seeded_db.commit()

    outcome = await topup_primogems(seeded_db, user.id, 1600, cap=1000)

    assert outcome.added == 0
    assert outcome.primogems == 1000
    assert outcome.at_cap is True
    assert outcome.capped is True


async def test_topup_below_cap_is_not_capped(seeded_db, user):
    from app.services.currency_service import get_or_create_currency

    currency = await get_or_create_currency(seeded_db, user.id)
    currency.primogems = 100
    await seeded_db.commit()

    outcome = await topup_primogems(seeded_db, user.id, 160, cap=1000)

    assert outcome.added == 160
    assert outcome.primogems == 260
    assert outcome.capped is False
    assert outcome.at_cap is False


async def test_real_cap_is_500000(client, seeded_db):
    """出厂上限就是需求里说的 500000。"""
    await register(client)
    assert (await client.get("/api/currency/topup-options")).json()["cap"] == 500_000


async def test_concurrent_topups_cannot_exceed_cap(client, seeded_db, monkeypatch):
    """★ 上限判定必须在数据库层完成。

    "先读出来判断再写回去"在并发下会双双通过检查，把余额顶到上限之上。
    这里并发发起多个请求，断言最终余额恰好等于上限。
    """
    monkeypatch.setattr(settings, "primogem_topup_cap", 20000)
    await register(client)

    # 初始 16000，只剩 4000 空间；并发 5 次 +1600（共 8000）必然超发
    results = await asyncio.gather(
        *(client.post("/api/currency/topup", json={"amount": 1600}) for _ in range(5)),
        return_exceptions=True,
    )

    codes = [r.status_code for r in results if hasattr(r, "status_code")]
    assert all(c == 200 for c in codes), codes

    final = (await client.get("/api/currency/topup-options")).json()
    assert final["cap"] == 20000

    # 再加一次拿到当前余额，确认没有超发
    after = (await client.post("/api/currency/topup", json={"amount": 10})).json()
    assert after["primogems"] == 20000, f"并发后余额 {after['primogems']} 不应超过上限 20000"


async def test_topup_then_pull_uses_the_new_balance(client, seeded_db):
    """加完原石能真的拿来抽卡。"""
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    await client.post("/api/currency/topup", json={"amount": 1600})

    # 初始 20 个纠缠之缘 + (16000+1600)/160 = 110 个 → 共 130 抽 = 13 次十连
    for _ in range(13):
        resp = await client.post("/api/pull/ten", json={"pool_id": char["id"]})
        assert resp.status_code == 200

    final = await client.post("/api/pull/ten", json={"pool_id": char["id"]})
    assert final.status_code == 400, "加的原石用尽后应重新变成余额不足"


# ===========================================================================
# 配置解析
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10,160,1600", [10, 160, 1600]),
        ("10, 160, 1600", [10, 160, 1600]),  # 容忍空格
        ("10,10,160", [10, 160]),  # 去重
        ("abc,160", [160]),  # 忽略非法项
        ("", [10, 160, 1600]),  # 落回出厂档位
        ("abc", [10, 160, 1600]),
        ("-5,160", [160]),  # 忽略非正数
    ],
)
def test_topup_options_parsing(monkeypatch, raw, expected):
    monkeypatch.setattr(settings, "primogem_topup_options", raw)
    assert settings.topup_options == expected
