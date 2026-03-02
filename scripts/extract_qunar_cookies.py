#!/usr/bin/env python3
"""Script to extract Qunar cookies for authentication.

This script helps users extract cookies from their browser after logging in to Qunar,
which can then be used for authenticated scraping in headless mode.

Usage:
    python scripts/extract_qunar_cookies.py
"""

import asyncio
import json
import logging
from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def extract_cookies():
    """Extract Qunar cookies by opening a browser for user login."""
    logger.info("启动浏览器，请在浏览器中登录去哪儿网...")
    logger.info("登录完成后，按回车键继续提取cookies")

    async with async_playwright() as p:
        # Launch browser in non-headless mode
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        page = await context.new_page()

        # Navigate to Qunar flight search page
        await page.goto(
            "https://flight.qunar.com/",
            wait_until="networkidle",
        )

        # Wait for user to login
        input("\n请在浏览器中完成登录，然后按回车键继续...")

        # Get all cookies
        cookies = await context.cookies()

        # Filter Qunar-related cookies
        qunar_cookies = [
            {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": cookie["domain"],
                "path": cookie.get("path", "/"),
                "expires": cookie.get("expires", -1),
                "httpOnly": cookie.get("httpOnly", False),
                "secure": cookie.get("secure", False),
                "sameSite": cookie.get("sameSite", "Lax"),
            }
            for cookie in cookies
            if "qunar" in cookie["domain"]
        ]

        await browser.close()

        if not qunar_cookies:
            logger.warning("未找到去哪儿网的cookies，请确保已登录")
            return None

        logger.info(f"成功提取 {len(qunar_cookies)} 个cookies")

        # Format cookies as JSON string
        cookies_json = json.dumps(qunar_cookies, ensure_ascii=False, indent=2)

        # Save to file
        output_file = "qunar_cookies.json"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(cookies_json)

        logger.info(f"Cookies已保存到 {output_file}")

        # Also print for .env file
        print("\n" + "=" * 80)
        print("将以下内容添加到 .env 文件中：")
        print("=" * 80)
        print(f'QUNAR_COOKIES=\'{cookies_json}\'')
        print("=" * 80)

        return qunar_cookies


def main():
    """Main entry point."""
    try:
        cookies = asyncio.run(extract_cookies())
        if cookies:
            logger.info("\nCookies提取成功！")
            logger.info("您可以在 .env 文件中设置 QUNAR_COOKIES 来使用这些cookies")
        else:
            logger.error("Cookies提取失败")
    except KeyboardInterrupt:
        logger.info("\n用户中断")
    except Exception as e:
        logger.error(f"发生错误: {e}", exc_info=True)


if __name__ == "__main__":
    main()
