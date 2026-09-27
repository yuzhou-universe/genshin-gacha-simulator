"""认证相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.security import PASSWORD_MIN, USERNAME_MAX, USERNAME_MIN


class RegisterRequest(BaseModel):
    username: str = Field(min_length=USERNAME_MIN, max_length=USERNAME_MAX)
    password: str = Field(min_length=PASSWORD_MIN, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=USERNAME_MAX)
    password: str = Field(min_length=1, max_length=128)


class CurrencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    primogems: int = 0
    intertwined_fate: int = 0
    acquaint_fate: int = 0


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    created_at: datetime
    last_login_at: datetime | None = None
    currency: CurrencyOut | None = None


class AuthResponse(BaseModel):
    """登录/注册成功后返回，前端据此决定跳转目标。"""

    user: UserOut
    #: 普通用户 -> /gacha，管理员 -> /admin
    redirect_to: str
