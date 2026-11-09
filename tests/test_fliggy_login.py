"""Offline tests for the official Taobao canvas QR login and private cookie writes."""

import asyncio
import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import fliggy_login as module


@pytest.fixture(autouse=True)
def prohibit_real_browser(monkeypatch):
    monkeypatch.setattr(
        module, "async_playwright", Mock(side_effect=AssertionError("real browser forbidden"))
    )


@pytest.fixture
def authenticated_cookies():
    # Authentication requires both markers. Values are dummy, never live credentials.
    return [
        {
            "name": "cookie2",
            "value": "test-session",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
        },
        {"name": "unb", "value": "123456", "domain": ".taobao.com", "path": "/"},
        {"name": "fliggy_pref", "value": "zh_CN", "domain": ".fliggy.com", "path": "/"},
        {"name": "alitrip_pref", "value": "CNY", "domain": "www.alitrip.com", "path": "/"},
    ]


@pytest.mark.parametrize(
    "domain",
    [
        "fliggy.com",
        ".fliggy.com",
        "sjipiao.fliggy.com",
        ".taobao.com",
        "LOGIN.TAOBAO.COM",
        "alitrip.com",
        ".alitrip.com",
        "www.alitrip.com",
    ],
)
def test_platform_cookie_domains_accept_roots_and_real_subdomains(domain):
    assert module._is_platform_cookie({"domain": domain}) is True


@pytest.mark.parametrize(
    "domain",
    [
        "",
        None,
        "fliggy.com.evil.example",
        "evilfliggy.com",
        "taobao.com.evil.example",
        "nottaobao.com",
        "alitrip.com.evil.example",
        "notalitrip.com",
        ".qunar.com",
        ".example.com",
    ],
)
def test_platform_cookie_domains_reject_foreign_and_lookalike_hosts(domain):
    assert module._is_platform_cookie({"domain": domain}) is False
    assert module._is_platform_cookie({}) is False


def test_authentication_requires_both_official_cookie_markers(authenticated_cookies):
    assert module._has_authenticated_cookie(authenticated_cookies)
    assert not module._has_authenticated_cookie(authenticated_cookies[:1])
    assert not module._has_authenticated_cookie(authenticated_cookies[1:])
    assert not module._has_authenticated_cookie([])
    assert not module._has_authenticated_cookie(
        [
            {"name": "cna", "value": "anonymous-device", "domain": ".taobao.com"},
            {"name": "_m_h5_tk", "value": "anonymous-token", "domain": ".fliggy.com"},
        ]
    )


@pytest.mark.parametrize("marker", ["cookie2", "unb"])
@pytest.mark.parametrize("value", ["", " ", "0", "null", "NULL", "None", None])
def test_blank_or_placeholder_authentication_marker_is_not_login(
    authenticated_cookies, marker, value
):
    for cookie in authenticated_cookies:
        if cookie["name"] == marker:
            cookie["value"] = value
    assert not module._has_authenticated_cookie(authenticated_cookies)


@pytest.mark.parametrize("marker", ["cookie2", "unb"])
def test_authenticated_marker_on_foreign_domain_cannot_complete_login(
    authenticated_cookies, marker
):
    for cookie in authenticated_cookies:
        if cookie["name"] == marker:
            cookie["domain"] = ".taobao.com.evil.example"
    assert not module._has_authenticated_cookie(authenticated_cookies)


def test_atomic_write_fsyncs_private_temporary_before_replacing_old_file(
    tmp_path, monkeypatch, authenticated_cookies
):
    output = tmp_path / "fliggy_cookies.json"
    original = '[{"old":true}]\n'
    output.write_text(original, encoding="utf-8")
    output.chmod(0o644)
    real_fsync, real_replace = os.fsync, os.replace
    events = []

    def fsync(fd):
        assert output.read_text(encoding="utf-8") == original
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        events.append("fsync")
        return real_fsync(fd)

    def replace(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.parent == output.parent, "atomic rename must stay on the same filesystem"
        assert source != output
        assert destination == output
        assert output.read_text(encoding="utf-8") == original
        assert json.loads(source.read_text(encoding="utf-8")) == authenticated_cookies
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert events == ["fsync"]
        events.append("replace")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "fsync", fsync)
    monkeypatch.setattr(module.os, "replace", replace)
    module._write_cookies_atomically(output, authenticated_cookies)
    assert events == ["fsync", "replace"]
    assert json.loads(output.read_text(encoding="utf-8")) == authenticated_cookies
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("stage", ["serialize", "fsync", "replace"])
def test_atomic_write_failure_preserves_original_and_removes_temporary(
    tmp_path, monkeypatch, authenticated_cookies, stage
):
    output = tmp_path / "fliggy_cookies.json"
    original = b'[{"name":"old","value":"keep"}]\n'
    output.write_bytes(original)
    failure = Mock(side_effect=OSError("simulated disk failure"))
    if stage == "serialize":
        monkeypatch.setattr(module.json, "dump", failure)
    else:
        monkeypatch.setattr(module.os, stage, failure)
    with pytest.raises(OSError, match="simulated disk failure"):
        module._write_cookies_atomically(output, authenticated_cookies)
    assert output.read_bytes() == original
    assert list(tmp_path.iterdir()) == [output]


class LoginBrowser:
    """A strict fake for the small subset of Playwright used by qr_login."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.qr_paths = []
        self.qr = SimpleNamespace(
            wait_for=AsyncMock(), screenshot=AsyncMock(side_effect=self.screenshot)
        )
        self.page = SimpleNamespace(
            url=module.LOGIN_URL,
            goto=AsyncMock(),
            locator=Mock(side_effect=self.locator),
            is_closed=Mock(return_value=False),
        )
        self.context = SimpleNamespace(
            new_page=AsyncMock(return_value=self.page), cookies=AsyncMock(return_value=[])
        )
        self.browser = SimpleNamespace(
            new_context=AsyncMock(return_value=self.context), close=AsyncMock()
        )
        self.playwright = SimpleNamespace(
            chromium=SimpleNamespace(launch=AsyncMock(return_value=self.browser))
        )
        self.exited = []

    async def __aenter__(self):
        return self.playwright

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.exited.append(exc_type)

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        self.sleeps.append(seconds)
        await asyncio.sleep(0)

    def locator(self, selector):
        assert selector == "#qrcode-img canvas", (
            "capture the official Taobao canvas, not a fabricated QR"
        )
        return self.qr

    async def screenshot(self, *, path):
        path = Path(path)
        self.qr_paths.append(path)
        path.write_bytes(b"test-only-screenshot-bytes")

    def assert_cleaned(self):
        self.browser.close.assert_awaited_once()
        assert len(self.exited) == 1
        for path in self.qr_paths:
            assert not path.exists()
            assert not path.parent.exists()


@pytest.fixture
def browser(monkeypatch):
    browser = LoginBrowser()
    monkeypatch.setattr(module, "async_playwright", Mock(return_value=browser))
    monkeypatch.setattr(
        module, "asyncio", SimpleNamespace(get_running_loop=lambda: browser, sleep=browser.sleep)
    )
    return browser


async def test_headless_background_login_screenshots_official_qr_and_writes_only_after_authentication(
    browser, tmp_path, monkeypatch, authenticated_cookies
):
    output = tmp_path / "fliggy_cookies.json"
    original = '[{"old":true}]'
    output.write_text(original, encoding="utf-8")
    writes = Mock(wraps=module._write_cookies_atomically)
    monkeypatch.setattr(module, "_write_cookies_atomically", writes)
    batches = [
        authenticated_cookies[:1],
        authenticated_cookies
        + [
            {"name": "foreign", "value": "drop", "domain": ".example.com"},
            {"name": "lookalike", "value": "drop", "domain": "evilfliggy.com"},
        ],
    ]

    async def cookies():
        assert output.read_text(encoding="utf-8") == original
        writes.assert_not_called()
        if len(batches) == 1:
            browser.page.url = "https://www.fliggy.com/"
        return deepcopy(batches.pop(0))

    browser.context.cookies.side_effect = cookies
    captured = []

    def on_qr_ready(path):
        path = Path(path)
        assert path.read_bytes() == b"test-only-screenshot-bytes"
        assert output.read_text(encoding="utf-8") == original
        browser.context.cookies.assert_not_awaited()
        writes.assert_not_called()
        captured.append(path)

    # A task models the settings endpoint's background flow; never run a desktop browser.
    task = asyncio.create_task(module.qr_login(output_path=str(output), on_qr_ready=on_qr_ready))
    assert await task is True
    browser.playwright.chromium.launch.assert_awaited_once_with(headless=True)
    browser.browser.new_context.assert_awaited_once_with(locale="zh-CN")
    browser.page.goto.assert_awaited_once_with(
        module.LOGIN_URL, wait_until="domcontentloaded", timeout=30000
    )
    login_url = urlsplit(browser.page.goto.call_args.args[0])
    assert (login_url.scheme, login_url.hostname, login_url.path) == (
        "https",
        "login.taobao.com",
        "/member/login.jhtml",
    )
    assert parse_qs(login_url.query)["redirectURL"] == ["https://www.fliggy.com/"]
    browser.page.locator.assert_called_once_with("#qrcode-img canvas")
    browser.qr.wait_for.assert_awaited_once_with(state="visible", timeout=30000)
    browser.qr.screenshot.assert_awaited_once()
    assert captured == browser.qr_paths
    assert browser.context.cookies.await_count == 2
    assert browser.sleeps == [1]
    writes.assert_called_once_with(str(output), authenticated_cookies)
    assert json.loads(output.read_text(encoding="utf-8")) == authenticated_cookies
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [output]
    browser.assert_cleaned()


@pytest.mark.parametrize("existing", [False, True])
async def test_timeout_preserves_old_file_or_leaves_missing_file_absent(
    browser, tmp_path, monkeypatch, existing
):
    output = tmp_path / "fliggy_cookies.json"
    original = b'[{"name":"old","value":"keep"}]\n'
    if existing:
        output.write_bytes(original)
    browser.context.cookies.return_value = [
        {"name": "cookie2", "value": "not-enough", "domain": ".taobao.com"}
    ]
    writes = Mock(side_effect=AssertionError("unauthenticated cookies must not be saved"))
    monkeypatch.setattr(module, "_write_cookies_atomically", writes)
    assert await module.qr_login(output_path=str(output), timeout=3) is False
    assert browser.context.cookies.await_count == 3
    assert browser.sleeps == [1, 1, 1]
    assert output.read_bytes() == original if existing else not output.exists()
    writes.assert_not_called()
    browser.playwright.chromium.launch.assert_awaited_once_with(headless=True)
    browser.assert_cleaned()


async def test_closed_login_page_preserves_previous_file(browser, tmp_path):
    output = tmp_path / "fliggy_cookies.json"
    output.write_bytes(b"previous-login")
    browser.page.is_closed.return_value = True
    assert await module.qr_login(output_path=str(output)) is False
    assert output.read_bytes() == b"previous-login"
    assert browser.context.cookies.await_count == 1
    assert browser.sleeps == []
    browser.assert_cleaned()


@pytest.mark.parametrize(
    "url",
    [
        module.LOGIN_URL,
        "https://www.fliggy.com.evil.example/",
        "https://evilfliggy.com/",
        "https://www.taobao.com/",
    ],
)
async def test_cookie_markers_without_official_fliggy_redirect_do_not_save_login(
    browser, tmp_path, monkeypatch, authenticated_cookies, url
):
    output = tmp_path / "fliggy_cookies.json"
    output.write_bytes(b"previous-login")
    browser.context.cookies.return_value = authenticated_cookies
    browser.page.url = url
    writes = Mock(side_effect=AssertionError("login redirect has not completed"))
    monkeypatch.setattr(module, "_write_cookies_atomically", writes)
    assert await module.qr_login(output_path=str(output), timeout=2) is False
    assert output.read_bytes() == b"previous-login"
    writes.assert_not_called()
    browser.assert_cleaned()


@pytest.mark.parametrize(
    "url", ["https://fliggy.com/", "https://www.fliggy.com/", "https://sjipiao.fliggy.com/"]
)
async def test_authenticated_official_redirect_saves_login(
    browser, tmp_path, authenticated_cookies, url
):
    output = tmp_path / "fliggy_cookies.json"
    browser.context.cookies.return_value = authenticated_cookies
    browser.page.url = url
    assert await module.qr_login(output_path=str(output)) is True
    assert json.loads(output.read_text(encoding="utf-8")) == authenticated_cookies
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    browser.playwright.chromium.launch.assert_awaited_once_with(headless=True)
    browser.assert_cleaned()


@pytest.mark.parametrize(
    "stage", ["context", "page", "goto", "qr-visible", "screenshot", "cookies", "callback"]
)
async def test_login_failure_cleans_browser_and_never_overwrites_old_file(browser, tmp_path, stage):
    output = tmp_path / "fliggy_cookies.json"
    output.write_bytes(b"previous-login")
    operations = {
        "context": browser.browser.new_context,
        "page": browser.context.new_page,
        "goto": browser.page.goto,
        "qr-visible": browser.qr.wait_for,
        "screenshot": browser.qr.screenshot,
        "cookies": browser.context.cookies,
    }
    callback = Mock()
    if stage == "callback":
        callback.side_effect = RuntimeError("simulated login failure")
    else:
        operations[stage].side_effect = RuntimeError("simulated login failure")
    with pytest.raises(RuntimeError, match="simulated login failure"):
        await module.qr_login(output_path=str(output), on_qr_ready=callback)
    assert output.read_bytes() == b"previous-login"
    browser.assert_cleaned()


async def test_failed_cookie_replace_after_login_keeps_old_file_and_cleans_up(
    browser, tmp_path, monkeypatch, authenticated_cookies
):
    output = tmp_path / "fliggy_cookies.json"
    output.write_bytes(b"previous-login")
    browser.context.cookies.return_value = authenticated_cookies
    browser.page.url = "https://www.fliggy.com/"
    monkeypatch.setattr(module.os, "replace", Mock(side_effect=OSError("read-only filesystem")))
    with pytest.raises(OSError, match="read-only filesystem"):
        await module.qr_login(output_path=str(output))
    assert output.read_bytes() == b"previous-login"
    assert list(tmp_path.iterdir()) == [output]
    browser.assert_cleaned()


async def test_cancelling_background_login_keeps_old_file_and_cleans_qr_and_browser(
    browser, tmp_path
):
    output = tmp_path / "fliggy_cookies.json"
    output.write_bytes(b"previous-login")
    reading = asyncio.Event()
    release = asyncio.Event()

    async def blocked_cookies():
        reading.set()
        await release.wait()
        return []

    browser.context.cookies.side_effect = blocked_cookies
    task = asyncio.create_task(module.qr_login(output_path=str(output)))
    try:
        await reading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert output.read_bytes() == b"previous-login"
        assert browser.exited == [asyncio.CancelledError]
        browser.assert_cleaned()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_browser_launch_failure_exits_playwright_without_creating_cookie_file(
    browser, tmp_path
):
    output = tmp_path / "fliggy_cookies.json"
    browser.playwright.chromium.launch.side_effect = RuntimeError("browser unavailable")
    with pytest.raises(RuntimeError, match="browser unavailable"):
        await module.qr_login(output_path=str(output))
    assert not output.exists()
    assert browser.exited == [RuntimeError]
    browser.browser.close.assert_not_awaited()
