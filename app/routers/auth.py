"""认证路由：注册、登录、登出。

同时接受 JSON 与表单编码请求体，因此即使浏览器禁用 JS，页面上的原生表单
提交依然可用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError as PydanticValidationError

from app.core.deps import CurrentUser, DbSession, get_client_ip
from app.core.exceptions import AppError, Forbidden
from app.core.security import validate_password, validate_username
from app.core.templating import render
from app.models import UserRole
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest
from app.services import auth_service

router = APIRouter(tags=["auth"])


def redirect_for(user_role: str) -> str:
    """登录后按角色跳转：管理员进后台，普通用户进抽卡界面。"""
    return "/admin" if user_role == UserRole.ADMIN else "/gacha"


async def read_payload(request: Request) -> dict[str, Any]:
    """兼容 JSON 与表单两种请求体。"""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


class AuthError(AppError):
    status_code = 400
    message = "认证失败"


#: Pydantic 字段名 -> 中文标签，用于把校验错误翻译成人话
_FIELD_LABELS = {"username": "用户名", "password": "密码"}


def describe_validation_error(exc: PydanticValidationError, fallback: str) -> str:
    """把 Pydantic 的校验错误转成用户能看懂的一句话。

    直接吞掉原始错误、统一回一句"请填写用户名与密码"是很糟的体验：
    用户明明填了，只是长度不对，却看到"请填写"。这里把真实原因带出来。
    """
    errors = exc.errors()
    if not errors:
        return fallback

    first = errors[0]
    field = str(first.get("loc", ("",))[-1])
    label = _FIELD_LABELS.get(field, field)
    kind = first.get("type", "")

    if kind == "string_too_short":
        limit = (first.get("ctx") or {}).get("min_length")
        if field == "password":
            return f"密码至少 {limit} 位"
        return f"{label}至少 {limit} 个字符"
    if kind == "string_too_long":
        limit = (first.get("ctx") or {}).get("max_length")
        return f"{label}最多 {limit} 个字符"
    if kind == "missing":
        return f"请填写{label}"
    return f"{label}格式不正确"


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
@router.get("/login")
async def login_page(request: Request, user: CurrentUser):
    if user is not None:
        return render(request, "auth/redirect.html", {"target": redirect_for(user.role)})
    return render(request, "auth/login.html")


@router.get("/register")
async def register_page(request: Request, user: CurrentUser):
    if user is not None:
        return render(request, "auth/redirect.html", {"target": redirect_for(user.role)})
    return render(request, "auth/register.html")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@router.post("/auth/register", response_model=AuthResponse)
async def api_register(request: Request, db: DbSession) -> AuthResponse:
    raw = await read_payload(request)
    try:
        payload = RegisterRequest(**raw)
    except PydanticValidationError as exc:
        raise AuthError(describe_validation_error(exc, "请填写用户名与密码")) from None

    # auth_service.ValidationError 本身就是 AppError，无需在此包裹
    user = await auth_service.register(db, payload.username, payload.password)

    # 注册成功直接建立会话，省去一次登录
    request.session.clear()
    request.session["user_id"] = user.id
    return AuthResponse(user=user, redirect_to=redirect_for(user.role))  # type: ignore[arg-type]


@router.post("/auth/login", response_model=AuthResponse)
async def api_login(request: Request, db: DbSession) -> AuthResponse:
    raw = await read_payload(request)
    try:
        payload = LoginRequest(**raw)
    except PydanticValidationError as exc:
        raise AuthError(describe_validation_error(exc, "请填写用户名与密码")) from None

    user = await auth_service.authenticate(
        db, payload.username, payload.password, ip=get_client_ip(request)
    )

    request.session.clear()
    request.session["user_id"] = user.id
    return AuthResponse(user=user, redirect_to=redirect_for(user.role))  # type: ignore[arg-type]


@router.post("/auth/logout")
async def api_logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"redirect_to": "/login"}


# --------------------------------------------------------------------------
# 供路由内联校验复用
# --------------------------------------------------------------------------
def check_credentials(username: str, password: str) -> None:
    ok, msg = validate_username(username)
    if not ok:
        raise AuthError(msg)
    ok, msg = validate_password(password)
    if not ok:
        raise AuthError(msg)


__all__ = ["router", "redirect_for", "read_payload", "AuthError", "Forbidden"]
