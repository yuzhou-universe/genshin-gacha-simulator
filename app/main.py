"""FastAPI 应用入口。

启动流程（lifespan）：
1. 自动执行 Alembic 迁移到 head —— 因此 ``uvicorn app.main:app --reload`` 可以
   直接跑起来，无需先手动执行迁移命令。
2. 幂等引导管理员账号（来自 ADMIN_USERNAME / ADMIN_PASSWORD 环境变量）。
3. 首次启动时写入默认卡池种子数据。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from alembic import command
from app.core.config import BASE_DIR, settings
from app.core.exceptions import AppError, AuthRequired
from app.core.request_utils import wants_json
from app.core.templating import render
from app.db.session import SessionLocal
from app.routers import admin, api_ai, api_history, api_pools, api_pull, auth, pages
from app.services import auth_service, seed_service

logger = logging.getLogger("app")


def _run_migrations() -> None:
    """在独立线程中执行迁移。

    Alembic 的 ``command.upgrade`` 内部会调用 ``asyncio.run()``，而 lifespan
    本身已处于事件循环中，直接调用会抛 "asyncio.run() cannot be called from a
    running event loop"。放进线程即可获得干净的事件循环。
    """
    cfg = AlembicConfig(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.validate_production_safety()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    await asyncio.to_thread(_run_migrations)

    async with SessionLocal() as db:
        admin_user, password_changed = await auth_service.ensure_admin(db)
        if password_changed:
            logger.warning("管理员 %s 的密码已按 ADMIN_PASSWORD 环境变量重置", admin_user.username)
        created = await seed_service.ensure_seed_pools(db)
        if created:
            logger.info("已写入 %d 个默认卡池", created)

    logger.info("启动完成：%s (debug=%s)", settings.app_name, settings.debug)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan, debug=settings.debug)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie,
        max_age=settings.session_max_age,
        same_site="lax",
        # 生产环境走 HTTPS 时应置 True；开发环境为 False 否则 Cookie 不发出去
        https_only=not settings.debug,
    )

    # ---------------- 异常处理 ----------------
    @app.exception_handler(AuthRequired)
    async def _auth_required(request: Request, exc: AuthRequired):
        if wants_json(request):
            return JSONResponse({"detail": exc.message}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        if wants_json(request):
            return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
        return render(
            request,
            "error.html",
            {"status_code": exc.status_code, "message": exc.message},
            # 必须传：否则错误页会以 200 返回，
            # "普通用户访问后台"看起来就是成功了
            status_code=exc.status_code,
        )

    # ---------------- 路由 ----------------
    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(admin.router)
    app.include_router(api_pools.router)
    app.include_router(api_pull.router)
    app.include_router(api_history.router)
    app.include_router(api_ai.router)

    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

    return app


app = create_app()
