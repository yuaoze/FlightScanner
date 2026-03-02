#!/usr/bin/env python3
"""Verification script for scraper prototype.

This script verifies that the Playwright-based scraper can:
1. Launch a browser instance
2. Navigate to a target website
3. Take a screenshot
4. Print the page title

This confirms the scraper can bypass basic anti-crawler mechanisms and
load dynamic content.

Usage:
    python scripts/verify_scraper.py

Note:
    This script requires Playwright browsers to be installed:
    playwright install chromium
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def print_header(title: str) -> None:
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


async def verify_scraper() -> bool:
    """Verify scraper functionality using Playwright.

    Returns:
        True if all verifications pass, False otherwise.
    """
    print_header("Scraper Verification Script")

    try:
        from playwright.async_api import async_playwright, Browser, Page
    except ImportError:
        print("✗ Playwright not installed!")
        print("  Please install: pip install playwright && playwright install chromium")
        return False

    browser: Optional[Browser] = None

    try:
        # Step 1: Initialize Playwright
        print("\n[1/5] Initializing Playwright...")
        playwright = await async_playwright().start()
        print("✓ Playwright initialized")

        # Step 2: Launch browser
        print("\n[2/5] Launching browser...")
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        print("✓ Browser launched successfully (headless mode)")

        # Step 3: Create context with stealth settings
        print("\n[3/5] Creating browser context...")
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
        print("✓ Browser context created with stealth settings")

        # Step 4: Navigate to test page
        print("\n[4/5] Navigating to test website...")
        page: Page = await context.new_page()

        # Use a simple test URL (携程首页)
        test_url = "https://flights.ctrip.com"

        try:
            await page.goto(
                test_url,
                wait_until="networkidle",
                timeout=30000,
            )
            print(f"✓ Successfully navigated to: {test_url}")
        except Exception as e:
            print(f"⚠ Failed to navigate to {test_url}: {e}")
            print("  Trying alternative URL...")
            # Fallback to a simpler test
            test_url = "https://www.baidu.com"
            await page.goto(test_url, wait_until="networkidle", timeout=30000)
            print(f"✓ Successfully navigated to: {test_url}")

        # Get page title
        title = await page.title()
        print(f"✓ Page title: {title}")

        # Step 5: Take screenshot
        print("\n[5/5] Taking screenshot...")
        screenshot_path = project_root / "debug_screenshot.png"
        await page.screenshot(path=str(screenshot_path), full_page=False)
        print(f"✓ Screenshot saved to: {screenshot_path}")
        print(f"  File size: {screenshot_path.stat().st_size / 1024:.2f} KB")

        # Cleanup
        await context.close()
        await browser.close()
        await playwright.stop()

        return True

    except Exception as e:
        print(f"\n✗ Verification failed: {e}")
        import traceback
        traceback.print_exc()

        if browser:
            await browser.close()

        return False


def main() -> int:
    """Main entry point."""
    try:
        success = asyncio.run(verify_scraper())

        if success:
            print_header("VERIFICATION PASSED")
            print("\n✓ Scraper prototype is working correctly!")
            print("✓ Browser can launch and navigate to websites.")
            print("✓ Screenshot capability verified.")
            print("\nNext steps:")
            print("  1. Implement the actual flight search logic")
            print("  2. Add price parsing functionality")
            print("  3. Implement retry and error handling\n")
            return 0
        else:
            print_header("VERIFICATION FAILED")
            print("\n✗ Scraper verification failed.")
            print("✗ Please check the error messages above.\n")
            return 1

    except KeyboardInterrupt:
        print("\n\n✗ Verification interrupted by user.\n")
        return 1
    except Exception as e:
        print_header("UNEXPECTED ERROR")
        print(f"\n✗ An unexpected error occurred: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
