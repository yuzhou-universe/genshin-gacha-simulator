"""提示词模板与本地降级文案。

模板可在后台「全局配置」里覆盖（存 AppSetting 的 ``prompt_templates`` 键），
未覆盖时使用这里的默认值。

**隐私约束**：提示词只允许包含卡池名、物品名、星级、抽数、保底数这类
游戏内公开信息。模型调用链路上绝不出现用户名、用户 ID、密码哈希、会话
Cookie 或 IP —— 这一点由 ``ai_service`` 的函数签名从结构上保证：调用方
只能传白名单字段，没有把整个 user 对象塞进去的口子。
"""

from __future__ import annotations

import random
from typing import Any

from app.models.enums import PoolType

# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "你是一位熟悉《原神》祈愿系统的助手，说话风格轻快、有角色感，"
    "熟悉提瓦特的世界观与角色。用简体中文回答，只输出正文，"
    "不要解释你的推理过程，不要用 markdown 标记。"
)

# ---------------------------------------------------------------------------
# 用户提示词模板
# ---------------------------------------------------------------------------
#: 抽卡点评
TEMPLATE_PULL_COMMENT = """玩家在「{pool_name}」进行了一次{count}连抽。

本次结果：
{items}

当前距离五星保底还剩 {until_hard_pity} 抽。{guarantee_note}{tone_note}

请用一到两句话点评这次抽卡结果，可以带一点角色台词的味道。
总长度不要超过 60 个字。"""

#: 卡池介绍
TEMPLATE_POOL_INTRO = """请为《原神》风格的卡池「{pool_name}」写一段介绍。

卡池类型：{pool_type}
五星基础概率：{base_rate_5}
保底机制：{pity_desc}
UP 内容：{up_items}

要求：两到三句话，60 字以内，营造祈愿氛围，不要罗列数字。只输出正文。"""

#: 历史总结
TEMPLATE_HISTORY_SUMMARY = """请根据以下祈愿统计，给玩家写一句总结或祝福。

总抽数：{total_pulls}
五星数量：{five_star_count}
四星数量：{four_star_count}
平均出金抽数：{average_pulls}
最非的一次出金：{max_pulls} 抽
出金率：{five_star_rate}

要求：一句话，50 字以内，可以调侃也可以祝贺，只输出正文。"""

#: 角色/武器台词
TEMPLATE_CHARACTER_LINE = """请以《原神》中「{item_name}」的口吻，对刚刚抽到自己的玩家说一句话。

要求：一句话，30 字以内，符合该角色或武器的设定与语气，只输出正文。"""

DEFAULT_TEMPLATES: dict[str, str] = {
    "pull_comment": TEMPLATE_PULL_COMMENT,
    "pool_intro": TEMPLATE_POOL_INTRO,
    "history_summary": TEMPLATE_HISTORY_SUMMARY,
    "character_line": TEMPLATE_CHARACTER_LINE,
}

POOL_TYPE_LABELS = {
    PoolType.CHARACTER: "角色活动祈愿（限定 UP）",
    PoolType.WEAPON: "武器活动祈愿（限定 UP，可定轨）",
    PoolType.STANDARD: "常驻祈愿（无 UP）",
    PoolType.BEGINNER: "新手祈愿（限 20 抽）",
}


def get_template(overrides: dict[str, Any] | None, key: str) -> str:
    """取模板：后台覆盖优先，缺失或类型不对时回落到默认值。"""
    if overrides:
        value = overrides.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return DEFAULT_TEMPLATES[key]


def render(template: str, **kwargs: Any) -> str:
    """渲染模板。

    模板来自数据库、由管理员编辑，字段名写错是常事 —— 用 format_map 加
    兜底字典，让缺失的占位符渲染成空串而不是把整个 AI 调用炸掉。
    """

    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return ""

    return template.format_map(_Safe(kwargs)).strip()


def describe_items(items: list[dict[str, Any]]) -> str:
    """把抽卡结果渲染成提示词里的一段文本。

    **只取白名单字段**：名称、星级、是否 UP、是否保底。即便上游多传了字段，
    这里也不会带出去。
    """
    lines = []
    for i in items:
        name = str(i.get("name", ""))[:40]
        star = int(i.get("star", 0))
        tags = []
        if i.get("is_up"):
            tags.append("UP")
        if i.get("is_pity"):
            tags.append("保底")
        suffix = f"（{'、'.join(tags)}）" if tags else ""
        lines.append(f"- {name}：{star}星{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 本地降级文案
# ---------------------------------------------------------------------------
#: 五星（UP）
FALLBACK_FIVE_UP = [
    "金光乍现，{name}应召而来——这一发，值了。",
    "命运眷顾了你，{name}愿意与你同行。",
    "「看来我们的相遇并非偶然。」——{name}",
    "十连出金的快乐，只有抽过的人才懂。{name}已加入队伍。",
]

#: 五星（歪了 / 常驻）
FALLBACK_FIVE_OFF = [
    "虽然歪了，但{name}也是难得的好运气。",
    "{name}不请自来——至少，是个五星。",
    "结果不算完美，可{name}的实力毋庸置疑。",
    "大保底已经在路上了，下次一定。这一发先收下{name}。",
]

#: 四星为主
FALLBACK_FOUR = [
    "四星打底，运气正在积累——保底越来越近了。",
    "紫色光芒闪过，{name}入账。距离金光又近一步。",
    "稳扎稳打的一次，{name}加入队伍。",
    "虽然没出金，但{name}也算有所收获。",
]

#: 全三星
FALLBACK_THREE = [
    "全是三星……别灰心，概率正在为你积蓄。",
    "这一发沉了，但每一次都在推进保底计数。",
    "三星保底，至少说明——离出金又近了一抽。",
    "深呼吸，垫刀也是一种修行。",
]

#: 保底触发
FALLBACK_PITY = [
    "保底触发，{name}如约而至。坚持总会有回报。",
    "攒了这么久的运气，终于兑现——{name}前来报到。",
]


def local_comment(
    items: list[dict[str, Any]],
    *,
    rng: random.Random | None = None,
) -> str:
    """本地模板点评。AI 不可用时使用，也让结果不因网络问题而空白。"""
    rng = rng or random
    five_up = [i for i in items if i.get("star") == 5 and i.get("is_up")]
    five_off = [i for i in items if i.get("star") == 5 and not i.get("is_up")]
    pity_five = [i for i in items if i.get("star") == 5 and i.get("is_pity")]
    fours = [i for i in items if i.get("star") == 4]

    if five_up or five_off:
        target = (five_up or five_off)[0]
        pool = FALLBACK_PITY if pity_five else (FALLBACK_FIVE_UP if five_up else FALLBACK_FIVE_OFF)
        return rng.choice(pool).format(name=target.get("name", "五星"))
    if fours:
        return rng.choice(FALLBACK_FOUR).format(name=fours[0].get("name", "四星"))
    return rng.choice(FALLBACK_THREE)


def local_pool_intro(pool: Any) -> str:
    """本地卡池介绍。"""
    label = POOL_TYPE_LABELS.get(pool.pool_type, "祈愿")
    ups = [i.name for i in pool.items if i.star == 5 and i.is_up]
    if ups:
        return (
            f"「{pool.name}」现已开放——{label}。"
            f"本期 UP：{'、'.join(ups)}。愿风神护佑你的每一次祈愿。"
        )
    return f"「{pool.name}」常驻开放——{label}。愿你的每一抽都不留遗憾。"


def local_history_summary(stats: Any) -> str:
    """本地历史总结。"""
    total = stats.total_pulls
    five = stats.five_star_count
    if total == 0:
        return "还没有祈愿记录，去卡池试试手气吧。"

    rate = five / total
    if five == 0:
        return f"已经抽了 {total} 抽还没有出金，运气正在路上——保底不会骗人。"
    if rate >= 0.025:
        return f"{total} 抽拿下 {five} 个五星，这运气可以去璃月港摆摊了。"
    if stats.average_pulls_per_five_star and stats.average_pulls_per_five_star > 75:
        return f"{total} 抽 {five} 金，平均 {stats.average_pulls_per_five_star} 抽一金——非酋认证，但保底爱你。"
    return f"{total} 抽收获 {five} 个五星，总体符合预期，继续加油。"


def local_character_line(name: str) -> str:
    return f"「{name}」，愿意与你同行。"
