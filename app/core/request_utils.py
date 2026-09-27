"""请求相关的通用判断。"""

from __future__ import annotations

from fastapi import Request


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
