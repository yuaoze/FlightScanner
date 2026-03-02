#!/usr/bin/env python3
"""调试脚本：分析去哪儿网登录二维码的具体位置和结构"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from playwright.async_api import async_playwright


async def analyze_qunar_login():
    """访问去哪儿网并分析登录二维码的DOM结构"""

    url = "https://flight.qunar.com/site/oneway_list.htm?fromCity=%E4%B8%8A%E6%B5%B7&toCity=%E6%88%90%E9%83%BD&fromDate=2026-03-06&from=flight_dom_search"

    print("=" * 60)
    print("去哪儿网登录二维码调试分析")
    print("=" * 60)
    print(f"\n访问URL: {url}\n")

    async with async_playwright() as p:
        # 启动浏览器
        browser = await p.chromium.launch(
            headless=False,  # 非无头模式，方便观察
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
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

        try:
            print("1. 正在访问页面...")
            await page.goto(url, wait_until="networkidle", timeout=30000)

            await asyncio.sleep(3)  # 等待页面完全加载

            # 保存页面截图
            screenshot_path = "qunar_page_debug.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"✓ 页面截图已保存: {screenshot_path}")

            # 获取页面标题
            title = await page.title()
            print(f"✓ 页面标题: {title}")

            # 检查是否有登录相关的元素
            print("\n2. 检查登录相关元素...")

            # 方法1: 查找包含"登录"或"二维码"的文本
            login_texts = await page.locator("text=/登录|二维码|扫码/i").all()
            if login_texts:
                print(f"✓ 找到 {len(login_texts)} 个登录相关文本元素")

            # 方法2: 查找常见的登录模态框
            modal_selectors = [
                ".login-modal",
                ".qrcode-modal",
                "[class*='login']",
                "[class*='qrcode']",
                "[class*='QRCode']",
                "#login-modal",
                "#qrcode-modal",
            ]

            for selector in modal_selectors:
                elements = await page.locator(selector).all()
                if elements:
                    print(f"✓ 找到元素: {selector} (数量: {len(elements)})")
                    for i, elem in enumerate(elements):
                        is_visible = await elem.is_visible()
                        print(f"  - 元素 {i+1}: 可见={is_visible}")

            # 方法3: 查找所有图片，看是否有二维码
            print("\n3. 检查页面中的图片元素...")
            images = await page.locator("img").all()
            print(f"✓ 找到 {len(images)} 个图片元素")

            for i, img in enumerate(images[:20]):  # 只检查前20个
                src = await img.get_attribute("src")
                alt = await img.get_attribute("alt")
                class_name = await img.get_attribute("class")
                is_visible = await img.is_visible()

                if src and ("qr" in src.lower() or "code" in src.lower()):
                    print(f"\n  图片 {i+1} (可能的二维码):")
                    print(f"    src: {src[:100]}")
                    print(f"    alt: {alt}")
                    print(f"    class: {class_name}")
                    print(f"    可见: {is_visible}")

            # 方法4: 查找canvas元素（二维码可能用canvas绘制）
            print("\n4. 检查canvas元素...")
            canvases = await page.locator("canvas").all()
            if canvases:
                print(f"✓ 找到 {len(canvases)} 个canvas元素")
                for i, canvas in enumerate(canvases):
                    class_name = await canvas.get_attribute("class")
                    is_visible = await canvas.is_visible()
                    print(f"  Canvas {i+1}: class={class_name}, 可见={is_visible}")

            # 方法5: 检查iframe（登录可能在iframe中）
            print("\n5. 检查iframe元素...")
            frames = page.frames
            print(f"✓ 找到 {len(frames)} 个frame")
            for i, frame in enumerate(frames):
                print(f"  Frame {i+1}: {frame.url[:80]}")

            # 方法6: 保存页面HTML
            print("\n6. 保存页面HTML...")
            html_content = await page.content()
            html_path = "qunar_page_debug.html"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"✓ HTML已保存: {html_path}")

            # 方法7: 检查是否触发了登录拦截
            print("\n7. 检查页面URL和可能的重定向...")
            current_url = page.url
            print(f"✓ 当前URL: {current_url}")

            if "login" in current_url.lower():
                print("⚠ 页面被重定向到登录页面")

            # 等待用户观察
            print("\n" + "=" * 60)
            print("浏览器窗口保持打开，请手动检查页面")
            print("查看是否出现登录弹窗或二维码")
            print("按Enter键继续分析或Ctrl+C退出...")
            print("=" * 60)

            input()

            # 再次检查是否有弹窗出现
            print("\n8. 再次检查登录弹窗...")

            # 尝试查找所有可见的模态框
            visible_modals = await page.locator("[class*='modal']:visible, [class*='dialog']:visible, [class*='popup']:visible").all()
            if visible_modals:
                print(f"✓ 找到 {len(visible_modals)} 个可见的模态框/弹窗")

                for i, modal in enumerate(visible_modals):
                    print(f"\n  模态框 {i+1}:")
                    class_name = await modal.get_attribute("class")
                    print(f"    class: {class_name}")

                    # 在这个模态框中查找二维码
                    qr_imgs = await modal.locator("img[src*='qr'], img[class*='qr'], img[class*='code']").all()
                    if qr_imgs:
                        print(f"    ✓ 找到 {len(qr_imgs)} 个可能的二维码图片")
                        for j, qr_img in enumerate(qr_imgs):
                            src = await qr_img.get_attribute("src")
                            print(f"      二维码 {j+1}: {src[:100]}")

                    # 查找canvas
                    qr_canvases = await modal.locator("canvas").all()
                    if qr_canvases:
                        print(f"    ✓ 找到 {len(qr_canvases)} 个canvas元素")

            # 保存最终截图
            final_screenshot = "qunar_page_final.png"
            await page.screenshot(path=final_screenshot, full_page=True)
            print(f"\n✓ 最终截图已保存: {final_screenshot}")

        except Exception as e:
            print(f"\n✗ 错误: {e}")
            import traceback
            traceback.print_exc()

        finally:
            print("\n关闭浏览器...")
            await browser.close()

    print("\n分析完成！")
    print("请查看以下文件:")
    print("  - qunar_page_debug.png (初始截图)")
    print("  - qunar_page_debug.html (页面HTML)")
    print("  - qunar_page_final.png (最终截图)")


if __name__ == "__main__":
    asyncio.run(analyze_qunar_login())
