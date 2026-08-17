"""Tests for pollable, platform-aware immediate scrape status."""

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from flightscanner.api import main as api_main
from flightscanner.api.routers import routes as routes_api
from flightscanner.interfaces import SearchParams
from flightscanner.scheduler.price_monitor import (
    ImmediateScrapeStore,
    PriceMonitorScheduler,
)


class TestImmediateScrapeStore:
    def test_completed_task_reports_platform_counts(self):
        store = ImmediateScrapeStore(max_tasks=3)
        created = store.create(7, [("ctrip", "携程"), ("tongcheng", "同程旅行")])

        assert created["status"] == "queued"
        task_id = created["task_id"]
        store.mark_running(task_id)
        store.mark_platform_running(task_id, "ctrip", "携程")
        store.mark_platform_result(task_id, "ctrip", "携程", count=4)
        store.mark_platform_running(task_id, "tongcheng", "同程旅行")
        store.mark_platform_result(
            task_id,
            "tongcheng",
            "同程旅行",
            count=0,
            warning="同程旅行未获取到数据",
        )

        finished = store.finish(task_id)

        assert finished is not None
        assert finished["status"] == "completed"
        assert finished["total_count"] == 4
        assert finished["started_at"] is not None
        assert finished["completed_at"] is not None
        by_platform = {item["platform"]: item for item in finished["platforms"]}
        assert by_platform["ctrip"]["count"] == 4
        assert by_platform["tongcheng"]["count"] == 0
        assert by_platform["tongcheng"]["warning"] == "同程旅行未获取到数据"

    def test_mixed_platform_outcome_is_partial(self):
        store = ImmediateScrapeStore()
        created = store.create(9, [("ctrip", "携程"), ("tongcheng", "同程旅行")])
        task_id = created["task_id"]
        store.mark_running(task_id)
        store.mark_platform_running(task_id, "ctrip", "携程")
        store.mark_platform_result(task_id, "ctrip", "携程", count=2)
        store.mark_platform_running(task_id, "tongcheng", "同程旅行")
        store.mark_platform_result(
            task_id,
            "tongcheng",
            "同程旅行",
            error=TimeoutError("request timed out"),
        )

        finished = store.finish(task_id)

        assert finished is not None
        assert finished["status"] == "partial"
        failed = next(item for item in finished["platforms"] if item["platform"] == "tongcheng")
        assert failed["status"] == "failed"
        assert failed["error"] == "TimeoutError: request timed out"

    def test_all_failed_and_execution_failure_are_distinguishable(self):
        store = ImmediateScrapeStore()
        created = store.create(11, [("tongcheng", "同程旅行")])
        task_id = created["task_id"]
        store.mark_running(task_id)
        store.mark_platform_running(task_id, "tongcheng", "同程旅行")
        store.mark_platform_result(
            task_id,
            "tongcheng",
            "同程旅行",
            error=RuntimeError("blocked"),
        )

        all_failed = store.finish(task_id)

        assert all_failed is not None
        assert all_failed["status"] == "failed"
        assert all_failed["error"] == "所有平台采集均失败"

        second = store.create(12, [("ctrip", "携程")])
        execution_failed = store.finish(second["task_id"], ValueError("database write"))
        assert execution_failed is not None
        assert execution_failed["status"] == "failed"
        assert execution_failed["error"] == "ValueError: database write"

    def test_registry_is_bounded_and_latest_is_per_route(self):
        store = ImmediateScrapeStore(max_tasks=2)
        first = store.create(1, [("ctrip", "携程")])
        second = store.create(2, [("ctrip", "携程")])
        latest = store.create(1, [("tongcheng", "同程旅行")])

        assert store.get(first["task_id"]) is None
        assert store.get(second["task_id"]) is not None
        assert store.latest_for_route(1)["task_id"] == latest["task_id"]


@pytest.mark.asyncio
async def test_scrape_oneway_records_each_platform_result(mock_flight_price):
    class CtripScraper:
        max_results = 20

        async def search_flights(self, _params):
            return [mock_flight_price]

    class TongchengScraper:
        max_results = 20

        async def search_flights(self, _params):
            raise TimeoutError("tongcheng timeout")

    monitor = object.__new__(PriceMonitorScheduler)
    monitor.scrapers = [CtripScraper(), TongchengScraper()]
    monitor._active_scraper_tasks = set()
    monitor._scrape_warnings = []
    monitor._immediate_scrapes = ImmediateScrapeStore()
    created = monitor.create_scrape_task(route_id=3)
    task_id = created["task_id"]
    monitor._immediate_scrapes.mark_running(task_id)

    prices = await monitor._scrape_oneway(
        SearchParams("上海", "北京", date(2026, 9, 10)),
        task_id=task_id,
    )
    finished = monitor._immediate_scrapes.finish(task_id)

    assert len(prices) == 1
    assert finished is not None
    assert finished["status"] == "partial"
    by_platform = {item["platform"]: item for item in finished["platforms"]}
    assert by_platform["ctrip"]["count"] == 1
    assert by_platform["tongcheng"]["status"] == "failed"
    assert "tongcheng timeout" in by_platform["tongcheng"]["error"]


@pytest.mark.asyncio
async def test_scrape_route_finishes_zero_result_task_with_warning():
    class TongchengScraper:
        max_results = 20

        async def search_flights(self, _params):
            return []

    monitor = object.__new__(PriceMonitorScheduler)
    monitor.scrapers = [TongchengScraper()]
    monitor._active_scraper_tasks = set()
    monitor._scrape_warnings = []
    monitor._immediate_scrapes = ImmediateScrapeStore()
    route = SimpleNamespace(
        id=5,
        origin="上海",
        destination="香港",
        target_date=date(2026, 9, 10),
        return_date=None,
        trip_type="oneway",
        monitoring_mode="route",
        max_results=20,
    )
    created = monitor.create_scrape_task(route.id)

    await monitor.scrape_route(route, task_id=created["task_id"])

    finished = monitor.get_scrape_task(created["task_id"])
    assert finished is not None
    assert finished["status"] == "completed"
    assert finished["total_count"] == 0
    assert finished["platforms"][0]["status"] == "completed"
    assert "未获取到数据" in finished["platforms"][0]["warning"]


class TestImmediateScrapeApi:
    def test_post_returns_202_compatible_pollable_task(self, monkeypatch):
        route = MagicMock(id=23, is_active=True)
        service = MagicMock()
        service.get_route_by_id.return_value = route
        monkeypatch.setattr(routes_api, "RouteService", lambda _db: service)

        monitor = object.__new__(PriceMonitorScheduler)
        monitor.scrapers = []
        monitor._immediate_scrapes = ImmediateScrapeStore()
        monitor.scrape_route = AsyncMock()
        loop = MagicMock()
        loop.is_running.return_value = True
        monitor._loop = loop
        monkeypatch.setattr(api_main, "_monitor", monitor)

        submitted = []
        submitted_future = MagicMock()

        def submit(coro, target_loop):
            submitted.append((coro, target_loop))
            coro.close()
            return submitted_future

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)

        response = routes_api.trigger_scrape(23, db=MagicMock())

        post_route = next(
            route
            for route in routes_api.router.routes
            if route.path == "/routes/{route_id}/scrape" and "POST" in route.methods
        )
        assert post_route.status_code == 202
        assert response.status == "queued"
        assert response.task_id
        assert response.route_id == 23
        assert response.message == "采集任务已提交到后台调度器"
        assert len(submitted) == 1
        assert submitted[0][1] is loop

        exact = routes_api.get_scrape_status(23, response.task_id)
        latest = routes_api.get_latest_scrape_status(23)
        assert exact.task_id == response.task_id
        assert latest.task_id == response.task_id

        submitted_future.cancelled.return_value = True
        callback = submitted_future.add_done_callback.call_args.args[0]
        callback(submitted_future)
        cancelled = routes_api.get_scrape_status(23, response.task_id)
        assert cancelled.status == "failed"
        assert cancelled.error == "采集任务在执行前被取消"

    def test_post_keeps_accepted_response_when_scheduler_is_unavailable(self, monkeypatch):
        route = MagicMock(id=24, is_active=True)
        service = MagicMock()
        service.get_route_by_id.return_value = route
        monkeypatch.setattr(routes_api, "RouteService", lambda _db: service)
        monkeypatch.setattr(api_main, "_monitor", None)

        response = routes_api.trigger_scrape(24, db=MagicMock())

        assert response.status == "failed"
        assert response.task_id is None
        assert response.error == "后台调度器未运行"

    def test_status_rejects_task_from_another_route(self, monkeypatch):
        monitor = object.__new__(PriceMonitorScheduler)
        monitor.scrapers = []
        monitor._immediate_scrapes = ImmediateScrapeStore()
        task = monitor.create_scrape_task(route_id=30)
        monkeypatch.setattr(api_main, "_monitor", monitor)

        with pytest.raises(HTTPException) as exc_info:
            routes_api.get_scrape_status(31, task["task_id"])

        assert exc_info.value.status_code == 404
