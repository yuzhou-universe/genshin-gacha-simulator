"""货币 API（用户端）。

目前只有一个端点：自助增加原石。这是模拟器的便利功能，不是付费入口，
但**档位必须由服务端白名单校验** —— 让客户端传任意数值等于开了个随便刷的口子。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.deps import DbSession, RequiredUser
from app.services import currency_service

router = APIRouter(prefix="/api/currency", tags=["currency"])


class TopUpRequest(BaseModel):
    #: 只接受配置里的档位。上限校验交给下面的 validator，错误信息才能是人话。
    amount: int = Field(gt=0, le=10_000_000)

    @field_validator("amount")
    @classmethod
    def _must_be_an_allowed_option(cls, v: int) -> int:
        options = settings.topup_options
        if v not in options:
            allowed = " / ".join(str(o) for o in options)
            raise ValueError(f"只能选择这些档位：{allowed}")
        return v


class TopUpResult(BaseModel):
    primogems: int
    #: 本次实际增加的数量。达到上限时会小于请求值。
    added: int
    requested: int
    cap: int
    #: 本次是否被上限截断
    capped: bool
    #: 余额是否已经顶到上限
    at_cap: bool


@router.get("/topup-options")
async def topup_options(user: RequiredUser) -> dict:
    """当前可选的增加档位与上限。前端据此渲染，避免与服务端白名单脱节。"""
    return {"options": settings.topup_options, "cap": settings.primogem_topup_cap}


@router.post("/topup", response_model=TopUpResult)
async def topup(body: TopUpRequest, db: DbSession, user: RequiredUser) -> TopUpResult:
    """给当前用户增加原石，余额在上限处截断。

    达到上限时**截断而不是报错**：若直接拒绝，余额停在 499,999 时任何档位
    都会超出上限，用户就永久卡死、再也加不了。截断后返回实际增加量，
    界面可以如实告知"本次只加了多少"。
    """
    outcome = await currency_service.topup_primogems(db, user.id, body.amount)

    return TopUpResult(
        primogems=outcome.primogems,
        added=outcome.added,
        requested=outcome.requested,
        cap=outcome.cap,
        capped=outcome.capped,
        at_cap=outcome.at_cap,
    )
