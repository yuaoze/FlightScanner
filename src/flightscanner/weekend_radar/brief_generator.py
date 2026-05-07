"""周末目的地种草文案生成器（AI + 规则降级）。

优先调用 DeepSeek API 生成有温度的种草文案；
API 不可用或调用失败时，降级到规则引擎生成基础文案。
"""

import json
import logging
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from flightscanner.interfaces import FlightInfo
from flightscanner.weekend_radar.destinations import (
    INTERNATIONAL_DESTINATIONS,
    VISA_INFO,
)

logger = logging.getLogger(__name__)


_WEEKEND_BRIEF_SYSTEM = """\
你是"打工人周末逃跑计划"的专属种草达人。用简洁有力、充满画面感的中文文案，
激发都市打工人的出行冲动。风格：接地气 + 有点幽默 + 重点突出低价和免签。

规则：
1. 文案不超过 80 字
2. 必须提及具体价格和"击败 XX% 均价"（若有历史数据）
3. 国际目的地必须在文案开头或结尾注明签证状态
4. 提及天气/美食/特色体验中的1-2项具体细节

返回 JSON（严格遵循 schema，不包含其他文字）：
{
  "headline": "爆款标题（15字以内，如：¥850 济州岛周末说走就走！）",
  "body":     "种草文案正文（50-80字）",
  "visa_note": "签证提示字符串，仅国际目的地，国内目的地返回 null",
  "beat_pct":  <整数，如 95（表示击败95%的历史均价），无历史数据时返回 null>,
  "tags":      ["标签1", "标签2"]
}
"""


def _rule_based_brief(
    destination: str,
    outbound_info: FlightInfo,
    return_info: FlightInfo,
    total_price: Decimal,
    historical_avg: Optional[Decimal],
    beat_pct: Optional[int],
    is_international: bool,
) -> Dict[str, Any]:
    """规则引擎降级文案（无需 API Key）。"""
    price_str = f"¥{int(total_price):,}"
    beat_str = f"，击败 {beat_pct}% 均价" if beat_pct and beat_pct > 0 else ""

    headline = f"{price_str} {destination} 周末往返{beat_str[:6] if beat_str else '特惠'}！"

    body_parts = [
        f"上海飞{destination}往返仅需 {price_str}{beat_str}。",
        f"周五 {outbound_info.departure_time} 出发，",
        f"周日 {return_info.departure_time} 返程，",
        "打工人的完美逃跑计划，说走就走！",
    ]
    body = "".join(body_parts)

    visa_note: Optional[str] = None
    if is_international:
        visa_data = VISA_INFO.get(destination)
        if visa_data:
            visa_note = visa_data["label"]

    tags: List[str] = ["周末短途", "直飞"]
    if is_international:
        tags.append("出境游")
        if destination in VISA_INFO and VISA_INFO[destination]["status"] in ("免签", "落地签"):
            tags.append("说走就走")
    if beat_pct and beat_pct >= 20:
        tags.append("超值低价")

    return {
        "headline": headline,
        "body": body,
        "visa_note": visa_note,
        "beat_pct": beat_pct,
        "tags": tags,
    }


def _enforce_actual_price(brief: Dict[str, Any], actual_price: Decimal) -> Dict[str, Any]:
    """AI 文案价格校正：用实际爬取价格替换标题中任何 ¥NNN 占位。

    AI 有时会虚构或四舍五入价格；此函数确保标题里的价格与
    实际 `total_price` 完全一致，防止"标题说 ¥599 卡片显示 ¥783"的情况。
    """
    headline = brief.get("headline", "")
    if headline:
        price_str = f"¥{int(actual_price):,}"
        # 匹配 ¥NNN 或 ¥N,NNN 等格式，统一替换为实际价格
        brief["headline"] = re.sub(r"¥[\d,]+", price_str, headline)
    return brief


async def generate_weekend_brief(
    destination: str,
    outbound_info: FlightInfo,
    return_info: FlightInfo,
    total_price: Decimal,
    historical_avg: Optional[Decimal],
    is_international: bool,
    api_key: Optional[str] = None,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
) -> Dict[str, Any]:
    """生成周末目的地种草文案，失败时返回规则引擎降级结果。

    Args:
        destination: 目的地城市名。
        outbound_info: 去程航班信息。
        return_info: 回程航班信息。
        total_price: 往返总价。
        historical_avg: 历史均价（可空）。
        is_international: 是否为国际/港澳台目的地。
        api_key: DeepSeek API Key（可空，空时直接使用规则降级）。
        base_url: DeepSeek API Base URL。
        model: 使用的模型名称。

    Returns:
        Dict with keys: headline, body, visa_note, beat_pct, tags.
    """
    beat_pct: Optional[int] = None
    if historical_avg and historical_avg > 0:
        if total_price < historical_avg:
            beat_pct = int((1 - float(total_price) / float(historical_avg)) * 100)

    # 无 API Key 时直接降级
    if not api_key:
        logger.debug("未配置 API Key，使用规则引擎生成文案：%s", destination)
        return _rule_based_brief(
            destination, outbound_info, return_info,
            total_price, historical_avg, beat_pct, is_international,
        )

    visa_data = VISA_INFO.get(destination)
    visa_desc = visa_data["label"] if visa_data else "无特殊签证要求（国内）"

    historical_avg_str = f"¥{int(historical_avg)}" if historical_avg else "暂无数据"
    beat_pct_line = f"击败历史均价：{beat_pct}%\n" if beat_pct else "击败历史均价：暂无数据\n"

    # 注意：不要在隐式字符串拼接中插入三元表达式。Python 会把
    # `"a" "b" if x else "c" "d"` 解析为 `("a" "b") if x else ("c" "d")`，
    # 导致价格、出发信息等关键行从 user_msg 中消失，进而让 AI 虚构价格。
    user_msg = (
        f"目的地：{destination}\n"
        f"往返总价：¥{int(total_price):,}\n"
        f"历史均价：{historical_avg_str}\n"
        f"{beat_pct_line}"
        f"去程：{outbound_info.departure_time} 从上海出发，{outbound_info.arrival_time} 抵达\n"
        f"回程：{return_info.departure_time} 从{destination}出发，{return_info.arrival_time} 回到上海\n"
        f"是否国际目的地：{'是' if is_international else '否'}\n"
        f"签证信息：{visa_desc}\n"
        "请生成种草文案。"
    )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _WEEKEND_BRIEF_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.8,
            max_tokens=400,
        )
        raw = response.choices[0].message.content or "{}"
        result = json.loads(raw)
        # 注入计算出的 beat_pct（如果 AI 返回的是 null）
        if result.get("beat_pct") is None and beat_pct:
            result["beat_pct"] = beat_pct
        # 确保标题价格与实际爬取价格一致（防止 AI 虚构价格）
        result = _enforce_actual_price(result, total_price)
        return result

    except Exception:
        logger.warning("AI 文案生成失败，降级到规则引擎：%s", destination, exc_info=True)
        return _rule_based_brief(
            destination, outbound_info, return_info,
            total_price, historical_avg, beat_pct, is_international,
        )
