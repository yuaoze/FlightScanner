"""通过飞猪官方淘宝扫码入口更新独立登录 Cookie。"""

import argparse
import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)
LOGIN_URL = (
    "https://login.taobao.com/member/login.jhtml?f=top"
    "&redirectURL=https%3A%2F%2Fwww.fliggy.com%2F"
)
OUTPUT_PATH = str(Path(__file__).resolve().parents[1] / "fliggy_cookies.json")
COOKIE_DOMAINS = {"fliggy.com", "taobao.com", "alitrip.com"}


def _is_platform_cookie(cookie: dict) -> bool:
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    return any(domain == root or domain.endswith("." + root) for root in COOKIE_DOMAINS)


def _has_authenticated_cookie(cookies: list[dict]) -> bool:
    names = {
        cookie.get("name")
        for cookie in cookies
        if _is_platform_cookie(cookie)
        and str(cookie.get("value", "")).strip().lower() not in {"", "0", "null", "none"}
    }
    return {"cookie2", "unb"}.issubset(names)


def _write_cookies_atomically(path: str | Path, cookies: list[dict]) -> None:
    output = Path(path)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cookies, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


async def qr_login(
    headless: bool = True,
    output_path: str = OUTPUT_PATH,
    timeout: int = 120,
    on_qr_ready: Callable[[str], None] | None = None,
) -> bool:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(locale="zh-CN")
            page = await context.new_page()
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            qr = page.locator("#qrcode-img canvas")
            await qr.wait_for(state="visible", timeout=30000)
            with tempfile.TemporaryDirectory(prefix="flightscanner-fliggy-login-") as directory:
                qr_path = str(Path(directory) / "qr.png")
                await qr.screenshot(path=qr_path)
                if on_qr_ready:
                    on_qr_ready(qr_path)
                logger.info("请使用淘宝 App 扫码并确认登录，二维码：%s", qr_path)
                deadline = asyncio.get_running_loop().time() + timeout
                while asyncio.get_running_loop().time() < deadline:
                    cookies = [c for c in await context.cookies() if _is_platform_cookie(c)]
                    host = urlsplit(page.url).hostname or ""
                    if _has_authenticated_cookie(cookies) and (
                        host == "fliggy.com" or host.endswith(".fliggy.com")
                    ):
                        _write_cookies_atomically(output_path, cookies)
                        logger.info("飞猪登录 Cookie 已更新（%d 条）", len(cookies))
                        return True
                    if page.is_closed():
                        break
                    await asyncio.sleep(1)
                logger.warning("飞猪扫码登录未完成，保留原有 Cookie")
                return False
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="飞猪淘宝扫码登录（默认不弹浏览器）")
    parser.add_argument("--output", default=OUTPUT_PATH)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(
        0 if asyncio.run(qr_login(output_path=args.output, timeout=args.timeout)) else 1
    )
