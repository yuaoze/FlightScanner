"""Backend integration tests for the Tongcheng platform."""

import asyncio
import base64
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from flightscanner.api.routers import cookies as cookie_api
from flightscanner.api.routers import settings as settings_api
from flightscanner.scheduler import price_monitor
from flightscanner.utils.config import Settings


def _cookie_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "qunar": tmp_path / "qunar.json",
        "ctrip": tmp_path / "ctrip.json",
        "tongcheng": tmp_path / "tongcheng.json",
    }


@pytest.fixture
def clean_tongcheng_login_state():
    """Keep the module-level login state isolated between thread-based tests."""
    with cookie_api._login_lock:
        previous = cookie_api._login_states["tongcheng"]
        cookie_api._login_states["tongcheng"] = cookie_api.LoginState()
    yield
    with cookie_api._login_lock:
        cookie_api._login_states["tongcheng"] = previous


def _wait_for_login_status(platform: str, expected: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = cookie_api.get_login_status(platform)
        if state.status == expected:
            return state
        time.sleep(0.01)
    raise AssertionError(
        f"{platform} login did not reach {expected!r}; "
        f"last state was {cookie_api.get_login_status(platform).status!r}"
    )


class TestTongchengSettings:
    def test_scraper_type_accepts_tongcheng_and_preserves_order(self):
        settings = Settings(
            _env_file=None,
            scraper_type="tongcheng,qunar,tongcheng,ctrip",
        )

        assert settings.scraper_type == "tongcheng,qunar,ctrip"

    def test_scraper_type_still_rejects_unknown_platform(self):
        with pytest.raises(ValueError, match="未知爬虫平台"):
            Settings(_env_file=None, scraper_type="qunar,not-a-platform")


class TestTongchengSchedulerIntegration:
    def test_scheduler_builds_tongcheng_with_shared_limits_and_cookies(self, monkeypatch):
        get_scraper = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(price_monitor.settings, "scraper_type", "tongcheng")
        monkeypatch.setattr(
            price_monitor.settings, "tongcheng_cookies", '[{"name":"sid","value":"1"}]'
        )
        monkeypatch.setattr(price_monitor.settings, "scraper_timeout", 45000)
        monkeypatch.setattr(price_monitor.settings, "scraper_retry_count", 4)
        monkeypatch.setattr(price_monitor.settings, "max_results_per_platform", 35)
        monkeypatch.setattr(price_monitor.ScraperRegistry, "get", get_scraper)
        monkeypatch.setattr(price_monitor, "build_notifiers", lambda *args: [])
        monkeypatch.setattr(price_monitor, "AsyncIOScheduler", MagicMock)
        monkeypatch.setattr(
            price_monitor,
            "init_db",
            lambda database_url: (MagicMock(), MagicMock()),
        )

        monitor = price_monitor.PriceMonitorScheduler(headless=False)

        get_scraper.assert_called_once_with(
            "tongcheng",
            cookies=[{"name": "sid", "value": "1"}],
            headless=False,
            timeout=45000,
            max_retries=4,
            max_results=35,
        )
        assert len(monitor.scrapers) == 1

    @pytest.mark.asyncio
    async def test_runtime_reconfigure_swaps_then_closes_old_scrapers(self, monkeypatch):
        monitor = object.__new__(price_monitor.PriceMonitorScheduler)
        monitor.headless = True
        monitor._active_scraper_tasks = set()
        old = MagicMock()
        old.close = AsyncMock()
        new = MagicMock()
        monitor.scrapers = [old]
        monkeypatch.setattr(price_monitor.settings, "scraper_type", "tongcheng")
        monkeypatch.setattr(price_monitor.settings, "scraper_headless", True)
        monitor._build_configured_scrapers = MagicMock(return_value=[new])

        platforms = await monitor.reconfigure_scrapers()

        assert platforms == ["tongcheng"]
        assert monitor.scrapers == [new]
        old.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_settings_hook_schedules_runtime_reconfigure(self, monkeypatch):
        from flightscanner.api import main as api_main

        monitor = MagicMock()
        monitor._loop = asyncio.get_running_loop()
        monitor.reconfigure_scrapers = AsyncMock(return_value=["tongcheng"])
        monkeypatch.setattr(api_main, "_monitor", monitor)

        assert settings_api._schedule_scraper_reconfigure() is True
        await asyncio.sleep(0.01)
        monitor.reconfigure_scrapers.assert_awaited_once()


class TestTongchengCookieApi:
    def test_raw_cookie_uses_ly_domain(self):
        parsed = cookie_api._parse_content_to_cookies("tongcheng", "session_id=abc; device_id=xyz")

        assert [cookie["domain"] for cookie in parsed] == [".ly.com", ".ly.com"]

    def test_json_cookie_preserves_explicit_domain_and_defaults_missing_domain(self):
        parsed = cookie_api._parse_content_to_cookies(
            "tongcheng",
            '[{"name":"a","value":"1","domain":"www.ly.com"},{"name":"b","value":"2"}]',
        )

        assert parsed[0]["domain"] == "www.ly.com"
        assert parsed[1]["domain"] == ".ly.com"

    def test_status_exposes_qr_login_and_uploaded_cookie_is_valid(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(cookie_api, "_COOKIE_FILES", _cookie_paths(tmp_path))
        monkeypatch.setattr(cookie_api, "_notify_scrapers_reload", lambda platform: None)

        response = cookie_api.upload_cookies(
            "tongcheng",
            cookie_api.UploadCookieRequest(content="session_id=abc"),
        )
        status = next(
            item for item in cookie_api.get_cookies_status() if item.platform == "tongcheng"
        )

        assert response.count == 1
        assert status.exists is True
        assert status.valid is True
        assert status.login_supported is True
        assert status.key_cookies_missing == []

    def test_start_reserves_state_before_thread_runs_and_rejects_reset(
        self, monkeypatch, clean_tongcheng_login_state
    ):
        start_thread = MagicMock()
        monkeypatch.setattr(cookie_api, "_start_login_thread", start_thread)

        response = cookie_api.start_qr_login("tongcheng")
        state = cookie_api.get_login_status("tongcheng")

        assert response.platform == "tongcheng"
        assert state.status == "starting"
        assert state.done is False
        assert state.timeout_seconds == 300
        start_thread.assert_called_once()

        for operation in (cookie_api.start_qr_login, cookie_api.reset_login_state):
            with pytest.raises(HTTPException) as exc_info:
                operation("tongcheng")
            assert exc_info.value.status_code == 409

    def test_qr_login_state_machine_passes_output_path_and_reloads_scraper(
        self, tmp_path, monkeypatch, clean_tongcheng_login_state
    ):
        monkeypatch.setattr(cookie_api, "_COOKIE_FILES", _cookie_paths(tmp_path))
        notify_reload = MagicMock()
        monkeypatch.setattr(cookie_api, "_notify_scrapers_reload", notify_reload)

        qr_path = tmp_path / "tongcheng-qr.png"
        qr_bytes = b"a-real-enough-png-for-the-api-contract"
        qr_ready = threading.Event()
        release_login = threading.Event()
        observed: dict = {}

        async def fake_qr_login(**kwargs):
            observed.update(kwargs)
            qr_path.write_bytes(qr_bytes)
            kwargs["on_qr_ready"](str(qr_path))
            qr_ready.set()
            while not release_login.is_set():
                await asyncio.sleep(0.005)
            Path(kwargs["output_path"]).write_text("[]", encoding="utf-8")
            return True

        loader = MagicMock(return_value=fake_qr_login)
        monkeypatch.setattr(cookie_api, "_load_qr_login", loader)

        cookie_api.start_qr_login("tongcheng")
        try:
            assert qr_ready.wait(timeout=1.0)
            state = _wait_for_login_status("tongcheng", "qr_ready")
            assert state.qr_base64 == base64.b64encode(qr_bytes).decode("ascii")
            assert state.done is False
            assert state.success is False
            assert "微信" in state.message
            assert state.timeout_seconds == 300

            with pytest.raises(HTTPException) as duplicate:
                cookie_api.start_qr_login("tongcheng")
            assert duplicate.value.status_code == 409

            with pytest.raises(HTTPException) as reset:
                cookie_api.reset_login_state("tongcheng")
            assert reset.value.status_code == 409
        finally:
            release_login.set()

        state = _wait_for_login_status("tongcheng", "success")
        assert state.done is True
        assert state.success is True
        assert state.message == "Cookie 已更新"
        loader.assert_called_once_with("tongcheng")
        assert observed["headless"] is True
        assert observed["timeout"] == cookie_api._LOGIN_TIMEOUTS["tongcheng"]
        assert observed["output_path"] == str(tmp_path / "tongcheng.json")
        notify_reload.assert_called_once_with("tongcheng")

        cookie_api.reset_login_state("tongcheng")
        reset_state = cookie_api.get_login_status("tongcheng")
        assert reset_state.status == "idle"
        assert reset_state.done is False

    def test_qr_read_failure_is_terminal_and_cannot_be_overwritten_by_success(
        self, tmp_path, monkeypatch, clean_tongcheng_login_state
    ):
        monkeypatch.setattr(cookie_api, "_COOKIE_FILES", _cookie_paths(tmp_path))
        notify_reload = MagicMock()
        monkeypatch.setattr(cookie_api, "_notify_scrapers_reload", notify_reload)

        async def fake_qr_login(**kwargs):
            kwargs["on_qr_ready"](str(tmp_path / "missing-qr.png"))
            return True

        monkeypatch.setattr(cookie_api, "_load_qr_login", lambda platform: fake_qr_login)

        cookie_api.start_qr_login("tongcheng")
        state = _wait_for_login_status("tongcheng", "error")

        assert state.done is True
        assert state.success is False
        assert "读取二维码失败" in state.message
        notify_reload.assert_not_called()

    def test_unsuccessful_login_reaches_consistent_terminal_state(
        self, monkeypatch, clean_tongcheng_login_state
    ):
        notify_reload = MagicMock()
        monkeypatch.setattr(cookie_api, "_notify_scrapers_reload", notify_reload)

        async def fake_qr_login(**kwargs):
            return False

        monkeypatch.setattr(cookie_api, "_load_qr_login", lambda platform: fake_qr_login)

        cookie_api.start_qr_login("tongcheng")
        state = _wait_for_login_status("tongcheng", "error")

        assert state.done is True
        assert state.success is False
        assert "登录失败或超时" in state.message
        assert "手动上传 Cookie" in state.message
        notify_reload.assert_not_called()
