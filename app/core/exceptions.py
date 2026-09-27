"""领域异常。

统一在 ``app/main.py`` 注册处理器，按请求路径区分响应形态：
HTML 页面请求重定向 / 渲染错误页，``/api/*`` 请求返回 JSON。
"""

from __future__ import annotations


class AppError(Exception):
    """所有业务异常的基类。"""

    status_code: int = 400
    message: str = "请求无效"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


# --------------------------------------------------------------------------
# 认证与授权
# --------------------------------------------------------------------------
class AuthRequired(AppError):
    """未登录。HTML 请求应重定向到登录页，API 请求返回 401。"""

    status_code = 401
    message = "请先登录"


class Forbidden(AppError):
    """已登录但权限不足（普通用户访问后台）。"""

    status_code = 403
    message = "无权访问该资源"


class AccountDisabled(AppError):
    status_code = 403
    message = "账号已被禁用"


class TooManyAttempts(AppError):
    status_code = 429
    message = "登录失败次数过多，请稍后再试"


# --------------------------------------------------------------------------
# 抽卡
# --------------------------------------------------------------------------
class PoolNotFound(AppError):
    status_code = 404
    message = "卡池不存在"


class PoolClosed(AppError):
    status_code = 400
    message = "该卡池当前未开放"


class BadPullCount(AppError):
    status_code = 400
    message = "不支持的抽卡次数"


class InsufficientCurrency(AppError):
    status_code = 400
    message = "货币不足"


class PullCapExceeded(AppError):
    status_code = 400
    message = "该卡池剩余可抽次数不足"


class EpitomizedDisabled(AppError):
    status_code = 400
    message = "该卡池未开启定轨"


class NotAFeaturedWeapon(AppError):
    status_code = 400
    message = "只能定轨当前卡池的 UP 五星武器"


# --------------------------------------------------------------------------
# 卡池配置（管理员保存时校验）
# --------------------------------------------------------------------------
class PoolConfigError(AppError):
    """卡池规则自相矛盾，例如软保底起点晚于硬保底、UP 池为空等。

    这类配置一旦入库会让抽卡引擎进入未定义行为，因此在**保存时**就必须拒绝，
    而不是等玩家抽卡时才炸。
    """

    status_code = 400
    message = "卡池配置无效"


# --------------------------------------------------------------------------
# 上传
# --------------------------------------------------------------------------
class UploadRejected(AppError):
    status_code = 400
    message = "文件被拒绝"
