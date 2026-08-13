"""Purchases / BuyPlans / Experiences API endpoints（v2.2.0 买入闭环）。

覆盖：
- 一键买入与买入记录查询（含买后价格序列、买点分析）
- 买入计划 CRUD 与触发确认
- 经验库 CRUD
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from flightscanner.api.deps import get_db
from flightscanner.api.schemas import (
    BuyPlanCreate,
    BuyPlanResponse,
    BuyPointAnalysisResponse,
    ExperienceCreate,
    ExperienceResponse,
    ExperienceUpdate,
    InstantBuyRequest,
    PlanConfirmRequest,
    PriceSeriesPoint,
    PurchaseDetailResponse,
    PurchaseResponse,
)
from flightscanner.api.time_utils import iso_utc
from flightscanner.core.services import PurchaseService
from flightscanner.models.database import BuyPlan, BuyPointAnalysis, ExperienceEntry, PurchaseRecord
from flightscanner.utils.config import settings

router = APIRouter()


# ── 序列化辅助 ──────────────────────────────────────────────────────────────


def _route_label(route: Any) -> str:
    if route is None:
        return ""
    return f"{route.origin} → {route.destination}"


def _parse_ai_analysis(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """ai_analysis 存的是 JSON 字符串，返回时解析为 dict；解析失败退化为原文。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"verdict_comment": str(parsed)}
    except (json.JSONDecodeError, TypeError):
        return {"verdict_comment": raw}


def _analysis_to_response(analysis: BuyPointAnalysis) -> BuyPointAnalysisResponse:
    return BuyPointAnalysisResponse(
        post_min_price=float(analysis.post_min_price) if analysis.post_min_price is not None else None,
        post_max_price=float(analysis.post_max_price) if analysis.post_max_price is not None else None,
        final_price=float(analysis.final_price) if analysis.final_price is not None else None,
        regret_cost=float(analysis.regret_cost) if analysis.regret_cost is not None else None,
        savings_vs_final=float(analysis.savings_vs_final) if analysis.savings_vs_final is not None else None,
        verdict=analysis.verdict,
        ai_analysis=_parse_ai_analysis(analysis.ai_analysis),
        llm_source=analysis.llm_source,
        auto_generated=bool(analysis.auto_generated),
        pre_departure=bool(analysis.pre_departure),
        analysis_status=analysis.analysis_status,
        sample_size=analysis.sample_size,
        coverage_hours=(
            float(analysis.coverage_hours) if analysis.coverage_hours is not None else None
        ),
        data_quality=analysis.data_quality,
        analyzed_at=iso_utc(analysis.analyzed_at),
    )


def _purchase_to_response(
    svc: PurchaseService,
    purchase: PurchaseRecord,
    *,
    with_series: bool = False,
) -> PurchaseResponse:
    route = purchase.route
    current_price, _ = svc.get_latest_min_price(purchase.route_id, purchase=purchase)
    purchase_price = float(purchase.purchase_price)
    change_pct: Optional[float] = None
    if current_price is not None and purchase_price > 0:
        change_pct = round((current_price - purchase_price) / purchase_price * 100, 1)

    flight_no: Optional[str] = None
    airline: Optional[str] = None
    if purchase.flight is not None:
        flight_no = purchase.flight.flight_no
        airline = purchase.flight.airline

    payload: Dict[str, Any] = dict(
        id=purchase.id,
        route_id=purchase.route_id,
        route_label=_route_label(route),
        target_date=route.target_date.isoformat() if route and route.target_date else None,
        flight_no=flight_no,
        airline=airline,
        purchase_price=purchase_price,
        total_paid=float(purchase.total_paid) if purchase.total_paid is not None else None,
        currency=purchase.currency,
        seat_class=purchase.seat_class,
        passengers=purchase.passengers,
        purchased_at=iso_utc(purchase.purchased_at),
        purchase_type=purchase.purchase_type,
        notes=purchase.notes,
        status=purchase.status,
        current_price=current_price,
        change_pct=change_pct,
        analysis=_analysis_to_response(purchase.analysis) if purchase.analysis else None,
        created_at=iso_utc(purchase.created_at),
    )

    if with_series:
        series = svc.get_price_series_since(
            purchase.route_id,
            purchase.purchased_at,
            purchase=purchase,
        )
        payload["price_series"] = [
            PriceSeriesPoint(time=iso_utc(ts) or "", price=price) for ts, price in series
        ]
        return PurchaseDetailResponse(**payload)
    return PurchaseResponse(**payload)


def _plan_to_response(plan: BuyPlan) -> BuyPlanResponse:
    return BuyPlanResponse(
        id=plan.id,
        route_id=plan.route_id,
        route_label=_route_label(plan.route),
        plan_price=float(plan.plan_price) if plan.plan_price is not None else None,
        plan_execute_by=iso_utc(plan.plan_execute_by),
        status=plan.status,
        triggered_at=iso_utc(plan.triggered_at),
        trigger_price=float(plan.trigger_price) if plan.trigger_price is not None else None,
        trigger_reason=plan.trigger_reason,
        created_at=iso_utc(plan.created_at),
    )


def _experience_to_response(entry: ExperienceEntry) -> ExperienceResponse:
    return ExperienceResponse(
        id=entry.id,
        route_pattern=entry.route_pattern,
        category=entry.category,
        title=entry.title,
        content=entry.content,
        evidence_count=entry.evidence_count,
        status=entry.status,
        created_at=iso_utc(entry.created_at),
        updated_at=iso_utc(entry.updated_at),
    )


# ── 买入记录 ────────────────────────────────────────────────────────────────


@router.post("/purchases/instant", response_model=PurchaseResponse)
def instant_buy(req: InstantBuyRequest, db: Session = Depends(get_db)) -> PurchaseResponse:
    """一键买入：price 为空时取当前监控最低价预填。"""
    svc = PurchaseService(db)
    try:
        purchase = svc.instant_buy(
            route_id=req.route_id,
            price=req.price,
            unit_price=req.unit_price,
            total_paid=req.total_paid,
            currency=req.currency,
            seat_class=req.seat_class,
            passengers=req.passengers,
            purchased_at=req.purchased_at,
            notes=req.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _purchase_to_response(svc, purchase)


@router.get("/purchases", response_model=List[PurchaseResponse])
def list_purchases(
    status: Optional[str] = Query(default=None, description="holding|completed"),
    route_id: Optional[int] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[PurchaseResponse]:
    svc = PurchaseService(db)
    return [
        _purchase_to_response(svc, p)
        for p in svc.list_purchases(
            status=status,
            route_id=route_id,
            offset=offset,
            limit=limit,
        )
    ]


@router.get("/purchases/{purchase_id}", response_model=PurchaseDetailResponse)
def get_purchase(purchase_id: int, db: Session = Depends(get_db)) -> PurchaseDetailResponse:
    """买入记录详情：含买点分析与买后价格序列。"""
    svc = PurchaseService(db)
    try:
        purchase = svc.get_purchase(purchase_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _purchase_to_response(svc, purchase, with_series=True)  # type: ignore[return-value]


@router.post("/purchases/{purchase_id}/analyze", response_model=BuyPointAnalysisResponse)
async def analyze_purchase(
    purchase_id: int, db: Session = Depends(get_db)
) -> BuyPointAnalysisResponse:
    """手动生成/重新生成买点分析（未起飞时基于已有数据并标注 pre_departure）。"""
    svc = PurchaseService(db)
    try:
        analysis = await svc.generate_analysis(
            purchase_id,
            api_key=settings.deepseek_api_key or None,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            auto=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _analysis_to_response(analysis)


# ── 买入计划 ────────────────────────────────────────────────────────────────


@router.post("/plans", response_model=BuyPlanResponse)
def create_plan(req: BuyPlanCreate, db: Session = Depends(get_db)) -> BuyPlanResponse:
    svc = PurchaseService(db)
    try:
        plan = svc.create_plan(
            route_id=req.route_id,
            plan_price=req.plan_price,
            plan_execute_by=req.plan_execute_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _plan_to_response(plan)


@router.get("/plans", response_model=List[BuyPlanResponse])
def list_plans(
    status: Optional[str] = Query(default=None, description="pending|triggered|converted|cancelled|expired"),
    route_id: Optional[int] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[BuyPlanResponse]:
    svc = PurchaseService(db)
    return [
        _plan_to_response(p)
        for p in svc.list_plans(
            status=status,
            route_id=route_id,
            offset=offset,
            limit=limit,
        )
    ]


@router.delete("/plans/{plan_id}")
def cancel_plan(plan_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = PurchaseService(db)
    try:
        plan = svc.cancel_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "plan_id": plan.id, "status": plan.status}


@router.post("/plans/{plan_id}/confirm", response_model=PurchaseResponse)
def confirm_plan(
    plan_id: int, req: PlanConfirmRequest, db: Session = Depends(get_db)
) -> PurchaseResponse:
    """确认已触发计划成交：计划置 converted 并创建买入记录。"""
    svc = PurchaseService(db)
    try:
        purchase = svc.confirm_plan(
            plan_id,
            actual_price=req.actual_price if req.actual_price is not None else req.price,
            unit_price=req.unit_price,
            total_paid=req.total_paid,
            currency=req.currency,
            seat_class=req.seat_class,
            passengers=req.passengers,
            purchased_at=req.purchased_at,
            notes=req.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _purchase_to_response(svc, purchase)


# ── 经验库 ──────────────────────────────────────────────────────────────────


@router.get("/experiences", response_model=List[ExperienceResponse])
def list_experiences(
    status: Optional[str] = Query(default="active", description="active|archived，空串=全部"),
    route_pattern: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[ExperienceResponse]:
    svc = PurchaseService(db)
    return [
        _experience_to_response(e)
        for e in svc.list_experiences(
            status=status or None,
            route_pattern=route_pattern,
            offset=offset,
            limit=limit,
        )
    ]


@router.post("/experiences", response_model=ExperienceResponse)
def create_experience(req: ExperienceCreate, db: Session = Depends(get_db)) -> ExperienceResponse:
    svc = PurchaseService(db)
    try:
        entry = svc.create_experience(
            title=req.title,
            content=req.content,
            route_pattern=req.route_pattern,
            category=req.category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _experience_to_response(entry)


@router.put("/experiences/{entry_id}", response_model=ExperienceResponse)
def update_experience(
    entry_id: int, req: ExperienceUpdate, db: Session = Depends(get_db)
) -> ExperienceResponse:
    svc = PurchaseService(db)
    try:
        entry = svc.update_experience(entry_id, **req.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _experience_to_response(entry)


@router.delete("/experiences/{entry_id}")
def delete_experience(entry_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = PurchaseService(db)
    try:
        svc.delete_experience(entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "deleted": entry_id}
