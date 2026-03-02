#!/usr/bin/env python3
"""详细调试：检查页面元素"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from playwright.async_api import async_playwright


async def debug_page_elements():
    """调试页面元素"""
    url = "https://flight.qunar.com/site/oneway_list.htm?fromCity=%E4%B8%8A%E6%B5%B7&toCity=%E6%88%90%E9%83%BD&fromDate=2026-03-06&from=flight_dom_search"

    print("=" * 70)
    print("调试去哪儿网页面元素（带反检测）")
    print("=" * 70)

    async with async_playwright() as p:
        # 使用与QunarScraper相同的配置
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
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
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )

        # Anti-detection script
        await context.add_init_script("""
            // Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false,
            });

            // Mock chrome property
            window.chrome = {
                runtime: {},
            };

            // Mock plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });

            // Mock languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en'],
            });

            // Mock permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)

        page = await context.new_page()

        try:
            print(f"\n1. 访问页面: {url[:80]}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            print(f"   ✓ 页面加载完成")

            # 等待几秒让登录弹窗出现
            print("\n2. 等待8秒让登录弹窗出现...")
            await asyncio.sleep(8)

            # 保存截图
            await page.screenshot(path="debug_page_antidetect.png", full_page=True)
            print("   ✓ 截图已保存: debug_page_antidetect.png")

            # 检查 webdriver 标志
            print("\n3. 检查反检测是否生效...")
            webdriver_value = await page.evaluate("navigator.webdriver")
            chrome_value = await page.evaluate("typeof window.chrome")
            print(f"   navigator.webdriver = {webdriver_value} (应该是 False)")
            print(f"   window.chrome = {chrome_value} (应该是 'object')")

            # 检查登录按钮状态 (新增)
            print("\n4. 检查登录按钮...")
            login_button = await page.query_selector("#__headerInfo_login__")
            if login_button:
                is_visible = await login_button.is_visible()
                print(f"   ✓ 找到登录按钮 (#__headerInfo_login__)")
                print(f"   登录按钮可见: {is_visible}")
                if is_visible:
                    print("   → 用户未登录，需要登录")
                    # 点击登录按钮触发弹窗
                    try:
                        await login_button.click()
                        print("   ✓ 已点击登录按钮")
                        await asyncio.sleep(3)  # 等待弹窗出现
                    except Exception as e:
                        print(f"   ✗ 点击失败: {e}")
                else:
                    print("   → 用户已登录")
            else:
                print("   ✗ 未找到登录按钮")

            # 检查 .login_QR_imgs
            print("\n5. 检查 .login_QR_imgs 元素...")
            login_qr_divs = await page.query_selector_all(".login_QR_imgs")
            print(f"   找到 {len(login_qr_divs)} 个 .login_QR_imgs 元素")

            for i, div in enumerate(login_qr_divs):
                visible = await div.is_visible()
                html = await div.inner_html()
                print(f"\n   元素 {i+1}:")
                print(f"   - 可见: {visible}")
                print(f"   - HTML: {html[:200]}...")

            # 检查二维码图片
            print("\n6. 检查二维码图片...")
            qr_imgs = await page.query_selector_all("img[src*='qcode']")
            print(f"   找到 {len(qr_imgs)} 个包含'qcode'的图片")

            for i, img in enumerate(qr_imgs):
                src = await img.get_attribute("src")
                visible = await img.is_visible()
                print(f"\n   图片 {i+1}:")
                print(f"   - src: {src}")
                print(f"   - 可见: {visible}")

                if visible:
                    try:
                        screenshot = await img.screenshot()
                        with open(f"qr_img_antidetect_{i+1}.png", "wb") as f:
                            f.write(screenshot)
                        print(f"   ✓ 已保存截图: qr_img_antidetect_{i+1}.png")
                    except Exception as e:
                        print(f"   ✗ 截图失败: {e}")

            # 检查所有class包含login的元素
            print("\n7. 检查class包含'login'的可见元素...")
            login_elements = await page.query_selector_all("[class*='login']")
            visible_count = 0
            for elem in login_elements:
                if await elem.is_visible():
                    visible_count += 1
                    if visible_count <= 5:
                        tag = await elem.evaluate("el => el.tagName")
                        class_name = await elem.get_attribute("class")
                        print(f"   [{visible_count}] <{tag}> class='{class_name}'")

            print(f"   总共 {len(login_elements)} 个元素，{visible_count} 个可见")

            # 检查URL
            print(f"\n8. 当前URL: {page.url}")

            # 使用JavaScript检查DOM
            print("\n9. 使用JavaScript检查登录相关元素...")
            js_result = await page.evaluate("""
                () => {
                    const results = {};

                    // 检查 .login_QR_imgs
                    const qrDiv = document.querySelector('.login_QR_imgs');
                    results.qrDiv = qrDiv ? {
                        exists: true,
                        visible: qrDiv.offsetParent !== null,
                        innerHTML: qrDiv.innerHTML.substring(0, 200)
                    } : { exists: false };

                    // 检查所有包含'login'或'qr'的可见div
                    const allDivs = document.querySelectorAll('div[class*="login"], div[class*="qr"], div[class*="QR"]');
                    results.loginDivs = Array.from(allDivs)
                        .filter(el => el.offsetParent !== null)
                        .map(el => ({
                            class: el.className,
                            visible: true
                        }))
                        .slice(0, 10);

                    return results;
                }
            """)
            print(f"   JavaScript检查结果:")
            print(f"   - .login_QR_imgs 存在: {js_result.get('qrDiv', {}).get('exists', False)}")
            if js_result.get('qrDiv', {}).get('exists'):
                print(f"   - .login_QR_imgs 可见: {js_result['qrDiv'].get('visible', False)}")
            print(f"   - 找到 {len(js_result.get('loginDivs', []))} 个可见的login/qr相关div")

            # 保持浏览器打开
            print("\n" + "=" * 70)
            print("浏览器保持打开，请手动检查页面")
            print("观察是否有登录弹窗")
            print("按Enter继续...")
            print("=" * 70)
            input()

        except Exception as e:
            print(f"\n✗ 错误: {e}")
            import traceback
            traceback.print_exc()

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(debug_page_elements())
