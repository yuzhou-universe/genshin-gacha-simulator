"""管理员后台测试：权限边界、卡池 CRUD、规则校验、操作日志。

权限部分刻意覆盖 **HTML 页面路径**，不只是 API —— 页面路径上的错误更容易写错，
而且一旦写错，"普通用户访问后台"这类越权在监控上会显示成成功。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.exceptions import PoolConfigError
from app.models import AdminLog, Pool, PoolType, UserRole
from app.services import auth_service
from app.services.pool_rules import PoolRules, validate_pool

# 管理端页面路径（浏览器访问）
ADMIN_PAGES = ["/admin", "/admin/pools", "/admin/assets", "/admin/settings", "/admin/logs"]
# 管理端 API 路径
ADMIN_APIS = ["/admin/api/pools", "/admin/stats", "/admin/api/settings", "/admin/api/assets"]


@pytest.fixture
async def admin_user(seeded_db):
    user, _ = await auth_service.ensure_admin(seeded_db)
    return user


@pytest.fixture
async def regular_user(seeded_db):
    return await auth_service.register(seeded_db, "regular", "Regular123")


async def login(client, username: str, password: str):
    return await client.post("/auth/login", json={"username": username, "password": password})


# ===========================================================================
# 权限边界
# ===========================================================================
@pytest.mark.parametrize("path", ADMIN_PAGES)
async def test_admin_pages_redirect_anonymous(client, path):
    resp = await client.get(path, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("path", ADMIN_PAGES)
async def test_admin_pages_forbidden_for_regular_user(client, seeded_db, regular_user, path):
    """★ 回归用例：普通用户访问后台页面必须 403。

    这里曾经同时踩了两个坑：
    1. 依赖清理会 rollback 并关闭会话，模板再读 ORM 用户的属性会抛
       DetachedInstanceError → 500；
    2. 错误页渲染时忘了传 status_code，TemplateResponse 默认 200 →
       越权访问在监控上看起来是成功的。
    """
    await login(client, "regular", "Regular123")
    resp = await client.get(path, follow_redirects=False)

    assert resp.status_code == 403, f"{path} 应返回 403，实际 {resp.status_code}"
    assert resp.status_code != 200, "越权绝不能以 200 返回"


@pytest.mark.parametrize("path", ADMIN_APIS)
async def test_admin_apis_forbidden_for_regular_user(client, seeded_db, regular_user, path):
    await login(client, "regular", "Regular123")
    resp = await client.get(path)

    assert resp.status_code == 403


@pytest.mark.parametrize("path", ADMIN_APIS)
async def test_admin_apis_unauthorized_for_anonymous(client, seeded_db, path):
    resp = await client.get(path)
    assert resp.status_code == 401


async def test_regular_user_cannot_write(client, seeded_db, regular_user):
    """普通用户的写操作必须全部 403，且不产生任何副作用。"""
    await login(client, "regular", "Regular123")
    writes = [
        ("post", "/admin/pools", {"json": {"key": "evil", "name": "E", "pool_type": "standard"}}),
        ("put", "/admin/pools/1", {"json": {"name": "Hacked"}}),
        ("delete", "/admin/pools/1", {}),
        ("post", "/admin/pools/1/set-current", {}),
        ("post", "/admin/pools/1/set-main", {}),
        ("put", "/admin/api/settings", {"json": {"initial_primogems": 999999}}),
        ("delete", "/admin/items/1", {}),
    ]
    for method, path, kwargs in writes:
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 403, f"{method.upper()} {path} 应 403，实际 {resp.status_code}"


async def test_regular_user_write_had_no_effect(client, seeded_db, regular_user):
    await login(client, "regular", "Regular123")
    await client.put("/admin/pools/1", json={"name": "Hacked"})

    pool = await seeded_db.get(Pool, 1)
    await seeded_db.refresh(pool)
    assert pool.name != "Hacked"


# ===========================================================================
# 管理员正常操作
# ===========================================================================
async def test_admin_can_reach_all_pages(client, seeded_db, admin_user):
    await login(client, "admin", "Admin12345")
    for path in ADMIN_PAGES:
        assert (await client.get(path)).status_code == 200, path


async def test_global_stats(client, seeded_db, admin_user, regular_user):
    await login(client, "admin", "Admin12345")
    data = (await client.get("/admin/stats")).json()

    assert data["total_users"] >= 2  # 管理员 + 普通用户
    assert data["admin_count"] >= 1
    assert data["total_pulls"] == 0
    assert data["five_star_rate"] == 0.0


# ===========================================================================
# 卡池创建
# ===========================================================================
@pytest.mark.parametrize(
    ("pool_type", "expect_5050", "expect_hard_pity", "expect_epitomized"),
    [
        ("character", True, 90, False),
        ("weapon", True, 80, True),
        ("standard", False, 90, False),
        ("beginner", False, 90, False),
    ],
)
async def test_new_pool_gets_type_specific_defaults(
    client, seeded_db, admin_user, pool_type, expect_5050, expect_hard_pity, expect_epitomized
):
    """★ 新建卡池必须套用该类型的出厂预设，而不是数据库的通用列默认值。

    否则新建一个常驻池会拿到 has_5050=True（列默认），
    变成"启用了 UP 规则却没有 UP 五星"，卡池根本存不进去。
    """
    await login(client, "admin", "Admin12345")
    resp = await client.post(
        "/admin/pools",
        json={"key": f"new-{pool_type}", "name": f"新{pool_type}", "pool_type": pool_type},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_5050"] is expect_5050
    assert data["hard_pity_5"] == expect_hard_pity
    assert data["epitomized_enabled"] is expect_epitomized


async def test_new_pool_is_created_disabled(client, seeded_db, admin_user):
    """新建的卡池强制停用 —— 物品还没配的池子一旦开放，玩家一抽就报错。"""
    await login(client, "admin", "Admin12345")
    data = (
        await client.post(
            "/admin/pools",
            json={"key": "fresh", "name": "Fresh", "pool_type": "standard"},
        )
    ).json()

    assert data["is_active"] is False
    assert data["is_current"] is False
    assert data["is_main"] is False


async def test_duplicate_key_rejected(client, seeded_db, admin_user):
    await login(client, "admin", "Admin12345")
    body = {"key": "dup", "name": "A", "pool_type": "standard"}
    assert (await client.post("/admin/pools", json=body)).status_code == 200
    resp = await client.post("/admin/pools", json={**body, "name": "B"})

    assert resp.status_code == 400
    assert "已被占用" in resp.json()["detail"]


async def test_missing_required_fields_rejected(client, seeded_db, admin_user):
    await login(client, "admin", "Admin12345")
    resp = await client.post("/admin/pools", json={"key": "nokey", "pool_type": "standard"})

    assert resp.status_code == 400


async def test_cannot_open_pool_without_items(client, seeded_db, admin_user):
    """★ 物品没配全的池子不允许开放。

    否则会出现"玩家看得见、点下去报错"的卡池 —— 比干脆不开放糟糕得多。
    """
    await login(client, "admin", "Admin12345")
    pool_id = (
        await client.post(
            "/admin/pools",
            json={"key": "empty", "name": "Empty", "pool_type": "standard"},
        )
    ).json()["id"]

    resp = await client.post(f"/admin/pools/{pool_id}/set-current")

    assert resp.status_code == 400
    assert "物品" in resp.json()["detail"]


# ===========================================================================
# 卡池编辑
# ===========================================================================
async def test_update_pool_rules_takes_effect_immediately(client, seeded_db, admin_user):
    """规则改完立即生效：引擎每次抽卡都从库里读，没有模块级缓存。"""
    await login(client, "admin", "Admin12345")
    # 软保底起点必须一并下移：起点晚于硬保底会被校验拒绝（那是正确行为）
    resp = await client.put(
        "/admin/pools/1",
        json={"base_rate_5": 0.012, "hard_pity_5": 50, "soft_pity_start": 40},
    )
    assert resp.status_code == 200, resp.text

    await client.post("/auth/register", json={"username": "checker", "password": "Checker123"})
    pools = (await client.get("/api/pools")).json()

    char_pool = next(p for p in pools if p["pool_type"] == "character")
    assert char_pool["base_rate_5"] == 0.012
    assert char_pool["hard_pity_5"] == 50


async def test_cannot_change_key_or_type(client, seeded_db, admin_user):
    """key 与 type 不允许改：前者是外部引用标识，后者会改变 UP/歪池的校验语义。"""
    await login(client, "admin", "Admin12345")
    assert (await client.put("/admin/pools/1", json={"key": "renamed"})).status_code == 400
    assert (await client.put("/admin/pools/1", json={"pool_type": "weapon"})).status_code == 400


async def test_invalid_rule_combination_rejected(client, seeded_db, admin_user):
    """软保底起点晚于硬保底是静默失效的配置，必须在保存时就拒绝。"""
    await login(client, "admin", "Admin12345")
    resp = await client.put("/admin/pools/1", json={"soft_pity_start": 500})

    assert resp.status_code == 400
    assert "软保底" in resp.json()["detail"]


async def test_item_updates_preserve_ids(client, seeded_db, admin_user):
    """★ 物品编辑必须原地更新，不能整表重建。

    整表重建会让每条物品换新 id，而 UserEpitomizedPath.charted_item_id 是
    ON DELETE SET NULL —— 管理员只改了个描述，玩家的定轨选择就被清空了。
    """
    await login(client, "admin", "Admin12345")
    before = (await client.get("/admin/api/pools")).json()[0]["items"]
    ids_before = [i["id"] for i in before]

    # 改一个、删一个、加一个
    edited = [dict(i) for i in before]
    edited[0]["name"] = "改过的名字"
    edited = edited[:-1]
    edited.append({"name": "新增物品", "item_type": "weapon", "star": 3})

    resp = await client.put("/admin/pools/1", json={"items": edited})
    assert resp.status_code == 200, resp.text
    after = resp.json()["items"]

    kept = {i["id"] for i in after} & set(ids_before)
    assert len(kept) == len(ids_before) - 1, "除被删的那件外，其余 id 都应保持不变"
    assert after[0]["name"] == "改过的名字"


async def test_epitomized_course_survives_pool_edit(client, seeded_db):
    """端到端验证：管理员编辑卡池后，玩家的定轨选择还在。"""
    from app.services.gacha_service import get_or_create_epitomized

    user, _ = await auth_service.ensure_admin(seeded_db)
    weapon = (
        (await seeded_db.execute(select(Pool).where(Pool.pool_type == PoolType.WEAPON)))
        .scalars()
        .first()
    )
    target = next(i for i in weapon.items if i.star == 5 and i.is_up)

    await login(client, "admin", "Admin12345")
    # 玩家先定轨
    await client.post("/auth/login", json={"username": "admin", "password": "Admin12345"})
    await client.post("/api/pull/epitomized", json={"pool_id": weapon.id, "item_id": target.id})

    # 管理员编辑卡池（只改描述）
    items = (await client.get("/admin/api/pools")).json()
    weapon_admin = next(p for p in items if p["pool_type"] == "weapon")
    await client.put(
        f"/admin/pools/{weapon.id}",
        json={"description": "改了描述", "items": weapon_admin["items"]},
    )

    state = await get_or_create_epitomized(seeded_db, user.id, weapon.id)
    await seeded_db.refresh(state)
    assert state.charted_item_id == target.id, "编辑卡池不应清空玩家的定轨选择"


async def test_delete_pool_keeps_history(client, seeded_db, admin_user):
    """删池不毁历史：记录里有快照字段。"""
    await login(client, "admin", "Admin12345")
    pool_id = (
        await client.post(
            "/admin/pools", json={"key": "temp", "name": "T", "pool_type": "standard"}
        )
    ).json()["id"]
    resp = await client.delete(f"/admin/pools/{pool_id}")

    assert resp.status_code == 200
    assert "existing_records" in resp.json()


# ===========================================================================
# 操作日志
# ===========================================================================
async def test_actions_are_logged(client, seeded_db, admin_user):
    await login(client, "admin", "Admin12345")
    await client.put("/admin/pools/1", json={"hard_pity_5": 77})
    await client.post("/admin/pools/1/set-current")
    await client.put("/admin/api/settings", json={"initial_primogems": 33333})

    logs = (await client.get("/admin/logs", headers={"X-Requested-With": "XMLHttpRequest"})).json()

    actions = {entry["action"] for entry in logs["items"]}
    assert "pool.update" in actions
    assert "settings.update" in actions
    assert logs["total"] > 0


async def test_log_records_only_changed_fields(client, seeded_db, admin_user):
    """日志只记变化的字段，否则改一个价格会打印 37 个字段，根本看不出改了什么。"""
    await login(client, "admin", "Admin12345")
    resp = await client.put("/admin/pools/1", json={"base_rate_4": 0.077})
    assert resp.status_code == 200, resp.text
    logs = (await client.get("/admin/logs", headers={"X-Requested-With": "XMLHttpRequest"})).json()

    update = next(e for e in logs["items"] if e["action"] == "pool.update")
    detail = update["detail"].replace("\n", " ")
    assert "base_rate_4" in detail
    assert '"from": 0.051, "to": 0.077' in detail
    # 没改的字段不应出现
    assert "hard_pity_5" not in detail
    assert "base_rate_5" not in detail


async def test_log_page_returns_html_for_browser(client, seeded_db, admin_user):
    """同一路径：浏览器拿页面，XHR 拿 JSON。"""
    await login(client, "admin", "Admin12345")
    as_html = await client.get("/admin/logs", headers={"Accept": "text/html"})
    as_json = await client.get("/admin/logs", headers={"X-Requested-With": "XMLHttpRequest"})

    assert "text/html" in as_html.headers["content-type"]
    assert as_json.headers["content-type"].startswith("application/json")


# ===========================================================================
# 卡池配置校验（纯函数层）
# ===========================================================================
class _Item:
    """校验用的最小物品替身。"""

    def __init__(
        self,
        star: int,
        *,
        is_up: bool = False,
        item_type: str = "character",
        weight: float = 1.0,
        name: str = "",
        is_active: bool = True,
    ) -> None:
        self.star = star
        self.is_up = is_up
        self.item_type = item_type
        self.weight = weight
        self.name = name or f"{star}star-{item_type}"
        self.is_active = is_active


def _make_pool(
    rules: PoolRules,
    items: list,
    *,
    pool_type: str = PoolType.CHARACTER,
    cost_per_pull: int = 1,
    cost_per_ten: int | None = 10,
):
    """构造一个只带校验所需字段的卡池替身。"""

    class _Pool:
        open_at = None
        close_at = None
        lifetime_pull_cap = None
        allows_single_pull = True
        allows_ten_pull = True

    _Pool.pool_type = pool_type
    _Pool.cost_per_pull = cost_per_pull
    _Pool.cost_per_ten = cost_per_ten
    _Pool.items = items
    for key, value in rules.as_dict().items():
        setattr(_Pool, key, value)
    return _Pool


def _valid_character_items() -> list:
    return [
        _Item(5, is_up=True, item_type="character", name="UP角色"),
        _Item(5, item_type="character", name="歪池角色"),
        _Item(4, item_type="character", name="四星"),
        _Item(3, item_type="weapon", name="三星武器"),
    ]


async def test_seed_pools_pass_their_own_validation(seeded_db):
    """★ 默认卡池必须能通过自己的校验规则。

    这是最容易忽略的一致性检查：改了校验逻辑却忘了同步种子数据，
    结果是"全新安装的用户连默认卡池都存不进去"。
    这里走真实入库路径，因此也顺带覆盖了"列默认值是否补齐"。
    """
    pools = (await seeded_db.execute(select(Pool))).scalars().all()
    assert len(pools) == 4

    for pool in pools:
        validate_pool(pool)  # 不抛异常即为通过


def test_ten_pull_at_par_with_single_is_allowed():
    """十连价格等于单抽累加价（不打折）是合法的 —— 线上角色池就是这样。"""
    validate_pool(_make_pool(PoolRules.character(), _valid_character_items()))


def test_ten_pull_more_expensive_than_single_is_rejected():
    """十连比单抽累加还贵才是标价错误。"""
    pool = _make_pool(PoolRules.character(), _valid_character_items(), cost_per_ten=15)
    with pytest.raises(PoolConfigError, match="十连价格"):
        validate_pool(pool)


def test_character_pool_rejects_weapon_in_offbanner():
    """角色池的歪池混入武器必须被拒 —— 否则歪了会掉武器。"""
    items = _valid_character_items()
    items[1] = _Item(5, item_type="weapon", name="歪池武器")
    pool = _make_pool(PoolRules.character(), items)

    with pytest.raises(PoolConfigError, match="物品类型"):
        validate_pool(pool)


def test_weapon_pool_rejects_character_in_offbanner():
    """反向也要拦：武器池的歪池混入角色。"""
    items = [
        _Item(5, is_up=True, item_type="weapon", name="UP武器A"),
        _Item(5, is_up=True, item_type="weapon", name="UP武器B"),
        _Item(5, item_type="character", name="歪池角色"),
        _Item(4, item_type="weapon", name="四星武器"),
        _Item(3, item_type="weapon", name="三星武器"),
    ]
    pool = _make_pool(PoolRules.weapon(), items, pool_type=PoolType.WEAPON)

    with pytest.raises(PoolConfigError, match="物品类型"):
        validate_pool(pool)


def test_epitomized_requires_exactly_two_up_weapons():
    """定轨的"各 37.5%"只在恰好两把 UP 武器时成立。"""
    items = [
        _Item(5, is_up=True, item_type="weapon", name="UP武器A"),
        _Item(5, item_type="weapon", name="歪池武器"),
        _Item(4, item_type="weapon", name="四星武器"),
        _Item(3, item_type="weapon", name="三星武器"),
    ]
    pool = _make_pool(PoolRules.weapon(), items, pool_type=PoolType.WEAPON)

    with pytest.raises(PoolConfigError, match="恰好有 2 把"):
        validate_pool(pool)


def test_pool_without_offbanner_is_rejected():
    """启用 UP 却没有歪池：歪了之后引擎无处可掉。"""
    items = [
        _Item(5, is_up=True, item_type="character", name="UP角色"),
        _Item(4, item_type="character", name="四星"),
        _Item(3, item_type="weapon", name="三星武器"),
    ]
    pool = _make_pool(PoolRules.character(), items)

    with pytest.raises(PoolConfigError, match="非 UP 五星"):
        validate_pool(pool)


def test_pool_without_three_star_is_rejected():
    """没有三星物品：未中四/五星时无处可掉。"""
    items = [
        _Item(5, is_up=True, item_type="character", name="UP角色"),
        _Item(5, item_type="character", name="歪池角色"),
        _Item(4, item_type="character", name="四星"),
    ]
    pool = _make_pool(PoolRules.character(), items)

    with pytest.raises(PoolConfigError, match="三星"):
        validate_pool(pool)


def test_all_zero_weights_rejected():
    """权重全零会导致除零。"""
    items = [
        _Item(i.star, is_up=i.is_up, item_type=i.item_type, weight=0, name=i.name)
        for i in _valid_character_items()
    ]
    pool = _make_pool(PoolRules.character(), items)

    with pytest.raises(PoolConfigError, match="权重之和"):
        validate_pool(pool)


def test_soft_pity_start_after_hard_pity_rejected():
    """静默失效的配置：软保底起点晚于硬保底，爬升永远不会发生。"""
    rules = PoolRules.character(soft_pity_start=95, hard_pity_5=90)
    with pytest.raises(PoolConfigError, match="软保底"):
        rules.validate()


def test_validate_pool_accepts_explicit_items_list():
    """validate_pool 支持传入调用方自建的物品列表。

    新建卡池时必须走这条路：那个对象还没进库，读 pool.items 会触发惰性加载，
    在异步上下文里会抛 MissingGreenlet（表现为 500）。
    """

    class _ExplodingItems:
        def __iter__(self):
            raise AssertionError("不应读取 pool.items")

    pool = _make_pool(PoolRules.character(), _valid_character_items())
    pool.items = _ExplodingItems()

    # 显式传 items 时不应去碰 pool.items
    validate_pool(pool, items=_valid_character_items())

    with pytest.raises(PoolConfigError, match="至少要有一个"):
        validate_pool(pool, items=[])
    # require_items=False 时允许空物品列表（新建卡池的路径）
    validate_pool(pool, items=[], require_items=False)


# ===========================================================================
# 管理员账号引导
# ===========================================================================
async def test_ensure_admin_is_idempotent(seeded_db):
    first, changed = await auth_service.ensure_admin(seeded_db)
    assert changed is False
    second, changed2 = await auth_service.ensure_admin(seeded_db)
    assert changed2 is False
    assert first.id == second.id


async def test_ensure_admin_overrides_password_from_env(seeded_db, monkeypatch):
    """需求要求管理员密码必须可通过环境变量覆盖。"""
    from app.core.config import settings

    admin, _ = await auth_service.ensure_admin(seeded_db)

    monkeypatch.setattr(settings, "admin_password", "NewAdminPass99")
    same_admin, changed = await auth_service.ensure_admin(seeded_db)

    assert changed is True
    assert same_admin.id == admin.id
    # 新密码可登录
    assert (await auth_service.authenticate(seeded_db, "admin", "NewAdminPass99")).id == admin.id


async def test_ensure_admin_promotes_existing_user(seeded_db, monkeypatch):
    """环境变量指定的用户名已被普通用户占用时，提升为管理员。"""
    from app.core.config import settings

    await auth_service.register(seeded_db, "wannabe", "Wannabe123")
    monkeypatch.setattr(settings, "admin_username", "wannabe")

    admin, _ = await auth_service.ensure_admin(seeded_db)

    assert admin.role == UserRole.ADMIN


async def test_admin_log_snapshots_username(seeded_db):
    """日志存用户名的快照，账号被删后日志依然可读。"""
    from app.services import admin_log_service

    admin, _ = await auth_service.ensure_admin(seeded_db)
    await admin_log_service.log_action(
        seeded_db, admin=admin, action="test.action", detail={"x": 1}
    )

    entry = (await seeded_db.execute(select(AdminLog))).scalars().first()
    assert entry is not None
    assert entry.admin_username == admin.username
