"""同程旅行 Cookie 刷新工具（微信扫码登录）。

运行方式::

    python scripts/tongcheng_login.py --headless

脚本从同程官方登录页进入微信 OAuth。只有微信回调完成且同程写入已登录
会员 Cookie 后，才会把 ``ly.com`` 域下的 Cookie 原子写入项目根目录；
二维码服务异常、扫码超时或浏览器异常均不会覆盖已有 Cookie。

同程 App 原生扫码链路仍保留为诊断实现，但其手机授权页依赖同程当前不稳定的
跨节点 SignalR 会话，因此不作为默认登录方式。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

LOGIN_URL = "https://passport.ly.com/login/NewLoginModule"
FLIGHTS_URL = "https://www.ly.com/flights/home"
OUTPUT_PATH = str(_PROJECT_ROOT / "tongcheng_cookies.json")

_QR_READY_TIMEOUT = 30.0
_QR_SELECTOR = ".scan_box:not(.has_invalid) #imgAppQrCode"
_LOGIN_WITH_APP_PATH = "/login/loginwithapp"
_SIGNALR_RETRY_PATHS = {"connect", "start", "poll", "reconnect", "abort"}
_SIGNALR_NODE_ERROR = b"The ConnectionId is in the incorrect format."
_SIGNALR_NODE_RETRIES = 12
_SIGNALR_TIMEOUT_MS = {
    "connect": 15_000,
    "start": 15_000,
    "poll": 40_000,
    "reconnect": 15_000,
    "abort": 5_000,
}
_WECHAT_APP_ID = "wx3827070276e49e30"
_WECHAT_QR_SELECTOR = "img.js_qrcode_img.web_qrcode_img:visible"
_WECHAT_REDIRECT_HOST = "wx.17u.cn"
_WECHAT_REDIRECT_PATH = "/flight/getwxuserinfo.html"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tongcheng_login")


def _is_ly_domain(domain: object) -> bool:
    """Return whether *domain* is ``ly.com`` or one of its subdomains."""
    normalized = str(domain or "").strip().lower().lstrip(".")
    return normalized == "ly.com" or normalized.endswith(".ly.com")


def _filter_ly_cookies(cookies: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep valid Playwright cookies belonging to the official ly.com domain."""
    result: list[dict[str, Any]] = []
    for raw in cookies:
        if not raw.get("name") or "value" not in raw or not _is_ly_domain(raw.get("domain")):
            continue
        result.append(dict(raw))
    return result


def _is_valid_qr_src(src: object) -> bool:
    """Recognise a generated QR URL and reject the page's static placeholder."""
    if not isinstance(src, str) or not src.strip():
        return False
    parsed = urlsplit(urljoin(LOGIN_URL, src.strip()))
    if parsed.hostname != "passport.ly.com":
        return False
    if parsed.path.rstrip("/").lower() != "/login/createqrcode":
        return False
    connection_ids = parse_qs(parsed.query).get("connectionId", [])
    return any(value.strip() for value in connection_ids)


def _is_login_with_app_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return (
        parsed.hostname == "passport.ly.com"
        and parsed.path.rstrip("/").lower() == _LOGIN_WITH_APP_PATH
    )


def _login_response_succeeded(payload: object) -> bool:
    """Only an explicit boolean Success from LoginWithApp is authoritative."""
    return isinstance(payload, Mapping) and payload.get("Success") is True


def _has_safe_url_authority(parsed: Any, allowed_ports: set[int | None]) -> bool:
    """Reject URL userinfo and malformed/non-standard ports."""
    try:
        port = parsed.port
    except ValueError:
        return False
    return parsed.username is None and parsed.password is None and port in allowed_ports


def _is_wechat_oauth_url(url: object) -> bool:
    """Validate the official WeChat OAuth URL emitted by Tongcheng Passport."""
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "open.weixin.qq.com"
        or parsed.path.rstrip("/") != "/connect/qrconnect"
        or not _has_safe_url_authority(parsed, {None, 443})
    ):
        return False
    query = parse_qs(parsed.query)
    if (
        query.get("appid") != [_WECHAT_APP_ID]
        or query.get("scope") != ["snsapi_login"]
        or query.get("response_type") != ["code"]
    ):
        return False
    redirect_values = query.get("redirect_uri", [])
    if len(redirect_values) != 1:
        return False
    redirect = urlsplit(redirect_values[0])
    if (
        redirect.scheme not in {"http", "https"}
        or redirect.hostname != _WECHAT_REDIRECT_HOST
        or redirect.path.rstrip("/").lower() != _WECHAT_REDIRECT_PATH
        or not _has_safe_url_authority(
            redirect,
            {None, 80} if redirect.scheme == "http" else {None, 443},
        )
    ):
        return False
    callback_values = parse_qs(redirect.query).get("url", [])
    if len(callback_values) != 1:
        return False
    callback = urlsplit(callback_values[0])
    if not (
        callback.scheme == "https"
        and callback.hostname == "passport.ly.com"
        and callback.path.rstrip("/").lower() == "/thirdparty/wechatlogin"
        and _has_safe_url_authority(callback, {None, 443})
    ):
        return False
    callback_query = parse_qs(callback.query)
    page_urls = callback_query.get("pageUrl", [])
    states = callback_query.get("state", [])
    if len(page_urls) != 1 or len(states) != 1 or not states[0].strip():
        return False
    page_url = urlsplit(page_urls[0])
    return (
        page_url.scheme == "https"
        and page_url.hostname == "www.ly.com"
        and _has_safe_url_authority(page_url, {None, 443})
    )


def _is_wechat_qr_src(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlsplit(urljoin("https://open.weixin.qq.com/connect/qrconnect", url))
    return (
        parsed.scheme == "https"
        and parsed.hostname == "open.weixin.qq.com"
        and _has_safe_url_authority(parsed, {None, 443})
        and parsed.path.count("/") == 3
        and parsed.path.startswith("/connect/qrcode/")
        and bool(parsed.path.removeprefix("/connect/qrcode/"))
    )


def _is_wechat_callback_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "passport.ly.com"
        and _has_safe_url_authority(parsed, {None, 443})
        and parsed.path.rstrip("/").lower() == "/thirdparty/wechatlogin"
    )


def _cookie_subfield(value: object, key: str) -> str | None:
    """Read one ``key=value`` field from a Tongcheng compound Cookie."""
    if not isinstance(value, str):
        return None
    for field in value.split("&"):
        raw_key, separator, raw_value = field.partition("=")
        if not separator or unquote(raw_key).lower() != key.lower():
            continue
        decoded = unquote(raw_value).strip()
        if decoded and decoded.lower() not in {"0", "null", "none", "undefined"}:
            return decoded
    return None


def _has_authenticated_member(cookies: Sequence[Mapping[str, Any]]) -> bool:
    """Require an official member marker; anonymous ly.com cookies are insufficient."""
    for cookie in cookies:
        if not _is_ly_domain(cookie.get("domain")):
            continue
        name = str(cookie.get("name", "")).lower()
        if name == "us" and _cookie_subfield(cookie.get("value"), "userid"):
            return True
        if name == "cnmember" and _cookie_subfield(cookie.get("value"), "MemberId"):
            return True
    return False


def _write_cookies_atomically(path: str | os.PathLike[str], cookies: Sequence[Mapping[str, Any]]) -> None:
    """Write cookies beside the target and atomically replace it after fsync."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(list(cookies), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _print_qr_terminal(png_path: str) -> None:
    """Render the QR in a terminal when Pillow is available."""
    try:
        from PIL import Image

        with Image.open(png_path) as source:
            image = source.convert("L")
        target_width = 60
        target_height = max(2, int(image.height * target_width / image.width))
        if target_height % 2:
            target_height += 1
        image = image.resize((target_width, target_height), Image.Resampling.NEAREST)
        pixels = list(image.getdata())
        print()
        for row in range(0, target_height, 2):
            line: list[str] = []
            for column in range(target_width):
                top = pixels[row * target_width + column] < 128
                bottom = pixels[(row + 1) * target_width + column] < 128
                line.append("█" if top and bottom else "▀" if top else "▄" if bottom else " ")
            print("".join(line))
        print()
    except Exception as exc:
        logger.debug("终端二维码渲染失败（不影响网页扫码）：%s", exc)


async def _visible(page: Any, selector: str) -> bool:
    locator = page.locator(selector)
    try:
        return bool(await locator.count() > 0 and await locator.first.is_visible())
    except Exception:
        return False


async def _activate_qr_login(page: Any) -> bool:
    """Show the QR panel and start Tongcheng's official QR connection.

    The full Passport page may expose ``.switch_corner_app``.  The standalone
    ``NewLoginModule`` used here does not render that corner control, but it
    still publishes the same official ``jQuery.appLogin`` initializer.  Calling
    that initializer is therefore required; merely unhiding the placeholder
    image would display a QR code that is not bound to any login session.
    """
    try:
        await page.wait_for_function(
            "() => window.jQuery && typeof window.jQuery.appLogin === 'function'",
            timeout=10_000,
        )
        if await _visible(page, ".switch_corner_app"):
            await page.locator(".switch_corner_app").first.click()
        else:
            await page.evaluate(
                """() => {
                    const $ = window.jQuery;
                    $('.login_con').addClass('none');
                    $('.login_app').removeClass('none');
                    $.appLogin();
                }"""
            )
        return True
    except Exception as exc:
        logger.error("无法启动同程官方扫码连接：%s", exc)
        return False


async def _wait_for_qr(page: Any, timeout: float) -> Any | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        locator = page.locator(_QR_SELECTOR).first
        try:
            if await locator.count() and await locator.is_visible():
                src = await locator.get_attribute("src")
                loaded = await locator.evaluate(
                    "(image) => image.complete && image.naturalWidth > 0"
                )
                if _is_valid_qr_src(src) and loaded:
                    return locator
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return None


async def _wait_for_wechat_qr(page: Any, timeout: float) -> Any | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        locator = page.locator(_WECHAT_QR_SELECTOR).first
        try:
            if await locator.count() and await locator.is_visible():
                src = await locator.get_attribute("src")
                loaded = await locator.evaluate(
                    "(image) => image.complete && image.naturalWidth > 0"
                )
                if _is_wechat_qr_src(src) and loaded:
                    return locator
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return None


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _navigation_timeout_ms(deadline: float, cap_seconds: float = 30.0) -> int:
    """Return a bounded Playwright timeout without extending the flow deadline."""
    return max(1, int(min(cap_seconds, _remaining_seconds(deadline)) * 1000))


async def _wait_for_authenticated_member(context: Any, timeout: float) -> list[dict[str, Any]]:
    """Wait briefly for Passport redirects to materialise a strong login Cookie."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        cookies = _filter_ly_cookies(await context.cookies())
        if _has_authenticated_member(cookies):
            return cookies
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return []
        await asyncio.sleep(min(0.25, remaining))


async def _read_response_json(response: Any) -> object:
    try:
        return await response.json()
    except Exception:
        try:
            return json.loads(await response.text())
        except Exception:
            return None


def _signalr_operation(url: object) -> str | None:
    """Return a retryable official SignalR operation, if any."""
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "passport.ly.com":
        return None
    prefix = "/qrcode/connection/"
    path = parsed.path.lower()
    if not path.startswith(prefix):
        return None
    operation = path[len(prefix) :].strip("/")
    return operation if operation in _SIGNALR_RETRY_PATHS else None


def _is_signalr_node_error(status: int, body: bytes) -> bool:
    """Match only Tongcheng's known cross-node token validation failure."""
    return status == 400 and body.strip() == _SIGNALR_NODE_ERROR


async def _retry_misdirected_signalr(route: Any, request: Any) -> None:
    """Retry a SignalR request until it reaches the token-signing IIS node.

    Tongcheng currently rotates four Passport IIS nodes between SignalR
    requests while ignoring its own ``route`` affinity Cookie.  Connection
    tokens are node-local, so a request hitting another node receives the
    exact 400 response matched above.  ``route.fetch`` preserves the original
    method, body, headers and cookies and bypasses this handler recursively.
    No business/login request is ever retried here.
    """
    operation = _signalr_operation(getattr(request, "url", None))
    if operation is None:
        await route.continue_()
        return

    last_response: Any = None
    last_body: bytes | None = None
    try:
        for _ in range(_SIGNALR_NODE_RETRIES):
            # Healthy long-polls take roughly 20-25s; other operations should
            # fail quickly enough for the official client to reconnect.
            response = await route.fetch(timeout=_SIGNALR_TIMEOUT_MS[operation])
            body = await response.body()
            last_response = response
            last_body = body
            if not _is_signalr_node_error(response.status, body):
                await route.fulfill(response=response, body=body)
                return
        if last_response is not None:
            await route.fulfill(response=last_response, body=last_body)
            return
    except Exception as exc:
        logger.debug("同程 SignalR 节点重试中止：%s", exc)

    try:
        # Never resend a potentially already-consumed poll outside the guarded
        # retry path. Aborting lets SignalR reconnect with its messageId.
        await route.abort("failed")
    except Exception:
        # Browser/context shutdown can dispose an in-flight long poll.
        pass


@asynccontextmanager
async def _temporary_qr_image(
    qr_element: Any,
    on_qr_ready: Callable[[str], None] | None,
) -> AsyncIterator[Path]:
    """Screenshot a QR element, notify the caller, and always delete the image."""
    fd, temporary_name = tempfile.mkstemp(prefix="tongcheng_qr_", suffix=".png")
    os.close(fd)
    qr_path = Path(temporary_name)
    try:
        await qr_element.screenshot(path=str(qr_path))
        if not qr_path.exists() or qr_path.stat().st_size == 0:
            raise ValueError("同程二维码截图为空")
        _add_qr_quiet_zone(qr_path)
        if on_qr_ready is not None:
            on_qr_ready(str(qr_path))
        yield qr_path
    finally:
        qr_path.unlink(missing_ok=True)


def _add_qr_quiet_zone(path: Path, border: int = 16) -> None:
    """Add the white quiet zone required by QR readers around Tongcheng's image."""
    if border <= 0:
        return
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            image = source.convert("RGB")
            padded = ImageOps.expand(image, border=border, fill="white")
            # Chromium can rasterize this CSS-sized element below the source
            # JPEG's native 231 px. Use nearest-neighbour scaling so the
            # module grid stays crisp on high-density phone displays.
            if padded.width < 300:
                scale = max(2, (300 + padded.width - 1) // padded.width)
                padded = padded.resize(
                    (padded.width * scale, padded.height * scale),
                    Image.Resampling.NEAREST,
                )
            padded.save(path, format="PNG")
    except Exception as exc:
        # Pillow is already an optional dependency for terminal rendering. The
        # browser UI still gives the image CSS whitespace if it is unavailable.
        logger.warning("无法为同程二维码添加白边，部分扫码器可能难以识别：%s", exc)


async def _run_app_qr_flow(
    *,
    headless: bool,
    timeout: int,
    on_qr_ready: Callable[[str], None] | None,
) -> list[dict[str, Any]] | None:
    """Run the browser flow and return cookies only after confirmed login."""
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser: Any = None
    try:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined, configurable: true});"
        )
        # Avoid an eager QR connection before event listeners and UI switching are ready.
        await context.add_cookies(
            [{"name": "usLoginType", "value": "0", "domain": "passport.ly.com", "path": "/"}]
        )
        # Register before navigation: the page can initialize SignalR as soon
        # as its official appQrCode bundle is evaluated.
        await context.route(
            "https://passport.ly.com/qrcode/connection/**",
            _retry_misdirected_signalr,
        )

        page = await context.new_page()
        login_confirmed = asyncio.Event()

        async def on_response(response: Any) -> None:
            if not _is_login_with_app_url(response.url):
                return
            payload = await _read_response_json(response)
            if _login_response_succeeded(payload):
                logger.info("同程 LoginWithApp 已确认登录成功")
                login_confirmed.set()

        page.on("response", on_response)

        logger.info("打开同程旅行官方登录页")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)

        if not await _activate_qr_login(page):
            return None

        qr_started_at = time.monotonic()
        qr_wait = min(float(timeout), _QR_READY_TIMEOUT)
        qr_element = await _wait_for_qr(page, qr_wait)
        if qr_element is None:
            logger.error("同程登录节点会话路由异常或二维码生成超时，请稍后重试")
            return None

        async with _temporary_qr_image(qr_element, on_qr_ready) as qr_path:
            logger.info("同程二维码已生成")
            print("\n请使用同程旅行 App 扫描二维码并在手机上确认登录：")
            _print_qr_terminal(str(qr_path))

            qr_elapsed = time.monotonic() - qr_started_at
            remaining = max(0.1, float(timeout) - qr_elapsed)
            try:
                await asyncio.wait_for(login_confirmed.wait(), timeout=remaining)
            except TimeoutError:
                logger.error("等待扫码确认超时")
                return None

        # Allow redirect and all Set-Cookie headers to settle. A follow-up official page
        # visit also materialises cookies shared from passport.ly.com to www.ly.com.
        await asyncio.sleep(1.5)
        try:
            await page.goto(FLIGHTS_URL, wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("登录后访问机票页失败（不影响已确认登录）：%s", exc)

        cookies = _filter_ly_cookies(await context.cookies())
        if not cookies:
            logger.error("登录成功但未获取到 ly.com Cookie")
            return None
        return cookies
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                logger.debug("关闭同程登录浏览器失败", exc_info=True)
        try:
            await playwright.stop()
        except Exception:
            logger.debug("关闭 Playwright 失败", exc_info=True)


async def _run_wechat_qr_flow(
    *,
    headless: bool,
    timeout: int,
    on_qr_ready: Callable[[str], None] | None,
) -> list[dict[str, Any]] | None:
    """Log in to Tongcheng through its official WeChat OAuth QR entry.

    Tongcheng App QR currently performs a second SignalR handshake inside the
    phone WebView. That mobile request is affected by Tongcheng's broken node
    affinity and cannot be repaired from this server. The official WeChat OAuth
    entry reaches the same Tongcheng account callback without that handshake.
    """
    from playwright.async_api import async_playwright

    deadline = time.monotonic() + float(timeout)
    playwright = await async_playwright().start()
    browser: Any = None
    try:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined, configurable: true});"
        )
        page = await context.new_page()
        oauth_completed = asyncio.Event()

        async def on_response(response: Any) -> None:
            # Reaching the trusted callback only means that OAuth returned.
            # Even an invalid code can receive a 302, so strong member Cookies
            # are checked separately before success is reported or persisted.
            if _is_wechat_callback_url(response.url):
                oauth_completed.set()

        page.on("response", on_response)
        logger.info("打开同程旅行官方登录页并获取微信 OAuth 二维码")
        await page.goto(
            LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=_navigation_timeout_ms(deadline),
        )

        oauth_link = await page.locator("a.sp_weix").first.get_attribute("data-href")
        if not _is_wechat_oauth_url(oauth_link):
            logger.error("同程官方微信登录入口缺失或地址校验失败")
            return None

        await page.goto(
            oauth_link,
            wait_until="domcontentloaded",
            timeout=_navigation_timeout_ms(deadline),
        )
        qr_element = await _wait_for_wechat_qr(
            page, min(_remaining_seconds(deadline), _QR_READY_TIMEOUT)
        )
        if qr_element is None:
            logger.error("微信 OAuth 二维码生成超时，请稍后重试")
            return None

        async with _temporary_qr_image(qr_element, on_qr_ready) as qr_path:
            logger.info("同程微信登录二维码已生成")
            print("\n请使用微信扫一扫，并确认登录同程旅行：")
            _print_qr_terminal(str(qr_path))
            try:
                await asyncio.wait_for(
                    oauth_completed.wait(),
                    timeout=max(0.1, _remaining_seconds(deadline)),
                )
            except TimeoutError:
                logger.error("等待微信扫码确认超时")
                return None

        # Passport may still be completing a redirect chain after the callback
        # response. Give it a short bounded window to set an authenticated
        # member marker. An unbound WeChat account must not replace good Cookie
        # data with anonymous session cookies.
        cookies = await _wait_for_authenticated_member(
            context,
            min(15.0, _remaining_seconds(deadline)),
        )
        if not cookies:
            logger.error(
                "微信授权未形成已登录同程账号；请确认该微信已绑定同程账号，"
                "或改用手动上传 Cookie"
            )
            return None

        try:
            await page.goto(
                FLIGHTS_URL,
                wait_until="domcontentloaded",
                timeout=_navigation_timeout_ms(deadline, cap_seconds=15.0),
            )
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("微信登录后访问机票页失败（不影响已确认登录）：%s", exc)

        cookies = _filter_ly_cookies(await context.cookies())
        if not _has_authenticated_member(cookies):
            logger.error("微信授权完成后同程登录态丢失，已有 Cookie 未被修改")
            return None
        return cookies
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                logger.debug("关闭同程微信登录浏览器失败", exc_info=True)
        try:
            await playwright.stop()
        except Exception:
            logger.debug("关闭 Playwright 失败", exc_info=True)


async def _run_qr_flow(
    *,
    headless: bool,
    timeout: int,
    on_qr_ready: Callable[[str], None] | None,
) -> list[dict[str, Any]] | None:
    """Use Tongcheng's official WeChat OAuth QR as the reliable default."""
    return await _run_wechat_qr_flow(
        headless=headless,
        timeout=timeout,
        on_qr_ready=on_qr_ready,
    )


async def qr_login(
    headless: bool = True,
    output_path: str = OUTPUT_PATH,
    timeout: int = 300,
    on_qr_ready: Callable[[str], None] | None = None,
) -> bool:
    """Refresh Tongcheng cookies through its official WeChat OAuth QR.

    The signature intentionally matches the Qunar and Ctrip login scripts so the
    Cookie API can invoke all platforms through one background state machine.
    """
    if timeout <= 0:
        logger.error("timeout 必须大于 0")
        return False

    print("\n" + "=" * 60)
    print("同程旅行 Cookie 刷新工具（扫码登录）")
    print("=" * 60)
    try:
        cookies = await _run_qr_flow(
            headless=headless,
            timeout=timeout,
            on_qr_ready=on_qr_ready,
        )
        cookies = _filter_ly_cookies(cookies or [])
        if not _has_authenticated_member(cookies):
            print("✗ 扫码登录未完成，已有 Cookie 未被修改")
            return False
        _write_cookies_atomically(output_path, cookies)
    except Exception as exc:
        logger.error("同程扫码登录失败：%s", exc)
        print("✗ 已有 Cookie 未被修改")
        return False

    print(f"✓ 登录成功，已保存 {len(cookies)} 条 ly.com Cookie → {output_path}")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description="同程旅行 Cookie 刷新工具（扫码登录）")
    parser.add_argument(
        "--headless", action="store_true", help="无头模式（服务器使用，默认有头）"
    )
    parser.add_argument("--output", default=OUTPUT_PATH, help="Cookie 输出路径")
    parser.add_argument("--timeout", type=int, default=300, help="等待扫码最大秒数")
    args = parser.parse_args()
    success = await qr_login(
        headless=args.headless,
        output_path=args.output,
        timeout=args.timeout,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
