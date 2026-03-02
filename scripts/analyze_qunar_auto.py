#!/usr/bin/env python3
"""自动分析去哪儿网登录二维码位置（自动化版本）"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from playwright.async_api import async_playwright


async def auto_analyze():
    """自动分析去哪儿网登录结构"""
    url = "https://flight.qunar.com/site/oneway_list.htm?fromCity=%E4%B8%8A%E6%B5%B7&toCity=%E6%88%90%E9%83%BD&fromDate=2026-03-06&from=flight_dom_search"

    print("开始分析去哪儿网登录结构...\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)

            # 保存截图和HTML
            await page.screenshot(path="qunar_auto_debug.png", full_page=True)
            html = await page.content()
            with open("qunar_auto_debug.html", "w", encoding="utf-8") as f:
                f.write(html)

            print(f"✓ 页面标题: {await page.title()}")
            print(f"✓ 当前URL: {page.url}\n")

            # 分析结果
            results = {}

            # 1. 检查登录模态框
            modal_selectors = [
                ".login-modal", ".qrcode-modal", "[id*='login']",
                "[class*='login']", "[class*='qrcode']", "[class*='QRCode']"
            ]

            print("=== 登录模态框检测 ===")
            for sel in modal_selectors:
                elements = await page.locator(sel).all()
                if elements:
                    for i, elem in enumerate(elements):
                        visible = await elem.is_visible()
                        class_name = await elem.get_attribute("class")
                        print(f"✓ {sel}: 可见={visible}, class={class_name}")

            # 2. 检查图片元素
            print("\n=== 图片元素检测 ===")
            images = await page.locator("img").all()
            print(f"总图片数: {len(images)}")

            qr_related = []
            for img in images[:50]:
                src = await img.get_attribute("src") or ""
                alt = await img.get_attribute("alt") or ""
                class_name = await img.get_attribute("class") or ""

                if any(keyword in src.lower() + alt.lower() + class_name.lower()
                       for keyword in ["qr", "code", "scan", "login"]):
                    visible = await img.is_visible()
                    qr_related.append({
                        "src": src[:100],
                        "alt": alt,
                        "class": class_name,
                        "visible": visible
                    })

            if qr_related:
                print(f"找到 {len(qr_related)} 个可能的二维码图片:")
                for i, img in enumerate(qr_related):
                    print(f"  [{i+1}] src: {img['src']}")
                    print(f"      class: {img['class']}")
                    print(f"      可见: {img['visible']}\n")

            # 3. 检查Canvas
            print("=== Canvas元素检测 ===")
            canvases = await page.locator("canvas").all()
            print(f"找到 {len(canvases)} 个canvas")
            for i, canvas in enumerate(canvases[:10]):
                class_name = await canvas.get_attribute("class") or ""
                id_attr = await canvas.get_attribute("id") or ""
                visible = await canvas.is_visible()
                print(f"  Canvas {i+1}: class={class_name}, id={id_attr}, 可见={visible}")

            # 4. 搜索HTML中的关键字
            print("\n=== HTML关键字搜索 ===")
            keywords = ["qrcode", "QRCode", "二维码", "扫码", "login-qr"]
            for keyword in keywords:
                if keyword in html:
                    count = html.count(keyword)
                    print(f"✓ '{keyword}' 出现 {count} 次")

            # 5. 检查iframe
            print("\n=== IFrame检测 ===")
            print(f"Frame数量: {len(page.frames)}")
            for i, frame in enumerate(page.frames[:5]):
                print(f"  Frame {i+1}: {frame.url[:80]}")

            print("\n✓ 分析完成！")
            print("文件已保存:")
            print("  - qunar_auto_debug.png")
            print("  - qunar_auto_debug.html")

        except Exception as e:
            print(f"✗ 错误: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(auto_analyze())
