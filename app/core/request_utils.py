"""请求相关的通用判断与错误信息整理。"""

from __future__ import annotations

from typing import Any

from fastapi import Request

#: Pydantic 字段名 -> 中文标签，用于把校验错误翻译成人话
FIELD_LABELS = {
    "username": "用户名",
    "password": "密码",
    "amount": "数量",
    "pool_id": "卡池",
    "count": "抽卡次数",
    "star": "星级",
    "name": "名称",
    "key": "标识",
    "pool_type": "卡池类型",
}

#: Pydantic 在自定义校验器抛 ValueError 时会给消息加上这个前缀
_VALUE_ERROR_PREFIX = "Value error, "


def describe_validation_error(exc: Any, fallback: str) -> str:
    """把 Pydantic 的校验错误转成用户能看懂的一句话。

    直接吞掉原始错误、统一回一句"请填写用户名与密码"是很糟的体验：
    用户明明填了，只是长度不对，却看到"请填写"。

    也用于 FastAPI 的 RequestValidationError —— 它默认返回的是结构化数组，
    前端拿到只能显示成 ``[object Object]``。
    """
    try:
        errors = exc.errors()
    except Exception:  # pragma: no cover - 理论上不会发生
        return fallback
    if not errors:
        return fallback

    first = errors[0]
    loc = first.get("loc") or ()
    # loc 形如 ("body", "amount")，取最后一段作为字段名
    field = str(loc[-1]) if loc else ""
    label = FIELD_LABELS.get(field, field)
    kind = first.get("type", "")
    message = str(first.get("msg", ""))

    # 自定义校验器给的消息本身就是人话，直接用（去掉 Pydantic 加的前缀）
    if message.startswith(_VALUE_ERROR_PREFIX):
        return message[len(_VALUE_ERROR_PREFIX) :]

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
    if kind in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
        return f"{label}超出允许范围"
    return f"{label}格式不正确" if field else fallback


def wants_json(request: Request) -> bool:
    """判断请求期望 JSON 还是 HTML。

    放在 core 而不是 main 里：路由模块需要用它来在同一路径上分派
    "浏览器要页面 / XHR 要数据"，如果它住在 main 里就会形成
    路由 → 入口模块的反向依赖，也让循环导入成为隐患。

    只按路径前缀判断是不够的：``/auth/login``、``/admin/logs`` 这类路径
    浏览器和 XHR 都会访问，返回错了前端就只能显示"服务器返回了非预期的响应"。
    """
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/admin/api/"):
        return True
    # 纯 JSON 端点，没有对应的 HTML 页面：未登录时应当返回 401，
    # 而不是被当成页面重定向到登录页（前端拿不到 401 就无法区分"没登录"和"没权限"）。
    if path == "/admin/stats":
        return True
    # 后台整个界面都是 fetch 驱动的，没有任何原生表单提交。
    # 因此非 GET 的后台请求一定是 API 调用，错误也应当以 JSON 返回 ——
    # 否则上传被拒时前端收到的是一整页 HTML，只能显示"服务器返回了非预期的响应"。
    if path.startswith("/admin/") and request.method not in ("GET", "HEAD"):
        return True
    # 我们自己前端发的 fetch 请求
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    # 请求体是 JSON 的，响应也应当是 JSON
    if request.headers.get("content-type", "").startswith("application/json"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept
