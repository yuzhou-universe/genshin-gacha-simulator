"""DeepSeek Flash 集成。

设计要点：

1. **绝不抛出异常给调用方**。所有对外方法都返回字符串：成功返回模型输出，
   任何失败都返回本地模板文案。抽卡主流程因此完全不受 AI 可用性影响。
2. **结构化保证降级**：抽卡接口不调用本模块，AI 点评由前端拿到结果后另行
   请求。所以"AI 挂了不影响抽卡"不是靠 try/except 兜出来的，而是拓扑如此。
3. **只对可重试的错误重试**。429/5xx 重试；400（模型名错）、401（密钥错）、
   402（余额不足）、422（参数错）重试多少次都不会变好，立即放弃并进入冷却。
4. **必须显式关闭 thinking**。deepseek-flash 默认开启思考模式，生成"一句点评"
   这种短文本会把延迟和费用都消耗在思维链上。
5. **隐私**：调用方只能传白名单字段（卡池名、物品名、星级、抽数），
   函数签名里没有 user 对象，从结构上杜绝把用户名/ID/token 发出去。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from app.core.config import settings
from app.services import prompts

logger = logging.getLogger("app.ai")

#: 值得重试的状态码。400/401/402/422 不在其中 —— 它们代表配置或参数错误，
#: 重试无意义，只会拖慢响应并浪费配额。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: 命中这些状态码说明配置有问题，进入冷却期，避免每次抽卡都白跑一趟网络。
FATAL_STATUS = frozenset({400, 401, 402, 403, 422})

#: 配置错误后的冷却时长（秒）
COOLDOWN_SECONDS = 300

#: 卡池介绍的缓存时长（秒）。卡池内容很少变，没必要每次打开页面都调一次模型。
POOL_INTRO_TTL = 3600


class AIService:
    """DeepSeek Flash 客户端。无密钥时 ``available`` 为 False，全部走本地模板。"""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        #: 仅供测试注入 httpx.MockTransport，生产环境为 None
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._cooldown_until: float = 0.0
        self._intro_cache: dict[int, tuple[float, str]] = {}
        self._last_error: str = ""
        #: 记录实际发出的请求体，供测试断言隐私与 thinking 开关
        self.sent_payloads: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """是否具备调用条件（有密钥且不在冷却期）。"""
        if not settings.ai_enabled:
            return False
        return time.monotonic() >= self._cooldown_until

    @property
    def last_error(self) -> str:
        """最近一次失败原因，供后台展示。不含任何密钥信息。"""
        return self._last_error

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # 连接与读取超时分开：连接超时短一些，读取超时留给模型生成
            timeout = httpx.Timeout(
                connect=min(5.0, settings.deepseek_timeout),
                read=settings.deepseek_timeout,
                write=5.0,
                pool=5.0,
            )
            self._client = httpx.AsyncClient(
                base_url=settings.deepseek_base_url,
                timeout=timeout,
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ------------------------------------------------------------------
    # 底层调用
    # ------------------------------------------------------------------
    def _payload(self, system: str, user: str, max_tokens: int, temperature: float) -> dict:
        body: dict[str, Any] = {
            "model": settings.deepseek_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if settings.deepseek_disable_thinking:
            # flash 默认开启思考模式；短文本生成必须关掉。
            body["thinking"] = {"type": "disabled"}
        return body

    async def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int = 200,
        temperature: float = 1.0,
    ) -> str | None:
        """调用模型。成功返回文本，任何失败返回 None（**不抛异常**）。"""
        if not self.available:
            return None

        client = await self._get_client()
        body = self._payload(
            system_prompt or prompts.SYSTEM_PROMPT, user_prompt, max_tokens, temperature
        )
        self.sent_payloads.append(body)
        attempts = max(1, settings.deepseek_max_retries + 1)

        for attempt in range(attempts):
            try:
                response = await client.post("/chat/completions", json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self._last_error = f"网络异常: {type(exc).__name__}"
                if attempt + 1 < attempts:
                    await self._backoff(attempt)
                    continue
                logger.warning("DeepSeek 调用失败（网络）: %s", type(exc).__name__)
                return None

            if response.status_code in RETRYABLE_STATUS:
                self._last_error = f"HTTP {response.status_code}"
                if attempt + 1 < attempts:
                    await self._backoff(attempt, response=response)
                    continue
                logger.warning("DeepSeek 重试耗尽，最后状态码 %s", response.status_code)
                return None

            if response.status_code in FATAL_STATUS:
                self._enter_cooldown(response)
                return None

            if response.status_code >= 400:
                self._last_error = f"HTTP {response.status_code}"
                logger.warning("DeepSeek 返回 %s", response.status_code)
                return None

            text = self._extract(response)
            if text:
                self._last_error = ""
                return text

            # 200 但内容为空（例如思考模式没关干净），当成失败降级
            self._last_error = "模型返回了空内容"
            if attempt + 1 < attempts:
                await self._backoff(attempt)
                continue
            return None

        return None

    @staticmethod
    def _extract(response: httpx.Response) -> str | None:
        try:
            data = response.json()
        except ValueError:
            return None
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()
        if content:
            return content
        # 只有 reasoning_content 说明思考模式没关掉，同样视为不可用
        return None

    async def _backoff(self, attempt: int, response: httpx.Response | None = None) -> None:
        """指数退避 + 抖动。

        Retry-After 头官方并未承诺提供，所以只作为"若存在则采纳"的优化，
        不能依赖它。抖动是必要的：大量客户端同时被限流时，固定间隔会让它们
        在同一时刻一起重试，形成二次冲击。
        """
        delay = min(0.4 * (2**attempt), 3.0)
        if response is not None:
            header = response.headers.get("retry-after")
            if header:
                try:
                    delay = min(float(header), 5.0)
                except ValueError:
                    pass
        await asyncio.sleep(delay + random.uniform(0, 0.25))

    def _enter_cooldown(self, response: httpx.Response) -> None:
        """配置类错误 → 冷却一段时间，不再每次抽卡都白跑网络。"""
        detail = ""
        try:
            payload = response.json()
            detail = str((payload.get("error") or {}).get("message") or "")[:120]
        except ValueError:
            detail = response.text[:120]

        self._last_error = f"HTTP {response.status_code}: {detail}"
        self._cooldown_until = time.monotonic() + COOLDOWN_SECONDS

        hint = ""
        if response.status_code == 400 and "model" in detail.lower():
            hint = f"（请检查 DEEPSEEK_MODEL，当前为 {settings.deepseek_model!r}）"
        elif response.status_code == 401:
            hint = "（请检查 DEEPSEEK_API_KEY）"

        logger.warning(
            "DeepSeek 配置错误，暂停 AI 功能 %d 秒: %s %s",
            COOLDOWN_SECONDS,
            self._last_error,
            hint,
        )

    # ------------------------------------------------------------------
    # 业务方法：全部保证返回可用文案
    # ------------------------------------------------------------------
    async def comment_on_pull(
        self,
        *,
        pool_name: str,
        count: int,
        items: list[dict[str, Any]],
        until_hard_pity: int,
        guaranteed: bool,
        templates: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """抽卡点评。返回 ``(文案, 是否来自模型)``。

        入参全部是游戏内公开信息，不含任何用户身份字段。
        """
        fallback = prompts.local_comment(items)

        if not self.available:
            return fallback, False

        has_five = any(i.get("star") == 5 for i in items)
        # 语气指示在 Python 侧组装，模板里不放条件分支 —— 用注释标记做分支
        # 会把噪声喂给模型，也让管理员难以读懂模板。
        tone = (
            "这次出了五星，语气要兴奋一些。" if has_five else "这次没有出五星，可以善意地调侃一下。"
        )
        prompt = prompts.render(
            prompts.get_template(templates, "pull_comment"),
            pool_name=pool_name[:60],
            count=count,
            items=prompts.describe_items(items),
            until_hard_pity=until_hard_pity,
            guarantee_note=("该卡池处于大保底状态，下一个五星必定为 UP。" if guaranteed else ""),
            tone_note=tone,
        )
        text = await self.complete(prompt, max_tokens=140)
        if not text:
            return fallback, False

        text = self._clean(text)
        return (text, True) if text else (fallback, False)

    async def pool_intro(self, pool: Any, templates: dict[str, Any] | None = None) -> str:
        """卡池介绍（带缓存）。"""
        cached = self._intro_cache.get(pool.id)
        if cached and time.monotonic() - cached[0] < POOL_INTRO_TTL:
            return cached[1]

        fallback = prompts.local_pool_intro(pool)
        if not self.available:
            return fallback

        ups = [i.name for i in pool.items if i.star == 5 and i.is_up]
        rules = _rules_of(pool)
        prompt = prompts.render(
            prompts.get_template(templates, "pool_intro"),
            pool_name=pool.name[:60],
            pool_type=prompts.POOL_TYPE_LABELS.get(pool.pool_type, "祈愿"),
            base_rate_5=f"{rules.base_rate_5 * 100:.1f}%",
            pity_desc=(
                f"{rules.hard_pity_5} 抽必出五星"
                + ("，含软保底递增" if rules.soft_pity_enabled else "")
            ),
            up_items="、".join(ups) if ups else "无 UP",
        )

        text = await self.complete(prompt, max_tokens=200, temperature=1.1)
        if not text:
            return fallback

        text = self._clean(text)
        if text:
            self._intro_cache[pool.id] = (time.monotonic(), text)
            return text
        return fallback

    async def history_summary(
        self, stats: Any, templates: dict[str, Any] | None = None
    ) -> tuple[str, bool]:
        """历史记录总结。"""
        fallback = prompts.local_history_summary(stats)
        if not self.available or stats.total_pulls == 0:
            return fallback, False

        prompt = prompts.render(
            prompts.get_template(templates, "history_summary"),
            total_pulls=stats.total_pulls,
            five_star_count=stats.five_star_count,
            four_star_count=stats.four_star_count,
            average_pulls=stats.average_pulls_per_five_star or "暂无",
            max_pulls=stats.max_pulls_for_five_star or "暂无",
            five_star_rate=f"{stats.five_star_rate * 100:.2f}%",
        )
        text = await self.complete(prompt, max_tokens=120)
        if not text:
            return fallback, False
        text = self._clean(text)
        return (text, True) if text else (fallback, False)

    async def character_line(
        self, item_name: str, templates: dict[str, Any] | None = None
    ) -> tuple[str, bool]:
        """角色/武器台词。"""
        fallback = prompts.local_character_line(item_name)
        if not self.available:
            return fallback, False

        prompt = prompts.render(
            prompts.get_template(templates, "character_line"),
            item_name=item_name[:40],
        )
        text = await self.complete(prompt, max_tokens=80)
        if not text:
            return fallback, False
        text = self._clean(text)
        return (text, True) if text else (fallback, False)

    # ------------------------------------------------------------------
    @staticmethod
    def _clean(text: str) -> str:
        """清理模型输出：去掉换行、markdown 包裹、超长内容。"""
        text = text.strip().strip('"').strip("「」").strip()
        text = text.replace("\n", " ").strip()
        # 去掉可能出现的 markdown 强调标记
        text = text.replace("**", "").replace("##", "").strip()
        return text[:180]


def _rules_of(pool: Any):
    from app.services.pool_rules import PoolRules

    return PoolRules.from_pool(pool)


#: 全局单例。httpx 客户端内部有连接池，复用才能省掉重复握手。
ai_service = AIService()


def get_ai_service() -> AIService:
    return ai_service
