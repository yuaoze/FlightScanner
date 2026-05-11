"""Notifications API endpoint for alert history."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from flightscanner.api.deps import get_db
from flightscanner.api.time_utils import fmt_cst
from flightscanner.models.database import NotificationLog, Route

router = APIRouter()


class NotificationItem(BaseModel):
    id: int
    route_id: int
    origin: str
    destination: str
    notified_at: str
    price: float
    trigger_reason: str
    channel: str
    status: str


class NotificationsResponse(BaseModel):
    total: int
    items: List[NotificationItem]


REASON_LABELS = {
    "target_hit": "达到目标价",
    "below_avg": "低于均价",
    "near_30d_low": "接近30天低点",
    "rebound_warning": "反弹预警",
    "trend_down": "持续下降",
    "departure_approaching": "临近出发",
}


@router.get("/notifications", response_model=NotificationsResponse)
def get_notifications(
    route_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> NotificationsResponse:
    """Get notification history with optional route filter."""
    query = (
        db.query(NotificationLog, Route.origin, Route.destination)
        .join(Route, NotificationLog.route_id == Route.id)
    )

    if route_id is not None:
        query = query.filter(NotificationLog.route_id == route_id)

    total = query.count()
    rows = (
        query.order_by(desc(NotificationLog.notified_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        NotificationItem(
            id=log.id,
            route_id=log.route_id,
            origin=origin,
            destination=destination,
            notified_at=fmt_cst(log.notified_at) or "",
            price=float(log.price),
            trigger_reason=log.trigger_reason,
            channel=log.channel,
            status=log.status,
        )
        for log, origin, destination in rows
    ]

    return NotificationsResponse(total=total, items=items)
