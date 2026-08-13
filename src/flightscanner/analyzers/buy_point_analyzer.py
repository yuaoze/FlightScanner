"""买点分析器：对已成交的买入记录生成 LLM 复盘分析。

仿照进化引擎 G3 RCA 的调用模式（openai AsyncClient + json_object），
无 API key 或调用失败时降级为规则模板文本，保证分析功能始终可用。
"""

import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
你是一个专业的机票购买复盘分析师。用户在某个时点买入了机票，系统持续监控了买入后的价格走势。
请根据提供的买入信息与买后价格数据，输出 JSON 格式的复盘分析，严格遵循以下 schema：
{
  "verdict_comment": "一句话总结这次买入时机的评价（30字以内）",
  "timing_assessment": "对买入时机的分析：结合距起飞天数、买后价格走势说明买早了/买晚了/时机恰当（100字以内）",
  "key_lessons": ["可复用的经验1（40字以内）", "可复用的经验2（40字以内，可少于2条）"]
}
重要限制：这是单笔买入之后的案例观察，不能由此断言整条航线的全局最佳买点。
经验必须使用"本次案例观察到……"等受限表述，不得声称"该航线提前N天价格最低"或生成节假日/季节规律。
若数据不足，必须明确说无法评价，key_lessons 返回空列表。
"""


def _rule_based_analysis(
    *,
    route_label: str,
    purchase_price: float,
    days_before_departure: Optional[int],
    post_min_price: Optional[float],
    final_price: Optional[float],
    regret_cost: Optional[float],
    savings_vs_final: Optional[float],
    verdict: Optional[str],
    analysis_status: str,
    sample_size: int,
) -> Dict[str, Any]:
    """规则降级分析：根据统计数据生成模板化复盘文本。"""
    if analysis_status == "insufficient" or verdict is None:
        return {
            "verdict_comment": "数据不足，暂无法评价这次买入时机",
            "timing_assessment": (
                f"买后仅有 {sample_size} 个独立采集批次，"
                "至少需要 2 个可比批次才能计算价格走势。"
            ),
            "key_lessons": [],
            "_source": "rule_based",
        }
    verdict_texts = {
        "excellent": "买点优秀：买入后价格未出现明显更低，且低于起飞前最终价",
        "good": "买点良好：买入价接近买后低位，时机把握较好",
        "fair": "买点一般：买入后出现过更低价，有一定多付成本",
        "poor": "买点偏差：买入后价格明显下行，多付较多",
    }
    verdict_comment = verdict_texts.get(verdict, "买点评价数据不足")

    parts: List[str] = []
    if days_before_departure is not None:
        parts.append(f"买入时距起飞 {days_before_departure} 天")
    parts.append(f"买入价 ¥{purchase_price:.0f}")
    if post_min_price is not None:
        parts.append(f"买后最低 ¥{post_min_price:.0f}")
    if final_price is not None:
        parts.append(f"起飞前最终 ¥{final_price:.0f}")
    if regret_cost is not None and regret_cost > 0:
        parts.append(f"多付 ¥{regret_cost:.0f}")
    if savings_vs_final is not None and savings_vs_final > 0:
        parts.append(f"较最终价节省 ¥{savings_vs_final:.0f}")
    timing_assessment = "；".join(parts) + "。"

    lessons: List[str] = []
    if regret_cost is not None and regret_cost > 0 and days_before_departure is not None:
        lessons.append(
            f"{route_label}：本次案例提前 {days_before_departure} 天买入后仍降 ¥{regret_cost:.0f}"
        )
    elif savings_vs_final is not None and savings_vs_final > 0 and days_before_departure is not None:
        lessons.append(
            f"{route_label}：本次案例提前 {days_before_departure} 天买入后价格上涨"
        )
    if not lessons:
        lessons.append(f"{route_label}：本次案例未观察到明显价格信号")

    return {
        "verdict_comment": verdict_comment,
        "timing_assessment": timing_assessment,
        "key_lessons": lessons,
        "_source": "rule_based",
    }


async def generate_buy_point_analysis_async(
    *,
    route_label: str,
    purchase_price: float,
    purchased_at: str,
    days_before_departure: Optional[int],
    target_date: Optional[date],
    post_min_price: Optional[float],
    post_max_price: Optional[float],
    final_price: Optional[float],
    regret_cost: Optional[float],
    savings_vs_final: Optional[float],
    verdict: Optional[str],
    sample_size: int = 0,
    coverage_hours: Optional[float] = None,
    data_quality: str = "insufficient",
    analysis_status: str = "insufficient",
    comparison_scope: str = "route",
    api_key: Optional[str] = None,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
) -> Dict[str, Any]:
    """生成买点复盘分析（异步版，供调度器协程内使用）。

    Args:
        route_label: 路线字符串，如 "上海 → 东京"。
        purchase_price: 实际成交价格。
        purchased_at: 买入时间（可读字符串）。
        days_before_departure: 买入时距起飞天数。
        target_date: 航班起飞日期。
        post_min_price: 买后最低价。
        post_max_price: 买后最高价。
        final_price: 起飞前最后采集价。
        regret_cost: 多付金额。
        savings_vs_final: 相对最终价节省。
        verdict: 规则计算的评级 excellent|good|fair|poor。
        api_key: DeepSeek API key（为空则降级规则模板）。
        base_url: API 基础 URL。
        model: 模型名称。

    Returns:
        包含 verdict_comment / timing_assessment / key_lessons 的字典，
        ``_source`` 标记 "deepseek" 或 "rule_based"。
    """
    fallback = _rule_based_analysis(
        route_label=route_label,
        purchase_price=purchase_price,
        days_before_departure=days_before_departure,
        post_min_price=post_min_price,
        final_price=final_price,
        regret_cost=regret_cost,
        savings_vs_final=savings_vs_final,
        verdict=verdict,
        analysis_status=analysis_status,
        sample_size=sample_size,
    )

    # 数据不足时不调用 LLM，避免从空数据中幻觉出经验。
    if analysis_status == "insufficient" or verdict is None:
        return fallback

    if not api_key:
        logger.info("买点分析降级到规则模板：api_key 未配置")
        return fallback

    user_prompt = (
        f"路线：{route_label}\n"
        f"买入时间：{purchased_at}\n"
        f"买入价：¥{purchase_price:.0f}\n"
        f"买入时距起飞天数：{days_before_departure if days_before_departure is not None else '未知'}\n"
        f"航班起飞日期：{target_date or '未知'}\n"
        f"买后最低价：{f'¥{post_min_price:.0f}' if post_min_price is not None else '无数据'}\n"
        f"买后最高价：{f'¥{post_max_price:.0f}' if post_max_price is not None else '无数据'}\n"
        f"起飞前最终价：{f'¥{final_price:.0f}' if final_price is not None else '无数据'}\n"
        f"多付成本（相对买后最低）：{f'¥{regret_cost:.0f}' if regret_cost is not None else '无法计算'}\n"
        f"相对最终价节省：{f'¥{savings_vs_final:.0f}' if savings_vs_final is not None else '无法计算'}\n"
        f"独立批次数：{sample_size}\n"
        f"覆盖时长：{f'{coverage_hours:.1f} 小时' if coverage_hours is not None else '未知'}\n"
        f"数据质量：{data_quality}\n"
        f"分析状态：{analysis_status}\n"
        f"比较口径：{comparison_scope}\n"
        f"系统评级：{verdict or '无法评价'}\n\n"
        "请输出复盘分析 JSON。"
    )

    try:
        import openai  # lazy import

        client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
        finally:
            await client.close()

        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        lessons = result.get("key_lessons")
        return {
            "verdict_comment": result.get("verdict_comment") or fallback["verdict_comment"],
            "timing_assessment": result.get("timing_assessment") or fallback["timing_assessment"],
            "key_lessons": [str(x) for x in lessons] if isinstance(lessons, list) and lessons else fallback["key_lessons"],
            "_source": "deepseek",
        }
    except Exception as exc:
        logger.warning("买点分析 LLM 调用失败，降级规则模板：%s", exc)
        return fallback
