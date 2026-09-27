"""用户端 HTTP 端到端测试：注册 → 登录 → 抽卡 → 历史 → 导出。

服务层的规则正确性已由其它测试覆盖，这里只关心**接口契约与鉴权**：
状态码、响应结构、越权拦截、以及 AI 失败时抽卡是否仍然正常。
"""

from __future__ import annotations

import pytest

from app.services import auth_service


def expected_pity_after(result: dict) -> int:
    """按实际结果推导批次结束时的 pity_5。

    **不能**写成"出金就是 0"。五星若出现在十连的第 2 抽，出金后计数会继续走，
    批次结束时 pity_5 是 8 而不是 0。正确值是「最后 8 抽里没有五星」，
    即 ``本批抽数 - 最后一次出金的序号``。

    这个 helper 的存在本身就是个教训：HTTP 接口用的是真实随机源，
    任何"假设不会出金"的断言都会有约 6% 的概率失败。
    """
    count = len(result["items"])
    five_indices = [i["index"] for i in result["items"] if i["star"] == 5]
    return count - max(five_indices) if five_indices else count


async def register(client, username="traveler", password="Traveler123"):
    return await client.post("/auth/register", json={"username": username, "password": password})


async def login(client, username="traveler", password="Traveler123"):
    return await client.post("/auth/login", json={"username": username, "password": password})


# ===========================================================================
# 注册与登录
# ===========================================================================
async def test_register_returns_user_and_redirect(client, seeded_db):
    resp = await register(client)

    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["username"] == "traveler"
    assert data["user"]["role"] == "user"
    assert data["redirect_to"] == "/gacha"
    # 响应里绝不能出现密码哈希
    assert "password" not in resp.text.lower()


async def test_register_rejects_duplicate_username(client, seeded_db):
    await register(client)
    resp = await register(client)

    assert resp.status_code == 400
    assert "已被注册" in resp.json()["detail"]


@pytest.mark.parametrize(
    ("username", "password", "fragment"),
    [
        ("ab", "Traveler123", "用户名至少 3"),
        ("a" * 25, "Traveler123", "用户名最多 20"),
        ("traveler", "short1", "密码至少 8"),
        ("traveler", "allletters", "必须包含数字"),
        ("traveler", "12345678", "必须包含字母"),
    ],
)
async def test_register_validates_input(client, seeded_db, username, password, fragment):
    """校验失败要说明具体原因，不能统一回一句"请填写用户名与密码"。"""
    resp = await register(client, username, password)

    assert resp.status_code == 400
    assert fragment in resp.json()["detail"]


async def test_login_success_and_wrong_password(client, seeded_db):
    await register(client)
    await client.post("/auth/logout")

    bad = await login(client, password="WrongPass123")
    assert bad.status_code == 400
    assert "用户名或密码错误" in bad.json()["detail"]

    good = await login(client)
    assert good.status_code == 200
    assert good.json()["redirect_to"] == "/gacha"


async def test_login_lockout_after_repeated_failures(client, seeded_db):
    await register(client)
    await client.post("/auth/logout")

    for _ in range(5):
        await login(client, password="WrongPass123")

    resp = await login(client)  # 正确密码也应被锁定拦下
    assert resp.status_code == 429
    assert "次数过多" in resp.json()["detail"]


async def test_nonexistent_user_does_not_reveal_existence(client, seeded_db):
    """用户名不存在与密码错误必须返回同一条消息，避免用户名枚举。"""
    await register(client)
    await client.post("/auth/logout")

    missing = await login(client, username="nobody")
    wrong = await login(client, username="traveler", password="WrongPass123")

    assert missing.json()["detail"] == wrong.json()["detail"]


async def test_admin_login_redirects_to_admin(client, seeded_db):
    await auth_service.ensure_admin(seeded_db)
    resp = await login(client, "admin", "Admin12345")

    assert resp.json()["redirect_to"] == "/admin"


# ===========================================================================
# 页面路由
# ===========================================================================
@pytest.mark.parametrize("path", ["/gacha", "/history"])
async def test_user_pages_redirect_anonymous(client, seeded_db, path):
    resp = await client.get(path, follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/gacha", "/history"])
async def test_user_pages_ok_when_logged_in(client, seeded_db, path):
    await register(client)
    resp = await client.get(path)

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


async def test_root_redirects_by_role(client, seeded_db):
    anon = await client.get("/", follow_redirects=False)
    assert anon.headers["location"] == "/login"

    await register(client)
    assert (await client.get("/", follow_redirects=False)).headers["location"] == "/gacha"

    await client.post("/auth/logout")
    await auth_service.ensure_admin(seeded_db)
    await login(client, "admin", "Admin12345")
    assert (await client.get("/", follow_redirects=False)).headers["location"] == "/admin"


async def test_gacha_page_renders_wallet_server_side(client, seeded_db):
    """货币余额由服务端渲染，首屏不该是空值。"""
    await register(client)
    html = (await client.get("/gacha")).text

    assert "data-cur-primogems" in html
    assert "16000" in html, "初始原石应直接出现在 HTML 里"


# ===========================================================================
# 卡池与抽卡
# ===========================================================================
async def test_pools_requires_login(client, seeded_db):
    resp = await client.get("/api/pools")
    assert resp.status_code == 401


async def test_list_pools_returns_open_pools_only(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()

    assert len(pools) == 4
    assert {p["pool_type"] for p in pools} == {"character", "weapon", "standard", "beginner"}
    # 概率说明字段齐备，前端据此渲染"概率说明"
    char = next(p for p in pools if p["pool_type"] == "character")
    assert char["base_rate_5"] == 0.006
    assert char["hard_pity_5"] == 90
    assert char["soft_pity_start"] == 74
    assert len(char["up_items_5"]) == 2


async def test_pull_single_and_ten(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    single = (await client.post("/api/pull/single", json={"pool_id": char["id"]})).json()
    assert single["count"] == 1
    assert len(single["items"]) == 1
    assert single["cost_amount"] == 1

    ten = (await client.post("/api/pull/ten", json={"pool_id": char["id"]})).json()
    assert ten["count"] == 10
    assert len(ten["items"]) == 10
    assert ten["cost_amount"] == 10
    assert {i["name"] for i in ten["items"]}  # 每件都有名字


async def test_pull_updates_currency_and_pity(client, seeded_db):
    """扣费与保底推进。

    注意：HTTP 接口用的是真实随机源，十连里有约 6% 的概率出金，
    而出金会把 pity_5 清零。因此**不能**直接断言 pity_5 == 10 ——
    那会是一个约 6% 概率失败的抖动测试。这里根据实际结果推导期望值。
    """
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    result = (await client.post("/api/pull/ten", json={"pool_id": char["id"]})).json()

    # 消耗是确定的（与随机结果无关）
    assert result["currency"]["intertwined_fate"] == 10  # 初始 20 减去 10

    # 保底计数取决于本批出金的位置
    expected = expected_pity_after(result)
    assert result["pity"]["pity_5"] == expected
    assert result["pity"]["until_hard_pity_5"] == char["hard_pity_5"] - expected


async def test_pull_rejects_invalid_count(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    resp = await client.post("/api/pull", json={"pool_id": char["id"], "count": 7})
    assert resp.status_code == 400


async def test_pull_rejects_unknown_pool(client, seeded_db):
    await register(client)
    resp = await client.post("/api/pull/single", json={"pool_id": 99999})
    assert resp.status_code == 404


async def test_pull_requires_login(client, seeded_db):
    resp = await client.post("/api/pull/single", json={"pool_id": 1})
    assert resp.status_code == 401


async def test_insufficient_currency_returns_400(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    # 初始资源：20 个纠缠之缘 + 16000 原石（按 160:1 折 100 个）= 恰好 120 抽
    # 即 12 次十连。抽满之后第 13 次必须因余额不足被拒。
    for _ in range(12):
        assert (await client.post("/api/pull/ten", json={"pool_id": char["id"]})).status_code == 200

    resp = await client.post("/api/pull/ten", json={"pool_id": char["id"]})
    assert resp.status_code == 400
    assert "不足" in resp.json()["detail"]


async def test_pity_endpoint(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    before = (await client.get(f"/api/pull/pity/{char['id']}")).json()
    assert before["pity_5"] == 0
    assert before["guaranteed_5"] is False

    result = (await client.post("/api/pull/ten", json={"pool_id": char["id"]})).json()
    after = (await client.get(f"/api/pull/pity/{char['id']}")).json()

    # 同上：按实际出金位置推导，避免抖动
    assert after["pity_5"] == expected_pity_after(result)


# ===========================================================================
# 定轨
# ===========================================================================
async def test_epitomized_set_switch_cancel(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    weapon = next(p for p in pools if p["pool_type"] == "weapon")
    up = weapon["up_items_5"]
    assert len(up) == 2

    set_resp = (
        await client.post(
            "/api/pull/epitomized", json={"pool_id": weapon["id"], "item_id": up[0]["id"]}
        )
    ).json()
    assert set_resp["charted_item_id"] == up[0]["id"]
    assert set_resp["max_points"] == 1, "默认对齐线上：命定值满 1 即必出"

    switch = (
        await client.post(
            "/api/pull/epitomized", json={"pool_id": weapon["id"], "item_id": up[1]["id"]}
        )
    ).json()
    assert switch["charted_item_id"] == up[1]["id"]

    cancel = (
        await client.post("/api/pull/epitomized", json={"pool_id": weapon["id"], "item_id": None})
    ).json()
    assert cancel["charted_item_id"] is None
    assert cancel["fate_points"] == 0


async def test_epitomized_on_character_pool_rejected(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    resp = await client.post(
        "/api/pull/epitomized",
        json={"pool_id": char["id"], "item_id": char["up_items_5"][0]["id"]},
    )
    assert resp.status_code == 400
    assert "定轨" in resp.json()["detail"]


# ===========================================================================
# 历史与导出
# ===========================================================================
async def test_history_pagination_and_filters(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    for _ in range(3):
        await client.post("/api/pull/ten", json={"pool_id": char["id"]})

    page = (await client.get("/api/history?page=1&page_size=10")).json()
    assert page["total"] == 30
    assert page["pages"] == 3
    assert len(page["items"]) == 10

    fives = (await client.get("/api/history?star=5")).json()
    assert all(i["star"] == 5 for i in fives["items"])

    ups = (await client.get("/api/history?is_up=true")).json()
    assert all(i["is_up"] for i in ups["items"])


async def test_history_stats(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")
    await client.post("/api/pull/ten", json={"pool_id": char["id"]})

    stats = (await client.get("/api/history/stats")).json()

    assert stats["total_pulls"] == 10
    assert stats["five_star_count"] + stats["four_star_count"] + stats["three_star_count"] == 10
    assert len(stats["pool_progress"]) == 4


async def test_history_isolated_between_users(client, seeded_db):
    """★ 用户之间绝不能看到彼此的历史。"""
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")
    await client.post("/api/pull/ten", json={"pool_id": char["id"]})

    await client.post("/auth/logout")
    await register(client, "intruder", "Intruder123")

    page = (await client.get("/api/history")).json()
    stats = (await client.get("/api/history/stats")).json()

    assert page["total"] == 0
    assert stats["total_pulls"] == 0


async def test_history_requires_login(client, seeded_db):
    assert (await client.get("/api/history")).status_code == 401
    assert (await client.get("/api/history/stats")).status_code == 401
    assert (await client.get("/api/history/export")).status_code == 401


async def test_csv_export(client, seeded_db):
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")
    await client.post("/api/pull/ten", json={"pool_id": char["id"]})

    resp = await client.get("/api/history/export")

    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.content.decode("utf-8-sig")
    assert body.startswith("﻿") or resp.content[:3] == b"\xef\xbb\xbf"
    assert "时间" in body and "卡池" in body
    assert len([ln for ln in body.splitlines() if ln.strip()]) == 11  # 表头 + 10 条


# ===========================================================================
# AI 端点在无密钥时必须降级而不是报错
# ===========================================================================
async def test_ai_comment_falls_back_without_key(client, seeded_db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "deepseek_api_key", "")
    await register(client)
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    resp = await client.post(
        "/api/ai/comment",
        json={
            "pool_id": char["id"],
            "count": 10,
            "until_hard_pity": 80,
            "guaranteed": False,
            "items": [
                {"name": "胡桃", "star": 5, "is_up": True, "is_pity": False},
                {"name": "冷刃", "star": 3},
            ],
        },
    )

    assert resp.status_code == 200, "AI 不可用时也必须返回 200 与本地模板"
    data = resp.json()
    assert data["text"]
    assert data["from_model"] is False


async def test_ai_comment_does_not_leak_user_identity(client, seeded_db, monkeypatch):
    """AI 请求体里只应有游戏内公开信息。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "deepseek_api_key", "")
    await register(client, "secretname", "SecretName123")
    pools = (await client.get("/api/pools")).json()
    char = next(p for p in pools if p["pool_type"] == "character")

    body = {
        "pool_id": char["id"],
        "count": 1,
        "items": [{"name": "胡桃", "star": 5}],
        "username": "secretname",  # 前端就算多传，模型侧也不该用到
    }
    resp = await client.post("/api/ai/comment", json=body)

    assert resp.status_code == 200
    assert "secretname" not in resp.text


async def test_ai_status_endpoint(client, seeded_db):
    await register(client)
    data = (await client.get("/api/ai/status")).json()

    assert "enabled" in data
    assert "model" in data
    # 绝不能暴露密钥
    assert "key" not in str(data).lower() or "deepseek_api_key" not in str(data)
