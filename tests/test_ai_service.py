"""DeepSeek Flash 集成测试。

用 httpx.MockTransport 假造 DeepSeek 端点，因此超时、限流、模型名错误、
配置错误这些路径都能被真实地走到，而不需要联网。

核心断言只有一条：**无论 AI 出什么状况，调用方永远拿到可用的文案，
且永远不抛异常**。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.services.ai_service import AIService
from app.services.pool_rules import PoolRules
from tests.conftest import FakeItem


@pytest.fixture
def enable_ai(monkeypatch):
    """打开 AI 开关（提供假密钥），每个测试独立。"""
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key-not-real")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-flash")
    monkeypatch.setattr(settings, "deepseek_max_retries", 2)
    monkeypatch.setattr(settings, "deepseek_timeout", 1.0)
    monkeypatch.setattr(settings, "deepseek_disable_thinking", True)


def make_service(handler) -> AIService:
    return AIService(transport=httpx.MockTransport(handler))


def ok_response(content: str = "金光乍现，恭喜出金！") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )


def error_response(status: int, message: str = "boom") -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message}})


SAMPLE_ITEMS = [
    {"name": "胡桃", "star": 5, "is_up": True, "is_pity": False},
    {"name": "行秋", "star": 4, "is_up": True, "is_pity": False},
    {"name": "冷刃", "star": 3, "is_up": False, "is_pity": False},
]


async def call_comment(service: AIService):
    return await service.comment_on_pull(
        pool_name="角色活动祈愿",
        count=10,
        items=SAMPLE_ITEMS,
        until_hard_pity=42,
        guaranteed=False,
    )


# ===========================================================================
# 正常路径
# ===========================================================================
async def test_returns_model_text_on_success(enable_ai):
    service = make_service(lambda req: ok_response("这一发，值了！"))

    text, from_model = await call_comment(service)

    assert text == "这一发，值了！"
    assert from_model is True
    await service.aclose()


async def test_disables_thinking_by_default(enable_ai):
    """deepseek-flash 默认开启 thinking，生成短点评必须显式关闭。

    否则延迟与费用都消耗在思维链上，而用户只想要一句话。
    """
    service = make_service(lambda req: ok_response())
    await call_comment(service)

    body = service.sent_payloads[0]
    assert body["thinking"] == {"type": "disabled"}
    assert body["model"] == "deepseek-flash"
    assert body["stream"] is False
    await service.aclose()


async def test_thinking_can_be_left_on_when_configured(enable_ai, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_disable_thinking", False)
    service = make_service(lambda req: ok_response())
    await call_comment(service)

    assert "thinking" not in service.sent_payloads[0]
    await service.aclose()


# ===========================================================================
# 隐私
# ===========================================================================
async def test_payload_contains_no_user_identity(enable_ai):
    """发给模型的提示词正文里不能出现任何用户身份信息。

    只检查 messages 的内容，不检查整个请求体：像 ``max_tokens`` 这样合法的
    API 参数名里就含 "token" 子串，对整体做子串匹配会误报。
    """
    service = make_service(lambda req: ok_response())
    await call_comment(service)

    body = service.sent_payloads[0]
    prompt_text = "\n".join(m["content"] for m in body["messages"])
    lowered = prompt_text.lower()

    for forbidden in [
        "traveler",
        "user_id",
        "userid",
        "password",
        "passwd",
        "session",
        "cookie",
        "bearer",
        "api_key",
        "apikey",
    ]:
        assert forbidden not in lowered, f"提示词里不应出现 {forbidden!r}"

    # 密钥本身绝不能出现在请求体的任何位置
    assert "test-key-not-real" not in json.dumps(body)

    # 只应包含游戏内公开信息
    assert "角色活动祈愿" in prompt_text
    assert "胡桃" in prompt_text
    await service.aclose()


async def test_service_signature_has_no_user_parameter(enable_ai):
    """结构保证：comment_on_pull 的入参里根本没有 user 对象。

    这比"运行时检查有没有泄漏"更可靠 —— 调用方没有任何入口能把用户身份传进来。
    """
    import inspect

    sig = inspect.signature(AIService.comment_on_pull)
    params = set(sig.parameters)

    assert "user" not in params
    assert not any("user" in p for p in params)
    assert params == {
        "self",
        "pool_name",
        "count",
        "items",
        "until_hard_pity",
        "guaranteed",
        "templates",
    }


async def test_logs_and_errors_never_contain_api_key(enable_ai, caplog):
    """报错日志里不能泄露密钥。"""
    service = make_service(lambda req: error_response(401, "Authentication Fails"))
    with caplog.at_level("WARNING"):
        await call_comment(service)

    assert "test-key-not-real" not in caplog.text
    await service.aclose()


# ===========================================================================
# 降级：各类失败都必须返回本地模板，且不抛异常
# ===========================================================================
async def test_no_api_key_uses_local_template(monkeypatch):
    """未配置密钥时直接走本地模板，连网络都不碰。"""
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    calls = []

    def handler(req):
        calls.append(req)
        return ok_response()

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False
    assert text
    assert calls == [], "没有密钥时不应发出任何请求"
    await service.aclose()


async def test_timeout_falls_back(enable_ai):
    def handler(req):
        raise httpx.ReadTimeout("timed out", request=req)

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False
    assert text, "超时后必须仍有本地文案"
    await service.aclose()


async def test_connection_error_falls_back(enable_ai):
    def handler(req):
        raise httpx.ConnectError("connection refused", request=req)

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False and text
    await service.aclose()


async def test_server_error_retries_then_falls_back(enable_ai):
    """5xx 会重试，重试耗尽后降级。"""
    attempts = []

    def handler(req):
        attempts.append(1)
        return error_response(503, "server overloaded")

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False and text
    assert len(attempts) == 3, "初始 1 次 + 重试 2 次"
    await service.aclose()


async def test_rate_limit_retries(enable_ai):
    attempts = []

    def handler(req):
        attempts.append(1)
        return error_response(429, "rate limited")

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False and text
    assert len(attempts) == 3
    await service.aclose()


async def test_model_not_exist_400_does_not_retry(enable_ai):
    """模型名错误返回 400，重试多少次都不会变好，应当立即放弃。

    顺带验证熔断：第一次失败后进入冷却，后续调用不再发请求。
    """
    attempts = []

    def handler(req):
        attempts.append(1)
        return error_response(400, "Model Not Exist")

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False and text
    assert len(attempts) == 1, "配置类错误不应重试"
    assert "400" in service.last_error

    # 冷却期内再调用：不再发请求，直接走本地模板
    text2, from_model2 = await call_comment(service)
    assert from_model2 is False and text2
    assert len(attempts) == 1, "冷却期内不应再发起请求"
    await service.aclose()


async def test_401_enters_cooldown(enable_ai):
    attempts = []

    def handler(req):
        attempts.append(1)
        return error_response(401, "Authentication Fails")

    service = make_service(handler)
    await call_comment(service)
    await call_comment(service)
    await call_comment(service)

    assert len(attempts) == 1, "401 后应进入冷却，避免每次抽卡都白跑网络"
    await service.aclose()


async def test_empty_content_falls_back(enable_ai):
    """200 但 content 为空（例如思考模式没关干净）同样要降级。"""

    def handler(req):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "", "reasoning_content": "让我想想…"}}]},
        )

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False and text, "不应把空的正文当成有效回复"
    await service.aclose()


async def test_malformed_json_falls_back(enable_ai):
    def handler(req):
        return httpx.Response(200, text="<html>not json</html>")

    service = make_service(handler)
    text, from_model = await call_comment(service)

    assert from_model is False and text
    await service.aclose()


async def test_recovery_after_cooldown(enable_ai, monkeypatch):
    """冷却结束后恢复调用，不会永久熔断。"""
    state = {"fail": True}

    def handler(req):
        if state["fail"]:
            return error_response(401, "Authentication Fails")
        return ok_response("恢复了")

    service = make_service(handler)
    await call_comment(service)
    assert service.available is False

    # 手动把冷却时间拨到过去，模拟冷却结束
    service._cooldown_until = 0.0
    state["fail"] = False

    text, from_model = await call_comment(service)
    assert from_model is True and text == "恢复了"
    await service.aclose()


# ===========================================================================
# 其它用途
# ===========================================================================
async def test_pool_intro_falls_back_without_key(monkeypatch):
    """没有密钥时用本地模板介绍卡池，且带上 UP 内容。"""
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    service = make_service(lambda req: ok_response())

    text = await service.pool_intro(_realish_pool())

    assert text
    assert "武器活动祈愿" in text
    assert "护摩之杖" in text, "本地模板应带上 UP 内容"
    await service.aclose()


async def test_pool_intro_is_cached(enable_ai):
    """卡池介绍按小时缓存，同一卡池不应反复调用模型。"""
    calls = []

    def handler(req):
        calls.append(1)
        return ok_response("欢迎来到本期祈愿")

    service = make_service(handler)
    pool = _realish_pool()

    first = await service.pool_intro(pool)
    second = await service.pool_intro(pool)

    assert first == second == "欢迎来到本期祈愿"
    assert len(calls) == 1, "第二次应命中缓存"
    await service.aclose()


async def test_history_summary_falls_back_on_zero_pulls(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key-not-real")

    class Stats:
        total_pulls = 0
        five_star_count = 0
        four_star_count = 0
        average_pulls_per_five_star = None
        max_pulls_for_five_star = None
        five_star_rate = 0.0

    service = make_service(lambda req: ok_response())
    text, from_model = await service.history_summary(Stats())

    assert from_model is False, "零记录时不必调用模型"
    assert "还没有" in text
    await service.aclose()


async def test_character_line_cleans_output(enable_ai):
    def handler(req):
        return ok_response("「往生堂，随时恭候。」 **——胡桃**")

    service = make_service(handler)
    text, from_model = await service.character_line("胡桃")

    assert from_model is True
    assert "**" not in text, "markdown 标记应被清理"
    assert "\n" not in text
    await service.aclose()


def _realish_pool():
    from app.models.enums import PoolType

    base = PoolRules.weapon()

    class RealishPool:
        id = 42
        name = "武器活动祈愿"
        pool_type = PoolType.WEAPON
        items = [FakeItem(1, "护摩之杖", 5, is_up=True)]

    for key, value in base.as_dict().items():
        setattr(RealishPool, key, value)
    return RealishPool


# ===========================================================================
# 本地降级文案
# ===========================================================================
def test_local_comment_mentions_the_five_star_name():
    from app.services.prompts import local_comment

    text = local_comment([{"name": "胡桃", "star": 5, "is_up": True, "is_pity": False}])
    assert "胡桃" in text


def test_local_comment_handles_all_three_star_batch():
    from app.services.prompts import local_comment

    text = local_comment([{"name": "冷刃", "star": 3, "is_up": False, "is_pity": False}])
    assert text


def test_render_tolerates_unknown_placeholders():
    """模板由管理员编辑，写错字段名不应把 AI 调用炸掉。"""
    from app.services.prompts import render

    out = render("你好 {name}，{不存在的字段}再见", name="旅行者")
    assert "旅行者" in out and "再见" in out


# ===========================================================================
# 注册/登录校验错误的人话提示
# ===========================================================================
@pytest.mark.parametrize(
    ("body", "expected_fragment"),
    [
        ({"username": "t4", "password": "Tester123"}, "用户名至少 3"),
        ({"username": "tester", "password": "abc"}, "密码至少 8"),
        ({"username": "tester"}, "请填写密码"),
        ({}, "请填写"),
    ],
)
def test_validation_errors_are_specific_not_generic(body, expected_fragment):
    """校验失败必须说明**具体**原因。

    统一回一句"请填写用户名与密码"是很糟的体验：用户明明填了，只是长度不对，
    却被告知"请填写"。这里锁死具体原因。
    """
    from pydantic import ValidationError

    from app.core.request_utils import describe_validation_error
    from app.schemas.auth import RegisterRequest

    try:
        RegisterRequest(**body)
    except ValidationError as exc:
        message = describe_validation_error(exc, "请填写用户名与密码")
    else:  # pragma: no cover - 上面的用例都应当校验失败
        pytest.fail("用例应当触发校验错误")

    assert expected_fragment in message
    assert message != "请填写用户名与密码" or expected_fragment == "请填写"


def test_validation_error_falls_back_when_no_errors():
    from app.core.request_utils import describe_validation_error

    class _FakeExc:
        def errors(self):
            return []

    assert describe_validation_error(_FakeExc(), "兜底文案") == "兜底文案"
