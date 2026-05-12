"""Weekend Inspiration Radar API endpoints."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from flightscanner.api.deps import get_db
from flightscanner.api.time_utils import iso_utc
from flightscanner.core.services.route_service import RouteService
from flightscanner.models.database import WeekendRadarCache
from flightscanner.weekend_radar.destinations import (
    DESTINATION_EMOJI,
    DESTINATION_GRADIENT,
    DESTINATION_IMAGE,
    INTERNATIONAL_DESTINATIONS,
    VISA_INFO,
)

router = APIRouter()


# ── Response models ──────────────────────────────────────────────────────


class AIBrief(BaseModel):
    headline: Optional[str] = None
    body: Optional[str] = None
    visa_note: Optional[str] = None
    tags: List[str] = []


class WeekendDealItem(BaseModel):
    id: int
    destination: str
    emoji: str
    gradient: str
    image_url: Optional[str] = None
    is_international: bool
    visa_status: Optional[str] = None
    visa_label: Optional[str] = None
    outbound_date: date
    return_date: date
    outbound_flight_no: Optional[str] = None
    outbound_airline: Optional[str] = None
    outbound_dep_time: Optional[str] = None
    outbound_arr_time: Optional[str] = None
    outbound_dep_airport: Optional[str] = None
    return_flight_no: Optional[str] = None
    return_airline: Optional[str] = None
    return_dep_time: Optional[str] = None
    return_arr_time: Optional[str] = None
    total_price: float
    historical_avg: Optional[float] = None
    beat_pct: Optional[int] = None
    source: str
    scan_type: str
    scanned_at: Optional[str] = None
    red_eye: bool
    ai_brief: Optional[AIBrief] = None


class WeekendOption(BaseModel):
    outbound_date: date
    return_date: date
    label: str
    deal_count: int


class RadarDealsResponse(BaseModel):
    deals: List[WeekendDealItem]
    weekends: List[WeekendOption]
    total: int
    latest_scan_at: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_brief(raw: Optional[str]) -> Optional[AIBrief]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        tags_raw = data.get("tags") or []
        tags = [str(t) for t in tags_raw if t]
        return AIBrief(
            headline=data.get("headline"),
            body=data.get("body"),
            visa_note=data.get("visa_note"),
            tags=tags,
        )
    except Exception:
        return None


def _is_red_eye(arr_time: Optional[str]) -> bool:
    """Flags arrivals between 00:01 and 06:59 as red-eye."""
    if not arr_time or ":" not in arr_time:
        return False
    try:
        h, m = map(int, arr_time.split(":"))
        return (0 <= h < 7) and not (h == 0 and m == 0)
    except ValueError:
        return False


def _record_to_item(rec: WeekendRadarCache) -> WeekendDealItem:
    is_intl = rec.destination in INTERNATIONAL_DESTINATIONS
    visa = VISA_INFO.get(rec.destination) if is_intl else None
    return WeekendDealItem(
        id=rec.id,
        destination=rec.destination,
        emoji=DESTINATION_EMOJI.get(rec.destination, DESTINATION_EMOJI["_default"]),
        gradient=DESTINATION_GRADIENT.get(
            rec.destination, "linear-gradient(135deg, #64748b, #334155)"
        ),
        image_url=DESTINATION_IMAGE.get(rec.destination),
        is_international=is_intl,
        visa_status=visa.get("status") if visa else None,
        visa_label=visa.get("label") if visa else None,
        outbound_date=rec.outbound_date,
        return_date=rec.return_date,
        outbound_flight_no=rec.outbound_flight_no,
        outbound_airline=rec.outbound_airline,
        outbound_dep_time=rec.outbound_dep_time,
        outbound_arr_time=rec.outbound_arr_time,
        outbound_dep_airport=rec.outbound_dep_airport,
        return_flight_no=rec.return_flight_no,
        return_airline=rec.return_airline,
        return_dep_time=rec.return_dep_time,
        return_arr_time=rec.return_arr_time,
        total_price=float(rec.total_price),
        historical_avg=float(rec.historical_avg) if rec.historical_avg else None,
        beat_pct=rec.beat_pct,
        source=rec.source,
        scan_type=rec.scan_type,
        scanned_at=iso_utc(rec.scanned_at),
        red_eye=_is_red_eye(rec.outbound_arr_time),
        ai_brief=_parse_brief(rec.ai_brief),
    )


def _featured_score(item: WeekendDealItem) -> float:
    """Heuristic 'should we feature this deal' score (higher = better).

    Balances price competitiveness, historical-beat percentage, visa
    friendliness, and AI-content quality. Tweak weights here when tuning.
    """
    score = 0.0
    p = item.total_price

    # Price competitiveness — log-shaped: cheaper gets disproportionately more.
    if p < 600:
        score += 35
    elif p < 1000:
        score += 25
    elif p < 1500:
        score += 15
    elif p < 2500:
        score += 8
    elif p < 4000:
        score += 3

    # Beat-vs-historical — only available when AI/scanner provided a baseline.
    if item.beat_pct:
        if item.beat_pct >= 85:
            score += 30
        elif item.beat_pct >= 70:
            score += 22
        elif item.beat_pct >= 50:
            score += 12
        elif item.beat_pct >= 30:
            score += 5

    # Cross-border weekend escapes are extra-novel when visa is friendly.
    if item.is_international:
        if item.visa_status == "免签":
            score += 18
        elif item.visa_status == "落地签":
            score += 10
        elif item.visa_status == "需申请":
            score += 3

    # AI gave it a real headline (not the auto-generated fallback).
    if item.ai_brief and item.ai_brief.headline and "周末逃跑计划" not in item.ai_brief.headline:
        score += 6

    # Has at least one AI tag — signals brief generated successfully.
    if item.ai_brief and item.ai_brief.tags:
        score += 3

    # Red-eye penalty — featured picks should be relaxed by default.
    if item.red_eye:
        score -= 12

    return score


def _curate_featured(items: List[WeekendDealItem], limit: int = 18) -> List[WeekendDealItem]:
    """Pick a diverse, high-value subset. ≤ 2 deals per destination."""
    if not items:
        return []
    scored = sorted(items, key=_featured_score, reverse=True)
    per_dest: dict[str, int] = {}
    picks: List[WeekendDealItem] = []
    for it in scored:
        if per_dest.get(it.destination, 0) >= 2:
            continue
        picks.append(it)
        per_dest[it.destination] = per_dest.get(it.destination, 0) + 1
        if len(picks) >= limit:
            break
    return picks


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/radar/deals", response_model=RadarDealsResponse)
def get_radar_deals(
    outbound_date: Optional[date] = Query(None, description="Filter by specific outbound date"),
    max_budget: Optional[int] = Query(None, ge=0, description="Upper bound on total price"),
    visa_free_only: bool = Query(False),
    exclude_red_eye: bool = Query(False),
    db: Session = Depends(get_db),
) -> RadarDealsResponse:
    """Return cached weekend deals + weekend options for UI filters."""
    today = date.today()

    # Collect all weekends that have at least one deal from today onward.
    # Count DISTINCT destinations (not rows) so that re-scanned weekends don't
    # double-count the chip badge.
    from sqlalchemy import func

    all_weekends = (
        db.query(
            WeekendRadarCache.outbound_date,
            WeekendRadarCache.return_date,
            func.count(func.distinct(WeekendRadarCache.destination)).label("cnt"),
        )
        .filter(WeekendRadarCache.outbound_date >= today)
        .group_by(WeekendRadarCache.outbound_date, WeekendRadarCache.return_date)
        .order_by(WeekendRadarCache.outbound_date)
        .all()
    )
    weekends = [
        WeekendOption(
            outbound_date=row.outbound_date,
            return_date=row.return_date,
            label=f"{row.outbound_date.strftime('%m-%d')} 周五 / {row.return_date.strftime('%m-%d')} 周日",
            deal_count=row.cnt,
        )
        for row in all_weekends
    ]

    # Deals for the chosen weekend (or all upcoming if none).
    # Fetch extra rows so that dedup still leaves enough to show after filtering.
    q = db.query(WeekendRadarCache).filter(WeekendRadarCache.outbound_date >= today)
    if outbound_date:
        q = q.filter(WeekendRadarCache.outbound_date == outbound_date)
    if max_budget:
        q = q.filter(WeekendRadarCache.total_price <= max_budget)
    # Order by scanned_at DESC first so that, when deduping, we keep the freshest row.
    q = q.order_by(WeekendRadarCache.scanned_at.desc()).limit(300)

    records = q.all()

    # Dedupe by (destination, outbound_date) — keep the freshest scan.
    # Records are already ordered by scanned_at DESC, so the first occurrence wins.
    seen: set = set()
    unique_records = []
    for r in records:
        key = (r.destination, r.outbound_date)
        if key in seen:
            continue
        seen.add(key)
        unique_records.append(r)

    items = [_record_to_item(r) for r in unique_records]

    if visa_free_only:
        items = [
            i
            for i in items
            if i.is_international and i.visa_status and i.visa_status in ("免签", "落地签")
        ]
    if exclude_red_eye:
        items = [i for i in items if not i.red_eye]

    if outbound_date:
        # Specific weekend → cheapest first, all kept.
        items.sort(key=lambda i: i.total_price)
        items = items[:60]
    else:
        # Curated mode: score each deal, diversify by destination (≤2 per city),
        # then return top picks. The score balances price competitiveness, beat
        # vs historical, visa friendliness, and AI-content quality.
        items = _curate_featured(items, limit=18)

    latest_scan = (
        db.query(func.max(WeekendRadarCache.scanned_at)).scalar()
    )

    return RadarDealsResponse(
        deals=items,
        weekends=weekends,
        total=len(items),
        latest_scan_at=iso_utc(latest_scan),
    )


class LockRouteResponse(BaseModel):
    route_id: int
    message: str


@router.post("/radar/{cache_id}/lock", response_model=LockRouteResponse, status_code=201)
def lock_deal_as_route(
    cache_id: int,
    target_price: Optional[float] = Query(None, description="Custom alert price, defaults to the deal's total_price"),
    db: Session = Depends(get_db),
) -> LockRouteResponse:
    """Turn a cached weekend deal into a monitored round-trip route.

    Aligns time windows with the weekend radar scanner (friday ≥19:00 outbound,
    sunday 18:00-23:59 return) so future scrapes keep filtering to genuine
    weekend-evening flights. Also triggers an immediate scrape via the
    background scheduler so the new route shows data without waiting for the
    next scheduled cycle.
    """
    import asyncio

    rec = db.query(WeekendRadarCache).filter(WeekendRadarCache.id == cache_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="未找到该灵感记录")

    price = Decimal(str(target_price if target_price is not None else rec.total_price))
    is_intl = rec.destination in INTERNATIONAL_DESTINATIONS
    service = RouteService(db)
    try:
        route = service.add_route(
            origin=rec.origin,
            destination=rec.destination,
            target_date=rec.outbound_date,
            target_price=price,
            scrape_interval=6,
            return_date=rec.return_date,
            trip_type="roundtrip",
            is_international=is_intl,
            # Mirror the scanner's time constraints so subsequent scrapes stay
            # locked onto genuine weekend-evening flights.
            dep_time_from="19:00",
            ret_dep_time_from="18:00",
            ret_dep_time_to="23:59",
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 注册到调度器：定时任务 + 立即采集一次（一步到位）
    scrape_triggered = False
    try:
        from flightscanner.api import main as api_main

        monitor = api_main._monitor
        if monitor is not None:
            monitor.register_new_route(route)
            loop = getattr(monitor, "_loop", None)
            scrape_triggered = bool(loop and loop.is_running())
    except Exception:
        pass

    suffix = "，已触发首次采集" if scrape_triggered else "（调度器未就绪，将在下次周期采集）"
    return LockRouteResponse(
        route_id=route.id,
        message=f"已创建监控：{rec.origin} → {rec.destination}，目标价 ¥{int(price)}{suffix}",
    )


class ScanResponse(BaseModel):
    message: str
    status: str


@router.post("/radar/scan", response_model=ScanResponse, status_code=202)
def trigger_radar_scan() -> ScanResponse:
    """Fire a manual radar scan via the background scheduler if available."""
    import asyncio

    try:
        from flightscanner.api import main as api_main

        monitor = api_main._monitor
        loop = getattr(monitor, "_loop", None) if monitor else None
        if monitor and loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(monitor._run_weekend_radar_batch(), loop)
            return ScanResponse(
                message="实时扫描已启动，预计 1-2 分钟后刷新",
                status="queued",
            )
    except Exception:
        pass

    # If the scheduler isn't available, surface a clear message instead of silently succeeding
    next_friday = date.today()
    while next_friday.weekday() != 4:
        next_friday += timedelta(days=1)
    return ScanResponse(
        message="后台调度器未运行，无法启动实时扫描",
        status="scheduler_unavailable",
    )
