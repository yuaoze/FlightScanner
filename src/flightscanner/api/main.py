"""FastAPI application entry point for FlightScanner API."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from flightscanner.api.routers import analytics, cookies, notifications, radar, routes, settings, stats

logger = logging.getLogger(__name__)

# Global reference so that API endpoints can interact with the running scheduler
# (e.g., the POST /routes/{id}/scrape endpoint can forward to scheduler.trigger_scrape).
_monitor = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the background price-monitoring scheduler on API startup.

    Disable by setting env var FLIGHTSCANNER_DISABLE_SCHEDULER=1
    (e.g., when running tests or a read-only API instance).
    """
    global _monitor
    if os.getenv("FLIGHTSCANNER_DISABLE_SCHEDULER") == "1":
        logger.info("FLIGHTSCANNER_DISABLE_SCHEDULER=1，跳过后台调度器启动")
        yield
        return

    try:
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
        from flightscanner.utils.config import settings as app_settings

        _monitor = PriceMonitorScheduler(
            headless=app_settings.scraper_headless,
            enable_notifications=True,
        )
        _monitor.start()
        logger.info("后台调度器已启动（随 API 进程）")
    except Exception:
        logger.exception("后台调度器启动失败，API 将继续服务但不会自动采集")

    yield

    if _monitor is not None:
        try:
            _monitor.stop()
            logger.info("后台调度器已停止")
        except Exception:
            logger.exception("停止调度器时发生异常")


app = FastAPI(
    title="FlightScanner API",
    description="Flight price monitoring dashboard API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(cookies.router, prefix="/api")
app.include_router(radar.router, prefix="/api")
