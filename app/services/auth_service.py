"""注册、登录、登录失败限制与管理员引导。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AccountDisabled, AppError, TooManyAttempts
from app.core.security import (
    fake_verify,
    hash_password,
    needs_rehash,
    validate_password,
    validate_username,
    verify_password,
)
from app.db.base import utcnow
from app.models import LoginAttempt, User, UserCurrency, UserRole
from app.services import setting_service


class ValidationError(AppError):
    """注册/登录参数不合法。消息可直接展示给用户。

    继承 ``AppError`` 而非裸 ``Exception``：否则每个调用点都得记得 try/except，
    漏掉一处就会变成 500（登录密码错误曾经就是这样返回 500 的）。
    """

    status_code = 400
    message = "认证失败"


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def count_recent_failures(db: AsyncSession, username: str) -> int:
    """统计锁定窗口内该用户名的失败次数。"""
    since = utcnow() - timedelta(seconds=settings.login_lockout_seconds)
    result = await db.execute(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.username == username,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= since,
        )
    )
    return int(result.scalar_one())


async def _record_attempt(db: AsyncSession, username: str, ip: str, *, success: bool) -> None:
    db.add(LoginAttempt(username=username[:64], ip=ip[:64], success=success))


async def register(db: AsyncSession, username: str, password: str) -> User:
    """注册新用户。用户名唯一，密码以 argon2 哈希存储。"""
    ok, msg = validate_username(username)
    if not ok:
        raise ValidationError(msg)

    ok, msg = validate_password(password)
    if not ok:
        raise ValidationError(msg)

    if await get_user_by_username(db, username) is not None:
        raise ValidationError("该用户名已被注册")

    user = User(username=username, password_hash=hash_password(password), role=UserRole.USER)
    db.add(user)
    await db.flush()  # 取得 user.id

    primogems, intertwined, acquaint = await setting_service.get_initial_currency(db)
    db.add(
        UserCurrency(
            user_id=user.id,
            primogems=primogems,
            intertwined_fate=intertwined,
            acquaint_fate=acquaint,
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate(db: AsyncSession, username: str, password: str, ip: str = "") -> User:
    """校验凭据。失败时抛 ValidationError，超限时抛 TooManyAttempts。"""
    if await count_recent_failures(db, username) >= settings.login_max_attempts:
        raise TooManyAttempts(
            f"登录失败次数过多，请 {settings.login_lockout_seconds // 60} 分钟后再试"
        )

    user = await get_user_by_username(db, username)

    if user is None:
        # 用户名不存在时也做一次哈希校验，抹平响应时间差，防止用户名枚举
        fake_verify()
        await _record_attempt(db, username, ip, success=False)
        await db.commit()
        raise ValidationError("用户名或密码错误")

    if not verify_password(password, user.password_hash):
        await _record_attempt(db, username, ip, success=False)
        await db.commit()
        raise ValidationError("用户名或密码错误")

    if not user.is_active:
        await _record_attempt(db, username, ip, success=False)
        await db.commit()
        raise AccountDisabled

    # 哈希参数升级后透明重算
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.last_login_at = utcnow()
    await _record_attempt(db, username, ip, success=True)
    await db.commit()
    await db.refresh(user)
    return user


async def ensure_admin(db: AsyncSession) -> tuple[User, bool]:
    """按环境变量幂等引导管理员账号。

    返回 ``(admin, password_changed)``。

    - 账号不存在 → 创建。
    - 账号已存在但环境变量里的密码与库中不符 → **覆盖**（需求要求管理员密码
      必须可通过环境变量控制，因此环境变量是唯一事实来源）。
    - 用户名已存在但角色不是 admin → 提升为 admin。
    """
    username = settings.admin_username
    password = settings.admin_password

    ok, msg = validate_username(username)
    if not ok:
        raise RuntimeError(f"ADMIN_USERNAME 不合法: {msg}")
    ok, msg = validate_password(password)
    if not ok:
        raise RuntimeError(f"ADMIN_PASSWORD 不合法: {msg}")

    user = await get_user_by_username(db, username)
    password_changed = False

    if user is None:
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        primogems, intertwined, acquaint = await setting_service.get_initial_currency(db)
        db.add(
            UserCurrency(
                user_id=user.id,
                primogems=primogems,
                intertwined_fate=intertwined,
                acquaint_fate=acquaint,
            )
        )
    else:
        if not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password)
            password_changed = True
        if user.role != UserRole.ADMIN:
            user.role = UserRole.ADMIN
        user.is_active = True

    await db.commit()
    await db.refresh(user)
    return user, password_changed
