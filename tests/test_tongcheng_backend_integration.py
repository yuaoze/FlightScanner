"""Backend platform configuration, scheduler, and cookie integration tests."""

import asyncio
import base64
import json
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from flightscanner.api.routers import cookies as cookie_api
from flightscanner.api.routers import settings as settings_api
from flightscanner.scheduler import price_monitor
from flightscanner.utils.config import Settings


def _cookie_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "fliggy": tmp_path / "fliggy.json",
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


@pytest.fixture
def clean_fliggy_login_state():
    with cookie_api._login_lock:
        previous = cookie_api._login_states["fliggy"]
        cookie_api._login_states["fliggy"] = cookie_api.LoginState()
    yield
    with cookie_api._login_lock:
        cookie_api._login_states["fliggy"] = previous


@pytest.fixture
def cookie_client(tmp_path, monkeypatch):
    monkeypatch.setattr(cookie_api, "_COOKIE_FILES", _cookie_paths(tmp_path))
    monkeypatch.setattr(cookie_api, "_notify_scrapers_reload", MagicMock())
    app = FastAPI()
    app.include_router(cookie_api.router)
    with TestClient(app) as client:
        yield client


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
            scraper_type="tongcheng,fliggy,tongcheng,ctrip",
        )

        assert settings.scraper_type == "tongcheng,fliggy,ctrip"

    def test_scraper_type_still_rejects_unknown_platform(self):
        with pytest.raises(ValueError, match="未知爬虫平台"):
            Settings(_env_file=None, scraper_type="fliggy,not-a-platform")

    def test_default_platforms_replace_qunar_with_fliggy(self, monkeypatch):
        monkeypatch.delenv("SCRAPER_TYPE", raising=False)

        settings = Settings(_env_file=None)

        assert settings.scraper_type == "fliggy,ctrip,tongcheng"
        assert "fliggy_cookies" in Settings.model_fields
        assert "qunar_cookies" not in Settings.model_fields

    def test_scraper_type_normalizes_fliggy_and_preserves_order(self):
        settings = Settings(
            _env_file=None,
            scraper_type=" Fliggy,CTRIP,fliggy, tongcheng ",
            fliggy_cookies='[{"name":"cookie2","value":"session"}]',
        )

        assert settings.scraper_type == "fliggy,ctrip,tongcheng"
        assert settings.fliggy_cookies == '[{"name":"cookie2","value":"session"}]'

    @pytest.mark.parametrize("platforms", ["qunar", "ctrip,qunar", "fliggy,qunar"])
    def test_retired_qunar_is_not_a_configuration_alias(self, platforms):
        with pytest.raises(ValueError, match="未知爬虫平台"):
            Settings(_env_file=None, scraper_type=platforms)


class TestTongchengSchedulerIntegration:
    @pytest.mark.parametrize("platform", ["tongcheng", "fliggy", "ctrip"])
    def test_scheduler_builds_platform_with_shared_limits_and_cookies(self, monkeypatch, platform):
        get_scraper = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(price_monitor.settings, "scraper_type", platform)
        monkeypatch.setattr(
            price_monitor.settings, f"{platform}_cookies", '[{"name":"sid","value":"1"}]'
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
            platform,
            cookies=[{"name": "sid", "value": "1"}],
            headless=False,
            timeout=45000,
            max_retries=4,
            max_results=35,
        )
        assert len(monitor.scrapers) == 1

    @pytest.mark.parametrize("cookies", [None, "invalid-json"])
    def test_fliggy_uses_default_cookie_file_without_valid_config(self, monkeypatch, cookies):
        monitor = object.__new__(price_monitor.PriceMonitorScheduler)
        monitor.headless = True
        get_scraper = MagicMock()
        monkeypatch.setattr(price_monitor.settings, "fliggy_cookies", cookies)
        monkeypatch.setattr(price_monitor.ScraperRegistry, "get", get_scraper)

        monitor._build_configured_scrapers(["fliggy"])

        assert get_scraper.call_args.args == ("fliggy",)
        assert get_scraper.call_args.kwargs["cookies"] is None

    def test_fliggy_progress_uses_platform_key_and_label(self):
        from flightscanner.scrapers import FliggyScraper

        scraper = object.__new__(FliggyScraper)

        assert price_monitor._scraper_identity(scraper) == ("fliggy", "飞猪")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform", ["tongcheng", "fliggy"])
    async def test_runtime_reconfigure_swaps_then_closes_old_scrapers(self, monkeypatch, platform):
        monitor = object.__new__(price_monitor.PriceMonitorScheduler)
        monitor.headless = True
        monitor._active_scraper_tasks = set()
        old = MagicMock()
        old.close = AsyncMock()
        new = MagicMock()
        monitor.scrapers = [old]
        monkeypatch.setattr(price_monitor.settings, "scraper_type", platform)
        monkeypatch.setattr(price_monitor.settings, "scraper_headless", True)
        monitor._build_configured_scrapers = MagicMock(return_value=[new])

        platforms = await monitor.reconfigure_scrapers()

        assert platforms == [platform]
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

    @pytest.mark.parametrize(
        ("platform", "qr_message", "timeout"),
        [
            ("tongcheng", "请使用微信“扫一扫”并确认登录同程旅行", 300),
            ("fliggy", "请使用淘宝 App 扫码并确认登录飞猪", 120),
        ],
    )
    def test_qr_login_state_machine_passes_output_path_and_reloads_scraper(
        self, tmp_path, monkeypatch, clean_tongcheng_login_state,
        clean_fliggy_login_state, platform, qr_message, timeout,
    ):
        monkeypatch.setattr(cookie_api, "_COOKIE_FILES", _cookie_paths(tmp_path))
        notify_reload = MagicMock()
        monkeypatch.setattr(cookie_api, "_notify_scrapers_reload", notify_reload)

        qr_path = tmp_path / f"{platform}-qr.png"
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

        cookie_api.start_qr_login(platform)
        try:
            assert qr_ready.wait(timeout=1.0)
            state = _wait_for_login_status(platform, "qr_ready")
            assert state.qr_base64 == base64.b64encode(qr_bytes).decode("ascii")
            assert state.done is False
            assert state.success is False
            assert state.message == qr_message
            assert state.timeout_seconds == timeout

            with pytest.raises(HTTPException) as duplicate:
                cookie_api.start_qr_login(platform)
            assert duplicate.value.status_code == 409

            with pytest.raises(HTTPException) as reset:
                cookie_api.reset_login_state(platform)
            assert reset.value.status_code == 409
        finally:
            release_login.set()

        state = _wait_for_login_status(platform, "success")
        assert state.done is True
        assert state.success is True
        assert state.message == "Cookie 已更新"
        loader.assert_called_once_with(platform)
        assert observed["headless"] is True
        assert observed["timeout"] == cookie_api._LOGIN_TIMEOUTS[platform]
        assert observed["output_path"] == str(tmp_path / f"{platform}.json")
        notify_reload.assert_called_once_with(platform)

        cookie_api.reset_login_state(platform)
        reset_state = cookie_api.get_login_status(platform)
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


class TestFliggyCookieApi:
    def test_cookie_file_and_login_loader_use_fliggy(self):
        from scripts.fliggy_login import qr_login

        assert cookie_api._COOKIE_FILES["fliggy"].name == "fliggy_cookies.json"
        assert cookie_api._load_qr_login("fliggy") is qr_login
        assert cookie_api._LOGIN_TIMEOUT == 120
        assert set(cookie_api._login_states) == {"fliggy", "ctrip", "tongcheng"}

    def test_status_exposes_only_active_platforms(self, cookie_client):
        response = cookie_client.get("/cookies/status")

        assert response.status_code == 200
        statuses = {item["platform"]: item for item in response.json()}
        assert set(statuses) == {"fliggy", "ctrip", "tongcheng"}
        assert statuses["fliggy"]["label"] == "飞猪"
        assert statuses["fliggy"]["login_supported"] is True
        assert statuses["fliggy"]["exists"] is False
        assert statuses["fliggy"]["valid"] is False
        assert statuses["fliggy"]["key_cookies_present"] == []
        assert statuses["fliggy"]["key_cookies_missing"] == ["cookie2", "unb"]

    @pytest.mark.parametrize(
        ("content", "present", "valid"),
        [
            ("cookie2=session", ["cookie2"], False),
            ("unb=user", ["unb"], False),
            ("cookie2=session; unb=", ["cookie2"], False),
            ("cookie2=; unb=user", ["unb"], False),
            ("sid=anonymous", [], False),
            ("cookie2=0; unb=user", ["unb"], False),
            ("cookie2=session; unb=null", ["cookie2"], False),
            ("cookie2=session; unb=user", ["cookie2", "unb"], True),
        ],
    )
    def test_validity_requires_both_nonempty_auth_cookies(
        self, cookie_client, content, present, valid,
    ):
        response = cookie_client.post("/cookies/fliggy/upload", json={"content": content})

        assert response.status_code == 200
        assert response.json()["platform"] == "fliggy"
        cookies = cookie_api._read_cookies("fliggy")
        assert all(item["domain"] == ".fliggy.com" for item in cookies)
        status = next(
            item for item in cookie_client.get("/cookies/status").json()
            if item["platform"] == "fliggy"
        )
        assert status["valid"] is valid
        assert status["key_cookies_present"] == present
        assert status["key_cookies_missing"] == sorted({"cookie2", "unb"} - set(present))

    def test_foreign_domain_markers_do_not_make_fliggy_cookie_status_valid(self, cookie_client):
        cookies = [
            {"name": "cookie2", "value": "session", "domain": ".example.com"},
            {"name": "unb", "value": "user", "domain": ".fliggy.com.example.com"},
        ]
        assert cookie_client.post(
            "/cookies/fliggy/upload", json={"content": json.dumps(cookies)}
        ).status_code == 200
        status = next(
            item for item in cookie_client.get("/cookies/status").json()
            if item["platform"] == "fliggy"
        )
        assert status["valid"] is False
        assert status["key_cookies_present"] == []
        assert status["count"] == 0

    def test_json_upload_preserves_taobao_and_fliggy_domains(self, cookie_client):
        cookies = [
            {"name": "cookie2", "value": "session", "domain": ".taobao.com"},
            {"name": "unb", "value": "user", "domain": ".taobao.com"},
            {"name": "cookie2", "value": "session", "domain": ".fliggy.com"},
            {"name": "unb", "value": "user", "domain": ".fliggy.com"},
            {"name": "sid", "value": "device"},
        ]
        response = cookie_client.post(
            "/cookies/fliggy/upload", json={"content": json.dumps(cookies)},
        )

        assert response.status_code == 200
        assert response.json()["count"] == 5
        stored = cookie_api._read_cookies("fliggy")
        assert [item["domain"] for item in stored] == [
            ".taobao.com", ".taobao.com", ".fliggy.com", ".fliggy.com", ".fliggy.com",
        ]
        cookie_api._notify_scrapers_reload.assert_called_once_with("fliggy")

        cookie_api._notify_scrapers_reload.reset_mock()
        assert cookie_client.delete("/cookies/fliggy").status_code == 204
        assert cookie_api._read_cookies("fliggy") == []
        cookie_api._notify_scrapers_reload.assert_called_once_with("fliggy")

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/cookies/qunar/upload"),
            ("DELETE", "/cookies/qunar"),
            ("POST", "/cookies/qunar/login"),
            ("GET", "/cookies/qunar/login/status"),
            ("POST", "/cookies/qunar/login/reset"),
        ],
    )
    def test_retired_qunar_endpoints_are_rejected(self, cookie_client, method, path):
        response = cookie_client.request(method, path, json={"content": "sid=unused"})

        assert response.status_code == 400
        assert "不支持的平台" in response.json()["detail"]

    def test_http_login_contract_is_unchanged(
        self, cookie_client, monkeypatch, clean_fliggy_login_state,
    ):
        start_thread = MagicMock()
        monkeypatch.setattr(cookie_api, "_start_login_thread", start_thread)

        response = cookie_client.post("/cookies/fliggy/login")

        assert response.status_code == 200
        assert response.json() == {"platform": "fliggy", "message": "飞猪扫码登录已启动"}
        status = cookie_client.get("/cookies/fliggy/login/status").json()
        assert status["status"] == "starting"
        assert status["timeout_seconds"] == 120
        assert status["done"] is False
        assert status["success"] is False
        start_thread.assert_called_once_with("fliggy", cookie_api._login_states["fliggy"])
        assert cookie_client.post("/cookies/fliggy/login").status_code == 409
        assert cookie_client.post("/cookies/fliggy/login/reset").status_code == 409

        cookie_api._finish_login_state(
            "fliggy", cookie_api._login_states["fliggy"], success=False, message="登录失败或超时",
        )
        assert cookie_client.post("/cookies/fliggy/login/reset").status_code == 204
        assert cookie_client.get("/cookies/fliggy/login/status").json()["status"] == "idle"
