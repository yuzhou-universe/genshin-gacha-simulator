"""管理员后台：卡池管理、素材管理、全局配置、操作日志。

**所有**路由与 API 都依赖 ``AdminUser``（``require_admin``），普通用户访问
一律 403、未登录一律 401。页面与 JSON 的分派由 ``wants_json`` 统一处理。

改动卡池规则会立即生效（引擎每次抽卡都从数据库读规则，没有模块级缓存），
因此管理员调完概率，玩家下一抽就按新规则结算。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from sqlalchemy import case, func, select

from app.core.config import settings
from app.core.deps import AdminUser, DbSession, get_client_ip
from app.core.exceptions import NotFound, PoolConfigError
from app.core.request_utils import wants_json
from app.core.templating import render
from app.db.base import utcnow
from app.models import Pool, PoolItem, PoolType, PullRecord, User, UserRole
from app.schemas.admin import (
    GlobalStatsOut,
    PoolAdminOut,
    PoolIn,
    PoolItemIn,
    SettingsIn,
    UploadOut,
)
from app.services import admin_log_service, setting_service, upload_service
from app.services.gacha_service import list_all_pools
from app.services.pool_rules import PoolRules, validate_pool
from app.services.prompts import DEFAULT_TEMPLATES

router = APIRouter(prefix="/admin", tags=["admin"])


def tally(condition) -> Any:
    """SUM(CASE WHEN cond THEN 1 ELSE 0 END)。

    用 CASE 而不是 func.iif：后者是 SQLite 专有函数，PostgreSQL 上不存在。
    """
    return func.sum(case((condition, 1), else_=0))


#: 各卡池类型的出厂规则预设。新建卡池时按类型套用，
#: 而不是用数据库的通用列默认值。
RULE_PRESETS = {
    PoolType.CHARACTER: PoolRules.character,
    PoolType.WEAPON: PoolRules.weapon,
    PoolType.STANDARD: PoolRules.standard,
    PoolType.BEGINNER: PoolRules.beginner,
}


# ===========================================================================
# 序列化
# ===========================================================================
def pool_to_admin_out(pool: Pool) -> PoolAdminOut:
    return PoolAdminOut(
        id=pool.id,
        key=pool.key,
        name=pool.name,
        pool_type=pool.pool_type,
        pity_group=pool.pity_group,
        description=pool.description,
        background_image_url=pool.background_image_url,
        banner_url=pool.banner_url,
        icon_url=pool.icon_url,
        animation_key=pool.animation_key,
        is_active=pool.is_active,
        is_current=pool.is_current,
        is_main=pool.is_main,
        sort_order=pool.sort_order,
        open_at=pool.open_at,
        close_at=pool.close_at,
        cost_currency=pool.cost_currency,
        cost_per_pull=pool.cost_per_pull,
        cost_per_ten=pool.cost_per_ten,
        lifetime_pull_cap=pool.lifetime_pull_cap,
        allows_single_pull=pool.allows_single_pull,
        allows_ten_pull=pool.allows_ten_pull,
        base_rate_5=pool.base_rate_5,
        base_rate_4=pool.base_rate_4,
        hard_pity_5=pool.hard_pity_5,
        hard_pity_4=pool.hard_pity_4,
        soft_pity_enabled=pool.soft_pity_enabled,
        soft_pity_start=pool.soft_pity_start,
        soft_pity_step=pool.soft_pity_step,
        up_rate_5=pool.up_rate_5,
        up_rate_4=pool.up_rate_4,
        has_5050=pool.has_5050,
        epitomized_enabled=pool.epitomized_enabled,
        epitomized_max_points=pool.epitomized_max_points,
        capturing_radiance_enabled=pool.capturing_radiance_enabled,
        updated_at=pool.updated_at,
        items=[
            {
                "id": i.id,
                "name": i.name,
                "item_type": i.item_type,
                "star": i.star,
                "is_up": i.is_up,
                "weight": i.weight,
                "image_url": i.image_url,
                "icon_url": i.icon_url,
                "is_active": i.is_active,
                "sort_order": i.sort_order,
            }
            for i in pool.items
        ],
    )


async def get_pool_or_404(db, pool_id: int) -> Pool:
    pool = await db.get(Pool, pool_id)
    if pool is None:
        raise NotFound("卡池不存在")
    return pool


def snapshot(pool: Pool) -> dict[str, Any]:
    """取卡池当前值的快照，用于计算操作日志的差异。"""
    data = pool_to_admin_out(pool).model_dump(exclude={"items", "updated_at"})
    data["item_count"] = len(pool.items)
    return data


# ===========================================================================
# 页面
# ===========================================================================
@router.get("")
async def dashboard(request: Request, db: DbSession, admin: AdminUser):
    return render(request, "admin/dashboard.html")


@router.get("/pools")
async def pools_page(request: Request, db: DbSession, admin: AdminUser):
    return render(request, "admin/pools.html")


@router.get("/pools/{pool_id}")
async def pool_edit_page(pool_id: int, request: Request, db: DbSession, admin: AdminUser):
    pool = await get_pool_or_404(db, pool_id)
    return render(
        request,
        "admin/pool_edit.html",
        {"pool": pool_to_admin_out(pool), "rules": PoolRules.from_pool(pool).as_dict()},
    )


@router.get("/assets")
async def assets_page(request: Request, db: DbSession, admin: AdminUser):
    return render(request, "admin/assets.html")


@router.get("/settings")
async def settings_page(request: Request, db: DbSession, admin: AdminUser):
    from app.services.ai_service import get_ai_service

    animation = await setting_service.get_animation_config(db)
    templates = await setting_service.get_dict(db, setting_service.KEY_PROMPT_TEMPLATES, {})
    initial = await setting_service.get_initial_currency(db)
    service = get_ai_service()

    return render(
        request,
        "admin/settings.html",
        {
            "animation": animation,
            "templates": templates,
            "default_templates": DEFAULT_TEMPLATES,
            "initial": {
                "primogems": initial[0],
                "intertwined_fate": initial[1],
                "acquaint_fate": initial[2],
            },
            "ai": {
                "enabled": settings.ai_enabled,
                "available": service.available,
                "model": settings.deepseek_model,
                "base_url": settings.deepseek_base_url,
                "last_error": service.last_error,
            },
        },
    )


@router.get("/logs")
async def logs_page(
    request: Request,
    db: DbSession,
    admin: AdminUser,
    action: str | None = None,
    admin_username: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """浏览器要页面，XHR 要数据 —— 同一路径按请求类型分派。"""
    if wants_json(request):
        return await logs_api(
            db,
            admin,
            action=action,
            admin_username=admin_username,
            page=page,
            page_size=page_size,
        )
    actions = await admin_log_service.list_actions(db)
    return render(request, "admin/logs.html", {"actions": actions})


# ===========================================================================
# 卡池 API
# ===========================================================================
@router.get("/api/pools")
async def list_pools_api(db: DbSession, admin: AdminUser) -> list[PoolAdminOut]:
    pools = await list_all_pools(db)
    return [pool_to_admin_out(p) for p in pools]


@router.post("/pools")
async def create_pool(
    body: PoolIn, request: Request, db: DbSession, admin: AdminUser
) -> PoolAdminOut:
    if not body.key or not body.name or not body.pool_type:
        raise PoolConfigError("新建卡池必须提供标识（key）、名称与卡池类型")

    exists = await db.execute(select(Pool).where(Pool.key == body.key))
    if exists.scalar_one_or_none() is not None:
        raise PoolConfigError(f"标识「{body.key}」已被占用")

    payload = body.model_dump(exclude={"items"}, exclude_none=True)
    # 保底组默认跟随卡池类型；但调用方显式给的值优先
    payload.setdefault("pity_group", body.pool_type)

    # 套用该卡池类型的出厂规则预设，而不是数据库的通用列默认值。
    # 否则新建一个常驻池会拿到 has_5050=True（列默认），
    # 于是"启用了 UP 规则却没有 UP 五星"，卡池根本存不进去。
    # 调用方显式传了的字段优先。
    for field, value in RULE_PRESETS[body.pool_type]().as_dict().items():
        payload.setdefault(field, value)

    # 新建的卡池强制以停用状态创建：管理员通常是先建池、再配物品，
    # 而物品还没配全的池子一旦是开放的，玩家抽下去就会报错。
    payload["is_active"] = False
    payload["is_current"] = False
    payload["is_main"] = False

    pool = Pool(**payload)
    # 保留一份本地引用：校验时直接用它，不去读 pool.items —— 那个集合此刻
    # 还没加载，读它会触发惰性加载并在异步上下文里抛 MissingGreenlet。
    built_items = [PoolItem(**item.model_dump()) for item in (body.items or [])]
    pool.items = built_items

    db.add(pool)
    # 必须先 flush 再校验：SQLAlchemy 的列默认值要到 INSERT 时才生效，
    # 未 flush 的新对象上 hard_pity_5 之类全是 None，
    # 校验会在 `None < 1` 上抛 TypeError（表现为 500 而不是 400）。
    # flush 之后校验的是"真正会被存进去的东西"；校验失败则整体回滚。
    try:
        await db.flush()
        validate_pool(pool, items=built_items, require_items=False)
    except Exception:
        await db.rollback()
        raise

    await db.commit()
    await db.refresh(pool)

    await admin_log_service.log_action(
        db,
        admin=admin,
        action="pool.create",
        target_type="pool",
        target_id=pool.id,
        detail={"key": pool.key, "name": pool.name, "pool_type": pool.pool_type},
        ip=get_client_ip(request),
    )
    return pool_to_admin_out(pool)


@router.put("/pools/{pool_id}")
async def update_pool(
    pool_id: int, body: PoolIn, request: Request, db: DbSession, admin: AdminUser
) -> PoolAdminOut:
    pool = await get_pool_or_404(db, pool_id)
    before = snapshot(pool)

    payload = body.model_dump(exclude={"items"}, exclude_none=True)

    # key 与 pool_type 不允许改：前者是外部引用的稳定标识，
    # 后者会改变 UP/歪池的类型校验语义，改错会让引擎拒绝运行。
    for locked in ("key", "pool_type"):
        if locked in payload and payload[locked] != getattr(pool, locked):
            raise PoolConfigError(f"卡池的 {locked} 不允许修改")

    for field, value in payload.items():
        setattr(pool, field, value)

    if body.items is not None:
        # 按 id 做差量更新，而不是整表重建。
        # 整表重建会让每条物品换新 id，而 UserEpitomizedPath.charted_item_id
        # 是 ON DELETE SET NULL —— 管理员只改了个描述，玩家的定轨选择就没了。
        existing = {i.id: i for i in pool.items}
        keep: set[int] = set()

        for incoming in body.items:
            payload = incoming.model_dump(exclude={"id"})
            if incoming.id is not None and incoming.id in existing:
                for field, value in payload.items():
                    setattr(existing[incoming.id], field, value)
                keep.add(incoming.id)
            else:
                pool.items.append(PoolItem(**payload))

        for item_id, item in list(existing.items()):
            if item_id not in keep:
                pool.items.remove(item)

    # ★ 保存时就校验，而不是等玩家抽卡时才炸
    validate_pool(pool)

    await db.commit()
    await db.refresh(pool)

    after = snapshot(pool)
    changed = admin_log_service.diff_fields(before, after)
    if changed:
        await admin_log_service.log_action(
            db,
            admin=admin,
            action="pool.update",
            target_type="pool",
            target_id=pool.id,
            detail=changed,
            ip=get_client_ip(request),
        )
    return pool_to_admin_out(pool)


@router.delete("/pools/{pool_id}")
async def delete_pool(pool_id: int, request: Request, db: DbSession, admin: AdminUser) -> dict:
    pool = await get_pool_or_404(db, pool_id)

    pulled = int(
        (
            await db.execute(
                select(func.count()).select_from(PullRecord).where(PullRecord.pool_id == pool_id)
            )
        ).scalar_one()
    )

    name, key = pool.name, pool.key
    await db.delete(pool)
    await db.commit()

    await admin_log_service.log_action(
        db,
        admin=admin,
        action="pool.delete",
        target_type="pool",
        target_id=pool_id,
        detail={
            "key": key,
            "name": name,
            # 历史记录靠快照字段存活，删池不会毁掉玩家的记录，但要说清楚
            "existing_records": pulled,
            "note": "历史记录因存有快照字段而完整保留",
        },
        ip=get_client_ip(request),
    )
    return {"ok": True, "existing_records": pulled}


@router.post("/pools/{pool_id}/set-current")
async def set_current(
    pool_id: int, request: Request, db: DbSession, admin: AdminUser
) -> PoolAdminOut:
    """切换开放状态。可同时开放多个，主推只有一个。"""
    pool = await get_pool_or_404(db, pool_id)

    if not pool.is_current:
        # 开放前先确认这个池子真的能抽。
        # 否则会出现"玩家看得见、点下去报错"的卡池 —— 这比干脆不开放糟糕得多。
        validate_pool(pool)

    pool.is_current = not pool.is_current

    if pool.is_current:
        pool.is_active = True
    else:
        # 关闭时同时取消主推，避免出现"未开放的主推卡池"
        pool.is_main = False

    await db.commit()
    await db.refresh(pool)

    await admin_log_service.log_action(
        db,
        admin=admin,
        action="pool.set_current",
        target_type="pool",
        target_id=pool.id,
        detail={"name": pool.name, "is_current": pool.is_current},
        ip=get_client_ip(request),
    )
    return pool_to_admin_out(pool)


@router.post("/pools/{pool_id}/set-main")
async def set_main(pool_id: int, request: Request, db: DbSession, admin: AdminUser) -> PoolAdminOut:
    """设为当前主推卡池（全局唯一）。"""
    pool = await get_pool_or_404(db, pool_id)

    if not pool.is_current:
        raise PoolConfigError("只能把已开放的卡池设为主推")

    # 主推全局唯一：先清掉其它的
    others = (await db.execute(select(Pool).where(Pool.id != pool_id))).scalars().all()
    for other in others:
        other.is_main = False

    pool.is_main = True
    await db.commit()
    await db.refresh(pool)

    await admin_log_service.log_action(
        db,
        admin=admin,
        action="pool.set_main",
        target_type="pool",
        target_id=pool.id,
        detail={"name": pool.name},
        ip=get_client_ip(request),
    )
    return pool_to_admin_out(pool)


@router.post("/pools/{pool_id}/items")
async def add_item(
    pool_id: int, body: PoolItemIn, request: Request, db: DbSession, admin: AdminUser
) -> dict:
    pool = await get_pool_or_404(db, pool_id)
    item = body
    pool.items.append(PoolItem(**item.model_dump()))

    try:
        validate_pool(pool)
    except PoolConfigError:
        await db.rollback()
        raise

    await db.commit()
    await admin_log_service.log_action(
        db,
        admin=admin,
        action="item.create",
        target_type="pool_item",
        target_id=pool.id,
        detail={"pool": pool.name, "item": item.name},
        ip=get_client_ip(request),
    )
    return {"ok": True}


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, request: Request, db: DbSession, admin: AdminUser) -> dict:
    item = await db.get(PoolItem, item_id)
    if item is None:
        raise NotFound("物品不存在")

    pool = await db.get(Pool, item.pool_id)
    name = item.name
    await db.delete(item)
    await db.flush()

    if pool is not None:
        try:
            validate_pool(pool)
        except PoolConfigError:
            await db.rollback()
            raise

    await db.commit()
    await admin_log_service.log_action(
        db,
        admin=admin,
        action="item.delete",
        target_type="pool_item",
        target_id=item_id,
        detail={"name": name},
        ip=get_client_ip(request),
    )
    return {"ok": True}


# ===========================================================================
# 素材上传
# ===========================================================================
@router.post("/upload", response_model=UploadOut)
async def upload_asset(
    request: Request,
    db: DbSession,
    admin: AdminUser,
    file: Annotated[UploadFile, File()],
    category: Annotated[str, Form()] = "misc",
) -> UploadOut:
    """上传素材。

    文件会被重命名为 uuid4，原始文件名只作为日志留痕，绝不参与路径拼接。
    """
    content = await file.read()
    try:
        stored = await upload_service.save_image(
            content=content,
            filename=file.filename or "",
            category=category,
        )
    except Exception as exc:
        # 被拒的上传同样要留痕：有人反复尝试上传伪造图片，是值得被看到的安全信号。
        # 这里只记原始文件名与拒绝原因，不记文件内容。
        await admin_log_service.log_action(
            db,
            admin=admin,
            action="asset.reject",
            target_type="asset",
            target_id=category,
            detail={
                "original_name": (file.filename or "")[:120],
                "size": len(content),
                "reason": getattr(exc, "message", str(exc))[:160],
            },
            ip=get_client_ip(request),
            # 必须在这里就提交：异常继续向上抛，get_db 会执行 rollback，
            # 未提交的日志行会被一起回滚掉，等于没记。
            commit=True,
        )
        raise

    await admin_log_service.log_action(
        db,
        admin=admin,
        action="asset.upload",
        target_type="asset",
        target_id=stored.relative_path,
        detail={
            "category": category,
            "original_name": stored.original_name,
            "size": stored.size,
            "detected": stored.detected_format,
        },
        ip=get_client_ip(request),
    )
    return UploadOut(
        url=stored.url,
        size=stored.size,
        content_type=stored.content_type,
        original_name=stored.original_name,
    )


@router.get("/api/assets")
async def list_assets(db: DbSession, admin: AdminUser) -> list[dict]:
    """列出已上传的素材，并标出正在被哪些卡池/物品使用。"""
    used: dict[str, list[str]] = {}
    pools = await list_all_pools(db)
    for pool in pools:
        for field in ("background_image_url", "banner_url", "icon_url"):
            url = getattr(pool, field)
            if url:
                used.setdefault(url, []).append(pool.name)
        for item in pool.items:
            for url in (item.image_url, item.icon_url):
                if url:
                    used.setdefault(url, []).append(f"{pool.name}/{item.name}")

    out: list[dict] = []
    root = settings.upload_dir
    if not root.exists():
        return out

    for path in sorted(root.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative = path.relative_to(root).as_posix()
        url = f"/static/uploads/{relative}"
        out.append(
            {
                "url": url,
                "name": path.name,
                "category": relative.split("/")[0] if "/" in relative else "misc",
                "size": path.stat().st_size,
                "used_by": used.get(url, []),
            }
        )
    return out


# ===========================================================================
# 全局统计与配置
# ===========================================================================
@router.get("/stats", response_model=GlobalStatsOut)
async def global_stats(db: DbSession, admin: AdminUser) -> GlobalStatsOut:
    """全局抽卡统计。"""
    now = utcnow()

    user_row = (
        await db.execute(
            select(
                func.count().label("total"),
                tally(User.is_active).label("active"),
                tally(User.role == UserRole.ADMIN).label("admins"),
            )
        )
    ).one()

    pull_row = (
        await db.execute(
            select(
                func.count().label("total"),
                tally(PullRecord.star == 5).label("five"),
                tally(PullRecord.star == 4).label("four"),
            )
        )
    ).one()

    today = int(
        (
            await db.execute(
                select(func.count())
                .select_from(PullRecord)
                .where(PullRecord.created_at >= now - timedelta(days=1))
            )
        ).scalar_one()
    )
    week = int(
        (
            await db.execute(
                select(func.count())
                .select_from(PullRecord)
                .where(PullRecord.created_at >= now - timedelta(days=7))
            )
        ).scalar_one()
    )

    total_pulls = int(pull_row.total or 0)
    five = int(pull_row.five or 0)

    top = (
        await db.execute(
            select(PullRecord.item_name_snapshot, func.count().label("n"))
            .where(PullRecord.star == 5, PullRecord.item_name_snapshot != "")
            .group_by(PullRecord.item_name_snapshot)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()

    breakdown = (
        await db.execute(
            select(
                PullRecord.pool_name_snapshot,
                func.count().label("n"),
                tally(PullRecord.star == 5).label("five"),
            )
            .where(PullRecord.pool_name_snapshot != "")
            .group_by(PullRecord.pool_name_snapshot)
            .order_by(func.count().desc())
        )
    ).all()

    return GlobalStatsOut(
        total_users=int(user_row.total or 0),
        active_users=int(user_row.active or 0),
        admin_count=int(user_row.admins or 0),
        total_pulls=total_pulls,
        total_five_stars=five,
        total_four_stars=int(pull_row.four or 0),
        five_star_rate=round(five / total_pulls, 5) if total_pulls else 0.0,
        pulls_today=today,
        pulls_last_7_days=week,
        top_items=[{"name": r[0], "count": r[1]} for r in top],
        pool_breakdown=[
            {"pool_name": r[0], "pulls": r[1], "five_stars": int(r[2] or 0)} for r in breakdown
        ],
    )


@router.get("/api/settings")
async def get_settings_api(db: DbSession, admin: AdminUser) -> dict:
    return {
        "initial": dict(
            zip(
                ("primogems", "intertwined_fate", "acquaint_fate"),
                await setting_service.get_initial_currency(db),
                strict=True,
            )
        ),
        "animation": await setting_service.get_animation_config(db),
        "prompt_templates": await setting_service.get_dict(
            db, setting_service.KEY_PROMPT_TEMPLATES, {}
        ),
        "default_templates": DEFAULT_TEMPLATES,
    }


@router.put("/api/settings")
async def update_settings_api(
    body: SettingsIn, request: Request, db: DbSession, admin: AdminUser
) -> dict:
    changed: dict[str, Any] = {}

    if body.initial_primogems is not None:
        await setting_service.set_raw(
            db, setting_service.KEY_INITIAL_PRIMOGEMS, body.initial_primogems
        )
        changed["initial_primogems"] = body.initial_primogems
    if body.initial_intertwined_fate is not None:
        await setting_service.set_raw(
            db, setting_service.KEY_INITIAL_INTERTWINED, body.initial_intertwined_fate
        )
        changed["initial_intertwined_fate"] = body.initial_intertwined_fate
    if body.initial_acquaint_fate is not None:
        await setting_service.set_raw(
            db, setting_service.KEY_INITIAL_ACQUAINT, body.initial_acquaint_fate
        )
        changed["initial_acquaint_fate"] = body.initial_acquaint_fate

    if body.animation is not None:
        merged = await setting_service.get_animation_config(db)
        merged.update(body.animation)
        await setting_service.set_raw(db, setting_service.KEY_ANIMATION, merged)
        changed["animation"] = body.animation

    if body.prompt_templates is not None:
        await setting_service.set_raw(
            db, setting_service.KEY_PROMPT_TEMPLATES, body.prompt_templates
        )
        # 提示词模板可能很长，日志里只记键名，不记全文
        changed["prompt_templates"] = sorted(body.prompt_templates)

    await db.commit()

    if changed:
        await admin_log_service.log_action(
            db,
            admin=admin,
            action="settings.update",
            target_type="settings",
            target_id="global",
            detail=changed,
            ip=get_client_ip(request),
        )
    return {"ok": True, "changed": sorted(changed)}


# ===========================================================================
# 操作日志 API
# ===========================================================================
@router.get("/api/logs")
async def logs_api(
    db: DbSession,
    admin: AdminUser,
    action: str | None = None,
    admin_username: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    rows, total = await admin_log_service.query_logs(
        db, action=action, admin_username=admin_username, page=page, page_size=page_size
    )
    return {
        "items": [
            {
                "id": r.id,
                "admin_username": r.admin_username,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "detail": r.detail,
                "ip": r.ip,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }
