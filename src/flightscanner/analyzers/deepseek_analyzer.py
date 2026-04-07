"""DeepSeek AI-powered price briefing analyzer.

Generates structured price trend briefings using the DeepSeek chat API
(OpenAI-compatible format).  Automatically falls back to RuleBasedAnalyzer
when the API key is missing or the API call fails after retries.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from flightscanner.interfaces import FlightPrice
from flightscanner.analyzers.rule_based_analyzer import RuleBasedAnalyzer, _batch_min_prices

logger = logging.getLogger(__name__)

# ── AI output JSON schema ─────────────────────────────────────────────────────
#
# {
#   "trend":          "上涨|下跌|震荡|稳定",
#   "confidence":     0.0,           # 0.0 ~ 1.0
#   "key_factors":    ["因素1", ...],
#   "prediction_7d":  "未来7天价格走势描述",
#   "recommendation": "建议操作（立即购买/继续观望/等待节后）",
#   "alert_level":    "low|medium|high"
# }

_SYSTEM_PROMPT = """\
你是一个专业的机票价格分析师，擅长根据历史采集数据预测价格走势并给出购票建议。

分析时请考虑以下因素：
1. 价格序列的趋势（近期是否持续上涨/下跌/震荡）
2. 出行日期距今的天数（越临近出行，价格越难降）
3. 节假日、黄金周等特殊因素
4. 当前价格相对于历史均价的位置

请以 JSON 格式返回分析结果，严格遵循以下 schema，不要包含任何额外文字：
{
  "trend": "上涨|下跌|震荡|稳定",
  "confidence": <0.0~1.0 之间的浮点数>,
  "key_factors": ["影响因素1", "影响因素2"],
  "prediction_7d": "一段中文描述，说明未来7天价格走势",
  "recommendation": "一句话购票建议，例如：立即购买/继续观望/等待节后",
  "alert_level": "low|medium|high",
  "action": "Buy 或 Wait（基于当前价格和走势的购票建议）",
  "reason": "一句话说明给出此建议的核心原因"
}
"""


class DeepSeekBriefingAnalyzer:
    """AI price briefing analyzer using DeepSeek API.

    Constructs a prompt from the price history and route information,
    calls the DeepSeek chat completion endpoint, and parses the JSON response.

    Args:
        api_key: DeepSeek API key (must start with "sk-").
        base_url: API base URL (default: "https://api.deepseek.com").
        model: Model name (default: "deepseek-chat").
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
    ) -> None:
        import openai  # local import to avoid hard dep when API not used

        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def generate_brief(
        self,
        price_history: List[FlightPrice],
        target_date: date,
        route_label: str,
        evolution_context: str = "",
    ) -> Dict[str, Any]:
        """Call DeepSeek API to generate a price briefing.

        Args:
            price_history: Recent price records (should be ≥ 7 for best results).
            target_date:   Target departure date.
            route_label:   Human-readable route string, e.g. "北京 → 东京".
            evolution_context: Optional G4 evolved context string injected as
                               system message suffix to incorporate historical
                               prediction errors.

        Returns:
            Parsed JSON dict conforming to the AI output schema.

        Raises:
            Exception: On API error after all retries are exhausted.
        """
        # ── 构建价格序列 ───────────────────────────────────────────────────────
        sorted_history = sorted(price_history, key=lambda fp: fp.scraped_at)
        price_series = [
            {
                "time": fp.scraped_at.strftime("%Y-%m-%d %H:%M"),
                "price": float(fp.price),
                "source": fp.source,
            }
            for fp in sorted_history[-30:]  # 最多取最近 30 条
        ]

        days_until = (target_date - date.today()).days

        user_prompt = (
            f"路线：{route_label}\n"
            f"出行日期：{target_date}（距今 {days_until} 天）\n"
            f"价格序列（共 {len(price_series)} 条，按时间升序）：\n"
            f"{json.dumps(price_series, ensure_ascii=False, indent=2)}\n\n"
            "请根据以上数据生成价格简报。"
        )

        # ── G4：若有历史失误上下文则追加到 system message ────────────────────
        system_content = _SYSTEM_PROMPT
        if evolution_context:
            system_content = system_content + "\n\n" + evolution_context

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        return result

    def generate_brief_sync(
        self,
        price_history: List[FlightPrice],
        target_date: date,
        route_label: str,
        evolution_context: str = "",
    ) -> Dict[str, Any]:
        """Synchronous wrapper around :meth:`generate_brief`.

        Runs the coroutine in a new event loop.  Do not call from within a
        running event loop (e.g. inside an ``async def``).

        Args:
            price_history: Recent price records.
            target_date:   Target departure date.
            route_label:   Human-readable route string.
            evolution_context: Optional G4 evolved context string.

        Returns:
            Parsed JSON dict.
        """
        return asyncio.run(
            self.generate_brief(price_history, target_date, route_label, evolution_context)
        )


# ── Rule-based fallback brief ─────────────────────────────────────────────────

def _rule_based_brief(
    price_history: List[FlightPrice],
    target_date: date,
) -> Dict[str, Any]:
    """Generate a simple rule-based briefing as fallback.

    Args:
        price_history: Price history list.
        target_date:   Target departure date.

    Returns:
        Dict conforming to the AI output schema.
    """
    analyzer = RuleBasedAnalyzer()
    trend = analyzer.predict_trend(price_history, target_date)

    trend_map = {"down": "下跌", "up": "上涨", "stable": "稳定"}
    alert_map = {"down": "low", "up": "high", "stable": "medium"}

    prices = [float(fp.price) for fp in price_history]
    batch_mins = _batch_min_prices(price_history)
    avg = median(batch_mins) if batch_mins else (prices[0] if prices else 0.0)
    current = float(price_history[-1].price) if price_history else 0.0
    diff_pct = (current - avg) / avg * 100 if avg else 0.0

    key_factors = [f"当前价格 ¥{current:.0f}，30天均价 ¥{avg:.0f}"]
    if diff_pct < -5:
        key_factors.append(f"低于均价 {abs(diff_pct):.1f}%")
    elif diff_pct > 5:
        key_factors.append(f"高于均价 {diff_pct:.1f}%")

    days_until = (target_date - date.today()).days
    if days_until <= 7:
        key_factors.append("出行日期临近，价格波动空间有限")

    return {
        "trend": trend_map.get(trend.direction, "稳定"),
        "confidence": round(trend.confidence, 2),
        "key_factors": key_factors,
        "prediction_7d": trend.recommendation,
        "recommendation": (
            "立即购买" if trend.direction == "down" and trend.confidence > 0.5
            else ("等待观望" if trend.direction == "up" else "可继续观望")
        ),
        "alert_level": alert_map.get(trend.direction, "medium"),
        "action": "Buy" if trend.direction == "down" else "Wait",
        "reason": "规则引擎：" + trend.recommendation,
        "_source": "rule_based",
    }


def generate_brief_with_fallback(
    price_history: List[FlightPrice],
    target_date: date,
    route_label: str,
    api_key: Optional[str] = None,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
    evolution_context: str = "",
) -> Dict[str, Any]:
    """Generate a price briefing, falling back to rule-based analysis when needed.

    Falls back to :func:`_rule_based_brief` when:
    - ``api_key`` is empty or None
    - fewer than 7 historical records are available
    - DeepSeek API call fails after 3 retries

    Args:
        price_history: Historical price records.
        target_date:   Target departure date.
        route_label:   Human-readable route string, e.g. "北京 → 东京".
        api_key:       DeepSeek API key.
        base_url:      API base URL.
        model:         Model name.
        evolution_context: Optional G4 evolved context string injected into the
                           system prompt for historical error awareness.

    Returns:
        Dict conforming to the AI output schema.  A ``"_source"`` key
        indicates ``"deepseek"`` or ``"rule_based"``.
    """
    if not api_key or len(price_history) < 7:
        reason = "api_key 未配置" if not api_key else f"历史记录不足（{len(price_history)} < 7）"
        logger.info("AI 简报降级到规则引擎：%s", reason)
        brief = _rule_based_brief(price_history, target_date)
        brief["_source"] = "rule_based"
        return brief

    try:
        analyzer = DeepSeekBriefingAnalyzer(
            api_key=api_key, base_url=base_url, model=model
        )
        brief = analyzer.generate_brief_sync(
            price_history, target_date, route_label, evolution_context
        )
        brief["_source"] = "deepseek"
        return brief
    except Exception as exc:
        logger.warning("DeepSeek API 调用失败，降级到规则引擎：%s", exc)
        brief = _rule_based_brief(price_history, target_date)
        brief["_source"] = "rule_based"
        return brief
