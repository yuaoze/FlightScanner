from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from scripts import tongcheng_login


class _FakeLocator:
    def __init__(self, *, visible: bool) -> None:
        self.first = self
        self.visible = visible
        self.clicked = False

    async def count(self) -> int:
        return 1

    async def is_visible(self) -> bool:
        return self.visible

    async def click(self) -> None:
        self.clicked = True


class _FakeActivationPage:
    def __init__(self, *, corner_visible: bool) -> None:
        self.corner = _FakeLocator(visible=corner_visible)
        self.evaluated: str | None = None
        self.waited = False

    def locator(self, selector: str) -> _FakeLocator:
        assert selector == ".switch_corner_app"
        return self.corner

    async def wait_for_function(self, expression: str, *, timeout: int) -> None:
        assert "jQuery.appLogin" in expression
        assert timeout == 10_000
        self.waited = True

    async def evaluate(self, expression: str) -> None:
        self.evaluated = expression


@pytest.mark.asyncio
async def test_activate_qr_uses_visible_official_corner_switch() -> None:
    page = _FakeActivationPage(corner_visible=True)

    assert await tongcheng_login._activate_qr_login(page)
    assert page.waited
    assert page.corner.clicked
    assert page.evaluated is None


@pytest.mark.asyncio
async def test_activate_qr_calls_official_initializer_in_standalone_module() -> None:
    page = _FakeActivationPage(corner_visible=False)

    assert await tongcheng_login._activate_qr_login(page)
    assert not page.corner.clicked
    assert page.evaluated is not None
    assert "$.appLogin()" in page.evaluated
    assert ".login_app" in page.evaluated


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _FakeRoute:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = responses
        self.fetch_timeouts: list[int] = []
        self.fulfilled: list[tuple[_FakeResponse, bytes | None]] = []
        self.continued = 0
        self.aborted: list[str] = []

    async def fetch(self, *, timeout: int) -> _FakeResponse:
        self.fetch_timeouts.append(timeout)
        return self.responses.pop(0)

    async def fulfill(
        self, *, response: _FakeResponse, body: bytes | None = None
    ) -> None:
        self.fulfilled.append((response, body))

    async def continue_(self) -> None:
        self.continued += 1

    async def abort(self, error_code: str) -> None:
        self.aborted.append(error_code)


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


def _official_wechat_oauth_url(
    *,
    appid: str = "wx3827070276e49e30",
    redirect_host: str = "wx.17u.cn",
    redirect_path: str = "/flight/getwxuserinfo.html",
    callback_host: str = "passport.ly.com",
    page_host: str = "www.ly.com",
    state: str = "0123456789abcdef",
) -> str:
    callback = "https://" + callback_host + "/ThirdParty/WeChatLogin?" + urlencode(
        {"pageUrl": f"https://{page_host}/", "state": state}
    )
    redirect = "http://" + redirect_host + redirect_path + "?" + urlencode(
        {"url": callback}
    )
    return "https://open.weixin.qq.com/connect/qrconnect?" + urlencode(
        {
            "appid": appid,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "snsapi_login",
        }
    )


def test_accepts_only_tongcheng_official_wechat_oauth_chain() -> None:
    assert tongcheng_login._is_wechat_oauth_url(_official_wechat_oauth_url())

    assert not tongcheng_login._is_wechat_oauth_url(
        _official_wechat_oauth_url(appid="wx-attacker")
    )
    assert not tongcheng_login._is_wechat_oauth_url(
        _official_wechat_oauth_url(redirect_host="evil.example")
    )
    assert not tongcheng_login._is_wechat_oauth_url(
        _official_wechat_oauth_url(redirect_path="/other")
    )
    assert not tongcheng_login._is_wechat_oauth_url(
        _official_wechat_oauth_url(callback_host="evil.example")
    )
    assert not tongcheng_login._is_wechat_oauth_url(
        _official_wechat_oauth_url(page_host="evil.example")
    )
    assert not tongcheng_login._is_wechat_oauth_url(
        _official_wechat_oauth_url(state="")
    )


@pytest.mark.parametrize(
    "src",
    [
        "https://open.weixin.qq.com/connect/qrcode/abc-123",
        "https://open.weixin.qq.com/connect/qrcode/001YXvAk07kfGa1p",
        "/connect/qrcode/001YXvAk07kfGa1p",
    ],
)
def test_recognizes_official_wechat_qr_src(src: str) -> None:
    assert tongcheng_login._is_wechat_qr_src(src)


@pytest.mark.parametrize(
    "src",
    [
        "http://open.weixin.qq.com/connect/qrcode/abc",
        "https://evil.example/connect/qrcode/abc",
        "https://open.weixin.qq.com/connect/qrcode/",
        "https://open.weixin.qq.com/connect/qrcode/a/extra",
    ],
)
def test_rejects_untrusted_wechat_qr_src(src: str) -> None:
    assert not tongcheng_login._is_wechat_qr_src(src)


def test_wechat_callback_requires_exact_https_passport_endpoint() -> None:
    assert tongcheng_login._is_wechat_callback_url(
        "https://passport.ly.com/ThirdParty/WeChatLogin?code=secret"
    )
    assert not tongcheng_login._is_wechat_callback_url(
        "http://passport.ly.com/ThirdParty/WeChatLogin?code=secret"
    )
    assert not tongcheng_login._is_wechat_callback_url(
        "https://evil.example/ThirdParty/WeChatLogin?code=secret"
    )


@pytest.mark.parametrize("operation", ["connect", "start", "poll", "reconnect", "abort"])
def test_signalr_retry_operation_allowlist(operation: str) -> None:
    assert (
        tongcheng_login._signalr_operation(
            f"https://passport.ly.com/qrcode/connection/{operation}?token=secret"
        )
        == operation
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://passport.ly.com/qrcode/connection/negotiate",
        "https://passport.ly.com/login/CreateQrCode?connectionId=x",
        "https://passport.ly.com/Login/LoginWithApp",
        "https://evil.example/qrcode/connection/connect",
        "http://passport.ly.com/qrcode/connection/connect",
    ],
)
def test_signalr_retry_rejects_non_connection_or_untrusted_urls(url: str) -> None:
    assert tongcheng_login._signalr_operation(url) is None


@pytest.mark.asyncio
async def test_signalr_node_error_retries_until_owner_node() -> None:
    error = tongcheng_login._SIGNALR_NODE_ERROR
    responses = [
        _FakeResponse(400, error),
        _FakeResponse(400, error),
        _FakeResponse(200, b'{"Response":"started"}'),
    ]
    route = _FakeRoute(responses.copy())

    await tongcheng_login._retry_misdirected_signalr(
        route,
        _FakeRequest("https://passport.ly.com/qrcode/connection/start?token=secret"),
    )

    assert route.fetch_timeouts == [15_000, 15_000, 15_000]
    assert route.fulfilled == [(responses[-1], b'{"Response":"started"}')]
    assert route.continued == 0


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, b"another validation error"),
        (401, tongcheng_login._SIGNALR_NODE_ERROR),
        (500, tongcheng_login._SIGNALR_NODE_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_signalr_retry_does_not_repeat_other_responses(
    status: int, body: bytes
) -> None:
    response = _FakeResponse(status, body)
    route = _FakeRoute([response])

    await tongcheng_login._retry_misdirected_signalr(
        route,
        _FakeRequest("https://passport.ly.com/qrcode/connection/poll?token=secret"),
    )

    assert route.fetch_timeouts == [40_000]
    assert route.fulfilled == [(response, body)]


@pytest.mark.asyncio
async def test_signalr_retry_passthrough_for_negotiate() -> None:
    route = _FakeRoute([])

    await tongcheng_login._retry_misdirected_signalr(
        route,
        _FakeRequest("https://passport.ly.com/qrcode/connection/negotiate"),
    )

    assert route.fetch_timeouts == []
    assert route.fulfilled == []
    assert route.continued == 1
    assert route.aborted == []


@pytest.mark.asyncio
async def test_signalr_retry_fulfills_last_error_at_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tongcheng_login, "_SIGNALR_NODE_RETRIES", 3)
    responses = [
        _FakeResponse(400, tongcheng_login._SIGNALR_NODE_ERROR) for _ in range(3)
    ]
    route = _FakeRoute(responses.copy())

    await tongcheng_login._retry_misdirected_signalr(
        route,
        _FakeRequest("https://passport.ly.com/qrcode/connection/connect?token=secret"),
    )

    assert len(route.fetch_timeouts) == 3
    assert route.fetch_timeouts == [15_000, 15_000, 15_000]
    assert route.fulfilled == [(responses[-1], tongcheng_login._SIGNALR_NODE_ERROR)]


@pytest.mark.asyncio
async def test_signalr_fetch_failure_aborts_without_resending_request() -> None:
    class FailingRoute(_FakeRoute):
        async def fetch(self, *, timeout: int) -> _FakeResponse:
            self.fetch_timeouts.append(timeout)
            raise TimeoutError("poll stalled")

    route = FailingRoute([])
    await tongcheng_login._retry_misdirected_signalr(
        route,
        _FakeRequest("https://passport.ly.com/qrcode/connection/poll?token=secret"),
    )

    assert route.fetch_timeouts == [40_000]
    assert route.continued == 0
    assert route.aborted == ["failed"]


@pytest.mark.parametrize(
    "src",
    [
        "/login/CreateQrCode?connectionId=abc-123&r=0.1",
        "https://passport.ly.com/login/CreateQrCode?connectionId=abc",
        "//passport.ly.com/login/CreateQrCode?connectionId=abc&r=1",
    ],
)
def test_recognizes_generated_qr_urls(src: str) -> None:
    assert tongcheng_login._is_valid_qr_src(src)


@pytest.mark.parametrize(
    "src",
    [
        None,
        "",
        "//pic5.40017.cn/static-placeholder.jpg",
        "/login/CreateQrCode?r=1",
        "/login/CreateQrCode?connectionId=",
        "https://evil.example/login/CreateQrCode?connectionId=abc",
    ],
)
def test_rejects_placeholder_or_untrusted_qr_urls(src: object) -> None:
    assert not tongcheng_login._is_valid_qr_src(src)


def test_login_success_requires_official_endpoint_and_explicit_boolean() -> None:
    assert tongcheng_login._is_login_with_app_url(
        "https://passport.ly.com/Login/LoginWithApp?r=0.1"
    )
    assert not tongcheng_login._is_login_with_app_url(
        "https://example.com/Login/LoginWithApp"
    )
    assert tongcheng_login._login_response_succeeded({"Success": True, "Code": 0})
    assert not tongcheng_login._login_response_succeeded({"Success": False})
    assert not tongcheng_login._login_response_succeeded({"Success": "true"})
    assert not tongcheng_login._login_response_succeeded(None)


def test_filter_cookies_keeps_only_ly_domain() -> None:
    cookies = [
        {"name": "root", "value": "1", "domain": ".ly.com", "path": "/"},
        {"name": "passport", "value": "2", "domain": "passport.ly.com", "path": "/"},
        {"name": "www", "value": "3", "domain": "www.ly.com", "path": "/"},
        {"name": "lookalike", "value": "4", "domain": "badly.com", "path": "/"},
        {"name": "other", "value": "5", "domain": ".example.com", "path": "/"},
        {"value": "missing-name", "domain": ".ly.com"},
    ]

    filtered = tongcheng_login._filter_ly_cookies(cookies)

    assert [cookie["name"] for cookie in filtered] == ["root", "passport", "www"]


@pytest.mark.parametrize(
    "cookie",
    [
        {"name": "us", "value": "foo=1&userid=12345&bar=2", "domain": ".ly.com"},
        {
            "name": "CNMember",
            "value": "MemberId=abc%2D123&NickName=test",
            "domain": "passport.ly.com",
        },
    ],
)
def test_authenticated_member_requires_official_nonzero_marker(
    cookie: dict[str, str],
) -> None:
    assert tongcheng_login._has_authenticated_member([cookie])


@pytest.mark.parametrize(
    "cookies",
    [
        [{"name": "ASP.NET_SessionId", "value": "anonymous", "domain": ".ly.com"}],
        [{"name": "Identifier", "value": "anonymous", "domain": "passport.ly.com"}],
        [{"name": "us", "value": "userid=0&foo=1", "domain": ".ly.com"}],
        [{"name": "CNMember", "value": "MemberId=undefined", "domain": ".ly.com"}],
        [{"name": "CNMember", "value": "MemberId=123", "domain": ".example.com"}],
    ],
)
def test_anonymous_or_untrusted_cookies_are_not_login_success(
    cookies: list[dict[str, str]],
) -> None:
    assert not tongcheng_login._has_authenticated_member(cookies)


@pytest.mark.asyncio
async def test_wait_for_authenticated_member_ignores_anonymous_cookie() -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.calls = 0

        async def cookies(self) -> list[dict[str, str]]:
            self.calls += 1
            if self.calls == 1:
                return [
                    {"name": "Identifier", "value": "anon", "domain": ".ly.com"}
                ]
            return [
                {"name": "us", "value": "userid=42", "domain": ".ly.com"}
            ]

    context = FakeContext()
    cookies = await tongcheng_login._wait_for_authenticated_member(context, 1.0)

    assert context.calls == 2
    assert tongcheng_login._has_authenticated_member(cookies)


def test_atomic_write_produces_valid_json_and_no_temp_file(tmp_path: Path) -> None:
    output = tmp_path / "tongcheng_cookies.json"
    cookies = [{"name": "member", "value": "secret", "domain": ".ly.com", "path": "/"}]

    tongcheng_login._write_cookies_atomically(output, cookies)

    assert json.loads(output.read_text(encoding="utf-8")) == cookies
    assert list(tmp_path.glob(".tongcheng_cookies.json.*.tmp")) == []


def test_atomic_write_failure_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "tongcheng_cookies.json"
    output.write_text('[{"old": true}]', encoding="utf-8")

    def fail_replace(source: object, target: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        tongcheng_login._write_cookies_atomically(
            output, [{"name": "new", "value": "1", "domain": ".ly.com"}]
        )

    assert output.read_text(encoding="utf-8") == '[{"old": true}]'
    assert list(tmp_path.glob(".tongcheng_cookies.json.*.tmp")) == []


def test_qr_quiet_zone_is_added_for_reliable_phone_scanning(tmp_path: Path) -> None:
    from PIL import Image

    qr_path = tmp_path / "qr.png"
    Image.new("RGB", (20, 20), "black").save(qr_path)

    tongcheng_login._add_qr_quiet_zone(qr_path, border=16)

    with Image.open(qr_path) as padded:
        assert padded.size == (312, 312)
        assert padded.getpixel((0, 0)) == (255, 255, 255)
        assert padded.getpixel((96, 96)) == (0, 0, 0)


@pytest.mark.asyncio
async def test_temporary_qr_screenshot_notifies_then_cleans_up() -> None:
    callback_paths: list[str] = []

    class FakeQrElement:
        async def screenshot(self, *, path: str) -> None:
            Path(path).write_bytes(b"fake-png-data")

    async with tongcheng_login._temporary_qr_image(
        FakeQrElement(), callback_paths.append
    ) as qr_path:
        assert qr_path.exists()
        assert qr_path.read_bytes() == b"fake-png-data"
        assert callback_paths == [str(qr_path)]

    assert not qr_path.exists()


@pytest.mark.asyncio
async def test_temporary_qr_cleanup_when_callback_fails() -> None:
    captured_path: Path | None = None

    class FakeQrElement:
        async def screenshot(self, *, path: str) -> None:
            nonlocal captured_path
            captured_path = Path(path)
            captured_path.write_bytes(b"fake-png-data")

    def fail_callback(path: str) -> None:
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        async with tongcheng_login._temporary_qr_image(FakeQrElement(), fail_callback):
            pass

    assert captured_path is not None
    assert not captured_path.exists()


@pytest.mark.asyncio
async def test_failed_login_does_not_overwrite_existing_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "tongcheng_cookies.json"
    original = '[{"name":"old","value":"keep","domain":".ly.com"}]'
    output.write_text(original, encoding="utf-8")

    async def fail_flow(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(tongcheng_login, "_run_qr_flow", fail_flow)

    assert not await tongcheng_login.qr_login(output_path=str(output))
    assert output.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_oauth_callback_with_only_anonymous_cookies_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "tongcheng_cookies.json"
    original = '[{"name":"old","value":"keep","domain":".ly.com"}]'
    output.write_text(original, encoding="utf-8")

    async def anonymous_flow(**kwargs: object) -> list[dict[str, str]]:
        return [
            {
                "name": "ASP.NET_SessionId",
                "value": "anonymous",
                "domain": ".ly.com",
            },
            {
                "name": "Identifier",
                "value": "anonymous",
                "domain": "passport.ly.com",
            },
        ]

    monkeypatch.setattr(tongcheng_login, "_run_qr_flow", anonymous_flow)

    assert not await tongcheng_login.qr_login(output_path=str(output))
    assert output.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_successful_login_filters_and_atomically_replaces_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "tongcheng_cookies.json"
    output.write_text('[{"name":"old"}]', encoding="utf-8")

    async def successful_flow(**kwargs: object) -> list[dict[str, str]]:
        return [
            {
                "name": "CNMember",
                "value": "MemberId=12345&NickName=test",
                "domain": ".ly.com",
                "path": "/",
            },
            {"name": "foreign", "value": "drop", "domain": ".example.com", "path": "/"},
        ]

    monkeypatch.setattr(tongcheng_login, "_run_qr_flow", successful_flow)

    assert await tongcheng_login.qr_login(output_path=str(output))
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert [cookie["name"] for cookie in saved] == ["CNMember"]


@pytest.mark.asyncio
async def test_default_qr_flow_uses_wechat_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    async def fake_wechat_flow(**kwargs: object) -> list[dict[str, str]]:
        observed.update(kwargs)
        return [{"name": "us", "value": "userid=1", "domain": ".ly.com"}]

    monkeypatch.setattr(tongcheng_login, "_run_wechat_qr_flow", fake_wechat_flow)

    def callback(path: str) -> None:
        pass

    result = await tongcheng_login._run_qr_flow(
        headless=True,
        timeout=300,
        on_qr_ready=callback,
    )

    assert result is not None
    assert observed == {
        "headless": True,
        "timeout": 300,
        "on_qr_ready": callback,
    }


def test_qr_login_signature_matches_cookie_api_contract() -> None:
    signature = inspect.signature(tongcheng_login.qr_login)
    assert list(signature.parameters) == [
        "headless",
        "output_path",
        "timeout",
        "on_qr_ready",
    ]
    assert signature.parameters["headless"].default is True
    assert signature.parameters["timeout"].default == 300
