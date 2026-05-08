"""AI 进化引擎：4 齿轮闭环自进化预测系统。

G1 执行器：采集后记录结构化预测（世界状态快照）。
G2 监控器：对已到期预测执行回测，计算 Pain Index。
         触发条件：① 航班已出发  ② 预测写入已满 7 天（利用 7 日价格窗口评估走势准确性）。
G3 诊断器：高痛失误 RCA（第二次 LLM 调用）。
G4 进化器：动态注入历史失误上下文 + 信誉 badge。
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from flightscanner.models.database import AIPredictionLog, PriceHistory, Route

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

HIGH_PAIN_THRESHOLD = 200.0           # CNY，痛苦指数超过此值触发 G3 RCA
FATAL_LOSS_THRESHOLD = 300.0          # CNY，极严重失误（用于信誉计算）
SIGNIFICANCE_THRESHOLD = 0.05         # 5%，价格变动显著性门槛
CIRCUIT_BREAKER_CONSECUTIVE = 3       # 连续 N 次 fatal loss 触发熔断
MIN_EVALUATED_FOR_CREDIBILITY = 3     # 至少 N 条已评估预测才计算信誉
CATCHABLE_LOW_MIN_BATCHES = 2         # 至少连续 N 个采集批次出现更低价才算可捕捉

# G3 RCA 错误分类 Enum（供 prompt 引用）
_RCA_CATEGORIES = (
    "Holiday_Surge_Ignored"
    " | Airline_Flash_Sale_Missed"
    " | Too_Close_To_Departure"
    " | Trend_Reversal_Undetected"
    " | Low_Historical_Data"
    " | External_Event_Impact"
    " | Other"
)

_RCA_SYSTEM_PROMPT = f"""\
你是一个专业的机票价格预测系统审计员，负责对 AI 预测失误进行根因分析（RCA）。

请根据提供的预测记录和实际价格走势，输出 JSON 格式的分析结果，严格遵循以下 schema：
{{
  "error_category": "以下之一：{_RCA_CATEGORIES}",
  "rca_analysis": "一段中文分析，说明失误原因和改进建议（100字以内）"
}}
"""


# ── G1 执行器 ─────────────────────────────────────────────────────────────────

def log_prediction(
    session: Session,
    route_id: int,
    brief: Dict[str, Any],
    current_price: float,
    days_until_flight: int,
) -> AIPredictionLog:
    """G1：记录一次结构化预测到 ai_prediction_log 表。

    Args:
        session: SQLAlchemy 会话。
        route_id: 路线 ID。
        brief: generate_brief_with_fallback() 返回的简报字典。
        current_price: 预测时当前最低价。
        days_until_flight: 距出发天数。

    Returns:
        已写入数据库的 AIPredictionLog 记录。
    """
    # 从 brief 提取字段
    action_raw = brief.get("action", "")
    # 兼容：action 可能是 "Buy" / "Wait" / "立即购买" / "等待观望" 等
    if action_raw in ("Buy", "立即购买"):
        recommended_action = "Buy"
    else:
        recommended_action = "Wait"

    reason = brief.get("reason", None) or brief.get("recommendation", None)
    trend = brief.get("trend", None)
    confidence_raw = brief.get("confidence", None)
    confidence = float(confidence_raw) if confidence_raw is not None else None
    llm_source = brief.get("_source", "rule_based")

    log = AIPredictionLog(
        route_id=route_id,
        predicted_at=datetime.now(timezone.utc),
        price_at_prediction=current_price,
        days_until_flight=days_until_flight,
        recommended_action=recommended_action,
        reason=reason,
        trend=trend,
        confidence=confidence,
        llm_source=llm_source,
        outcome_status="pending",
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    logger.debug("[G1] 预测记录已写入 id=%d route_id=%d action=%s", log.id, route_id, recommended_action)
    return log


# ── G2 监控器 ─────────────────────────────────────────────────────────────────

async def run_backtesting(session_factory: Any) -> int:
    """G2：对符合条件的 pending 预测记录执行回测，计算 Pain Index。

    触发条件（满足任一即回测）：
    - 对应路线已出发（Route.target_date < today）：该路线所有 pending 记录均可评估，
      使用完整出发前价格窗口。
    - 预测写入距今已满 7 天（predicted_at ≤ now - 7d）：仅对**该路线最新一条** pending
      记录触发，使用预测后 7 天价格窗口（防止历史旧记录反复触发大量回测）。

    Args:
        session_factory: SQLAlchemy SessionLocal 工厂。

    Returns:
        处理的预测记录数量。
    """
    today = date.today()
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    processed = 0

    with session_factory() as session:
        # 子查询：每条路线在 pending 记录中最新的 predicted_at
        latest_subq = (
            session.query(
                AIPredictionLog.route_id,
                func.max(AIPredictionLog.predicted_at).label("latest_predicted_at"),
            )
            .filter(AIPredictionLog.outcome_status == "pending")
            .group_by(AIPredictionLog.route_id)
            .subquery()
        )

        # 查询所有 pending 且满足以下任一条件的记录：
        # ① 对应路线已出发（全量 pending 记录均可回测）
        # ② 该记录是本路线最新的 pending 预测，且写入已满 7 天
        pending_logs = (
            session.query(AIPredictionLog)
            .join(Route, AIPredictionLog.route_id == Route.id)
            .outerjoin(
                latest_subq,
                and_(
                    AIPredictionLog.route_id == latest_subq.c.route_id,
                    AIPredictionLog.predicted_at == latest_subq.c.latest_predicted_at,
                ),
            )
            .filter(
                AIPredictionLog.outcome_status == "pending",
                or_(
                    Route.target_date < today,
                    and_(
                        AIPredictionLog.predicted_at <= seven_days_ago,
                        latest_subq.c.latest_predicted_at.isnot(None),
                    ),
                ),
            )
            .all()
        )

        logger.info("[G2] 发现 %d 条待回测记录", len(pending_logs))

        for log_entry in pending_logs:
            try:
                _evaluate_prediction(session, log_entry)
                processed += 1
            except Exception as exc:
                logger.error("[G2] 回测记录 id=%d 失败：%s", log_entry.id, exc, exc_info=True)

    return processed


def _evaluate_prediction(session: Session, log_entry: AIPredictionLog) -> None:
    """对单条预测记录执行回测评估，计算 outcome_status 和 pain_index。

    价格观测窗口根据触发场景自动选择：
    - 航班已出发：predicted_at → target_date（完整出发前窗口）
    - 预测满 7 天但航班未出发：predicted_at → predicted_at + 7d（7 日走势窗口）

    Args:
        session: SQLAlchemy 会话。
        log_entry: 待评估的 AIPredictionLog 记录。
    """
    # 获取路线信息
    route = session.query(Route).filter(Route.id == log_entry.route_id).first()
    if not route:
        log_entry.outcome_status = "skipped"
        session.commit()
        return

    today = date.today()
    flight_departed = route.target_date < today

    # ── 确定价格观测窗口截止时间 ────────────────────────────────────────────
    if flight_departed:
        # 航班已出发：使用 target_date 当天 23:59 UTC 作为截止（避免出发后数据噪声）
        from datetime import time as dtime
        cutoff_end = datetime.combine(route.target_date, dtime(23, 59, 59)).replace(
            tzinfo=timezone.utc
        )
        window_label = "出发前完整窗口"
    else:
        # 预测满 7 天但航班未出发：使用预测后 7 天作为截止
        cutoff_end = log_entry.predicted_at + timedelta(days=7)
        window_label = "7日走势窗口"

    # 获取观测窗口内的价格历史
    price_records = (
        session.query(PriceHistory)
        .filter(
            PriceHistory.route_id == log_entry.route_id,
            PriceHistory.scraped_at > log_entry.predicted_at,
            PriceHistory.scraped_at <= cutoff_end,
        )
        .order_by(PriceHistory.scraped_at.asc())
        .all()
    )

    logger.debug(
        "[G2] 评估 id=%d 使用%s，价格记录 %d 条",
        log_entry.id, window_label, len(price_records),
    )

    # 按批次 ID 去重，统计唯一批次数量
    batch_ids = set()
    for rec in price_records:
        if rec.batch_id:
            batch_ids.add(rec.batch_id)
        else:
            # 无 batch_id 的记录，以 scraped_at 秒级精度作为伪批次 ID
            batch_ids.add(str(rec.scraped_at.replace(microsecond=0)))

    if len(batch_ids) < 2:
        log_entry.outcome_status = "skipped"
        session.commit()
        return

    prices = [float(rec.price) for rec in price_records]
    actual_min = min(prices)
    actual_final = float(price_records[-1].price)

    log_entry.actual_min_price = actual_min
    log_entry.actual_final_price = actual_final

    base_price = float(log_entry.price_at_prediction)

    # ── Pain Index 计算 ───────────────────────────────────────────────────────
    action = log_entry.recommended_action
    pain = 0.0

    if action == "Buy":
        # 建议买，但价格后续下跌 → 买贵了的机会成本
        drop = base_price - actual_min
        drop_pct = drop / base_price if base_price > 0 else 0.0
        if drop_pct > SIGNIFICANCE_THRESHOLD:
            pain = drop * 0.7  # 机会成本折扣（非全额损失）
    elif action == "Wait":
        # 建议等，但价格后续上涨 → 错过买点损失
        rise = actual_final - base_price
        rise_pct = rise / base_price if base_price > 0 else 0.0
        if rise_pct > SIGNIFICANCE_THRESHOLD:
            pain = rise * 1.0

    # 判断结果
    change_pct = abs(actual_final - base_price) / base_price if base_price > 0 else 0.0
    if change_pct <= SIGNIFICANCE_THRESHOLD:
        log_entry.outcome_status = "neutral"
        log_entry.pain_index = 0.0
    elif pain <= 0:
        log_entry.outcome_status = "win"
        log_entry.pain_index = 0.0
    else:
        log_entry.outcome_status = "loss"
        log_entry.pain_index = pain

    # ── 可捕捉低价检测 ────────────────────────────────────────────────────────
    log_entry.catchable_low_exists = _detect_catchable_low(price_records, base_price)

    session.commit()
    logger.debug(
        "[G2] 回测完成 id=%d outcome=%s pain=%.2f",
        log_entry.id, log_entry.outcome_status, pain,
    )


def _detect_catchable_low(
    price_records: List[PriceHistory],
    base_price: float,
) -> int:
    """检查预测后的价格序列中是否出现了可捕捉的低价。

    需要至少 CATCHABLE_LOW_MIN_BATCHES 个连续批次的价格低于
    base_price * (1 - SIGNIFICANCE_THRESHOLD) 才算可捕捉。

    Args:
        price_records: 预测时间之后的价格历史（已按时间升序排列）。
        base_price: 预测时的基准价格。

    Returns:
        1 = 有可捕捉低价，0 = 无。
    """
    threshold_price = base_price * (1 - SIGNIFICANCE_THRESHOLD)

    # 按批次 ID 分组
    batches: Dict[str, List[float]] = {}
    for rec in price_records:
        bid = rec.batch_id or str(rec.scraped_at.replace(microsecond=0))
        if bid not in batches:
            batches[bid] = []
        batches[bid].append(float(rec.price))

    # 按批次顺序检查（保持时间顺序需要原始记录的顺序）
    seen_bid_order: List[str] = []
    for rec in price_records:
        bid = rec.batch_id or str(rec.scraped_at.replace(microsecond=0))
        if bid not in seen_bid_order:
            seen_bid_order.append(bid)

    consecutive = 0
    for bid in seen_bid_order:
        batch_min = min(batches[bid])
        if batch_min < threshold_price:
            consecutive += 1
            if consecutive >= CATCHABLE_LOW_MIN_BATCHES:
                return 1
        else:
            consecutive = 0

    return 0


# ── G3 诊断器 ─────────────────────────────────────────────────────────────────

async def run_rca(
    session_factory: Any,
    api_key: str,
    base_url: str,
    model: str,
) -> int:
    """G3：对高痛失误记录执行根因分析（第二次 LLM 调用）。

    Args:
        session_factory: SQLAlchemy SessionLocal 工厂。
        api_key: DeepSeek API Key。
        base_url: API 基础 URL。
        model: 模型名称。

    Returns:
        处理的 RCA 记录数量。
    """
    processed = 0

    with session_factory() as session:
        loss_logs = (
            session.query(AIPredictionLog)
            .filter(
                AIPredictionLog.outcome_status == "loss",
                AIPredictionLog.pain_index > HIGH_PAIN_THRESHOLD,
                AIPredictionLog.rca_run_at.is_(None),
            )
            .all()
        )

        logger.info("[G3] 发现 %d 条待 RCA 记录", len(loss_logs))

        for log_entry in loss_logs:
            try:
                await _run_single_rca(session, log_entry, api_key, base_url, model)
                processed += 1
            except Exception as exc:
                logger.error("[G3] RCA 记录 id=%d 失败：%s", log_entry.id, exc, exc_info=True)

    return processed


async def _run_single_rca(
    session: Session,
    log_entry: AIPredictionLog,
    api_key: str,
    base_url: str,
    model: str,
) -> None:
    """对单条失误记录发起 RCA LLM 调用。

    Args:
        session: SQLAlchemy 会话。
        log_entry: 待分析的 AIPredictionLog 记录。
        api_key: DeepSeek API Key。
        base_url: API 基础 URL。
        model: 模型名称。
    """
    import openai  # lazy import

    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    route = session.query(Route).filter(Route.id == log_entry.route_id).first()
    route_label = f"{route.origin} → {route.destination}" if route else f"route_id={log_entry.route_id}"

    user_prompt = (
        f"路线：{route_label}\n"
        f"预测时间：{log_entry.predicted_at}\n"
        f"预测时价格：¥{float(log_entry.price_at_prediction):.0f}\n"
        f"建议操作：{log_entry.recommended_action}\n"
        f"建议原因：{log_entry.reason or '无'}\n"
        f"趋势预测：{log_entry.trend or '无'}\n"
        f"距出发天数：{log_entry.days_until_flight}\n"
        f"实际最低价：¥{float(log_entry.actual_min_price or 0):.0f}\n"
        f"实际最终价：¥{float(log_entry.actual_final_price or 0):.0f}\n"
        f"痛苦指数：¥{float(log_entry.pain_index or 0):.0f}\n\n"
        "请分析此次预测失误的根本原因。"
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _RCA_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
    finally:
        await client.close()

    content = response.choices[0].message.content or "{}"
    result = json.loads(content)

    log_entry.rca_run_at = datetime.now(timezone.utc)
    log_entry.error_category = result.get("error_category", "Other")
    log_entry.rca_analysis = result.get("rca_analysis", "")
    session.commit()

    logger.info("[G3] RCA 完成 id=%d category=%s", log_entry.id, log_entry.error_category)


# ── G4 进化器 ─────────────────────────────────────────────────────────────────

def get_route_credibility(session: Session, route_id: int) -> Dict[str, Any]:
    """G4：查询路线的 AI 预测信誉数据。

    Args:
        session: SQLAlchemy 会话。
        route_id: 路线 ID。

    Returns:
        包含以下字段的字典：
        - win_rate: 胜率（0.0~1.0）
        - evaluated_count: 已评估预测数量
        - consecutive_fatal_losses: 连续极严重失误次数
        - circuit_broken: 是否触发熔断
    """
    evaluated_logs = (
        session.query(AIPredictionLog)
        .filter(
            AIPredictionLog.route_id == route_id,
            AIPredictionLog.outcome_status.in_(["win", "loss", "neutral"]),
        )
        .order_by(AIPredictionLog.predicted_at.desc())
        .all()
    )

    evaluated_count = len(evaluated_logs)
    if evaluated_count == 0:
        return {
            "win_rate": 0.0,
            "evaluated_count": 0,
            "consecutive_fatal_losses": 0,
            "circuit_broken": False,
        }

    win_count = sum(1 for log in evaluated_logs if log.outcome_status == "win")
    # neutral 算半胜
    neutral_count = sum(1 for log in evaluated_logs if log.outcome_status == "neutral")
    win_rate = (win_count + neutral_count * 0.5) / evaluated_count

    # 统计近期连续极严重失误（从最新记录往前数）
    consecutive_fatal = 0
    for log in evaluated_logs:
        pain = float(log.pain_index or 0)
        if log.outcome_status == "loss" and pain >= FATAL_LOSS_THRESHOLD:
            consecutive_fatal += 1
        else:
            break

    circuit_broken = consecutive_fatal >= CIRCUIT_BREAKER_CONSECUTIVE

    return {
        "win_rate": win_rate,
        "evaluated_count": evaluated_count,
        "consecutive_fatal_losses": consecutive_fatal,
        "circuit_broken": circuit_broken,
    }


def build_evolved_context(session: Session, route_id: int) -> str:
    """G4：拼接历史失误摘要，作为 system prompt 后缀注入 AI 简报生成。

    Args:
        session: SQLAlchemy 会话。
        route_id: 路线 ID。

    Returns:
        历史预测记录摘要字符串（空字符串表示无历史数据）。
    """
    cred = get_route_credibility(session, route_id)
    evaluated_count = cred["evaluated_count"]

    if evaluated_count < MIN_EVALUATED_FOR_CREDIBILITY:
        return ""

    win_rate = cred["win_rate"]
    win_pct = int(win_rate * 100)

    # 获取最近 5 条失误记录
    recent_losses = (
        session.query(AIPredictionLog)
        .filter(
            AIPredictionLog.route_id == route_id,
            AIPredictionLog.outcome_status == "loss",
            AIPredictionLog.pain_index > 0,
        )
        .order_by(AIPredictionLog.predicted_at.desc())
        .limit(5)
        .all()
    )

    lines = [f"【历史预测记录】胜率 {win_pct}%（{evaluated_count} 次）"]

    if recent_losses:
        lines.append("近期失误：")
        for log in recent_losses:
            pain = float(log.pain_index or 0)
            cat = log.error_category or "未分类"
            action = log.recommended_action
            lines.append(
                f"  - 建议{action}，实际痛苦指数 ¥{pain:.0f}，分类：{cat}"
            )
        lines.append("请在本次分析中参考以上历史失误，避免重复类似错误。")

    if cred["circuit_broken"]:
        lines.append("⚠️ 警告：近期连续出现极严重误判，请提高分析置信度门槛。")

    return "\n".join(lines)
