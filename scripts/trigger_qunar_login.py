#!/usr/bin/env python3
"""点击登录按钮，触发并捕获二维码"""

import asyncio
import sys
import base64
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from playwright.async_api import async_playwright


async def trigger_login_qrcode():
    """触发登录并捕获二维码"""
    url = "https://flight.qunar.com/site/oneway_list.htm?fromCity=%E4%B8%8A%E6%B5%B7&toCity=%E6%88%90%E9%83%BD&fromDate=2026-03-06&from=flight_dom_search"

    print("=" * 70)
    print("触发去哪儿网登录二维码")
    print("=" * 70)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=1000)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            print(f"\n1. 访问页面: {url}")
            await page.goto(url, wait_until="networkidle")
            await asyncio.sleep(3)

            # 保存初始截图
            await page.screenshot(path="01_initial.png")
            print("   ✓ 初始截图: 01_initial.png")

            # 查找并点击登录按钮
            print("\n2. 查找登录按钮...")

            login_selectors = [
                "text=/登录/",
                ".q_header_username",
                "#J_loginBtn",
                "a[href*='login']",
                "button:has-text('登录')",
                "[class*='login']",
            ]

            login_clicked = False
            for selector in login_selectors:
                try:
                    elements = await page.locator(selector).all()
                    if elements:
                        print(f"   找到选择器: {selector}, 数量: {len(elements)}")
                        for i, elem in enumerate(elements):
                            try:
                                visible = await elem.is_visible()
                                if visible:
                                    text = await elem.inner_text()
                                    print(f"   尝试点击: {selector} (文本: {text[:20]})")
                                    await elem.click(timeout=5000)
                                    login_clicked = True
                                    print("   ✓ 点击成功!")
                                    break
                            except:
                                continue
                    if login_clicked:
                        break
                except Exception as e:
                    continue

            if not login_clicked:
                print("   ⚠ 未找到可点击的登录按钮，尝试直接查找二维码...")

            # 等待登录弹窗出现
            await asyncio.sleep(3)
            await page.screenshot(path="02_after_click.png")
            print("   ✓ 点击后截图: 02_after_click.png")

            # 查找二维码
            print("\n3. 查找登录二维码...")

            # 所有可能的二维码选择器
            qrcode_selectors = [
                "img[src*='qr']",
                "img[src*='QR']",
                "img[alt*='二维码']",
                "img[alt*='扫码']",
                "canvas[class*='qr']",
                "canvas[class*='QR']",
                "[class*='qrcode'] img",
                "[class*='QRCode'] img",
                "[id*='qrcode'] img",
                "[id*='QRCode'] img",
                ".qrcode-img",
                "#qrcode-img",
                "[class*='login'] canvas",
                "[class*='login'] img[src*='data:image']",
            ]

            qr_found = False
            for selector in qrcode_selectors:
                try:
                    elements = await page.locator(selector).all()
                    if elements:
                        print(f"   找到选择器: {selector}, 数量: {len(elements)}")

                        for i, elem in enumerate(elements):
                            try:
                                visible = await elem.is_visible()
                                if visible:
                                    print(f"   ✓ 找到可见的二维码元素! (选择器: {selector})")

                                    # 尝试获取图片
                                    tag_name = await elem.evaluate("el => el.tagName.toLowerCase()")

                                    if tag_name == "img":
                                        src = await elem.get_attribute("src")
                                        alt = await elem.get_attribute("alt")
                                        print(f"     - 标签: img")
                                        print(f"     - src: {src[:100] if src else 'None'}")
                                        print(f"     - alt: {alt}")

                                        # 截取二维码元素
                                        qr_screenshot = await elem.screenshot()
                                        with open(f"qrcode_element_{i+1}.png", "wb") as f:
                                            f.write(qr_screenshot)
                                        print(f"     ✓ 二维码已保存: qrcode_element_{i+1}.png")

                                        # 如果是base64图片，保存原始数据
                                        if src and src.startswith("data:image"):
                                            try:
                                                img_data = src.split(",")[1] if "," in src else src
                                                with open(f"qrcode_base64_{i+1}.png", "wb") as f:
                                                    f.write(base64.b64decode(img_data))
                                                print(f"     ✓ Base64二维码已解码: qrcode_base64_{i+1}.png")
                                            except:
                                                pass

                                        qr_found = True

                                    elif tag_name == "canvas":
                                        print(f"     - 标签: canvas")
                                        qr_screenshot = await elem.screenshot()
                                        with open(f"qrcode_canvas_{i+1}.png", "wb") as f:
                                            f.write(qr_screenshot)
                                        print(f"     ✓ Canvas二维码已保存: qrcode_canvas_{i+1}.png")
                                        qr_found = True

                            except Exception as e:
                                print(f"     ✗ 处理元素出错: {e}")
                                continue

                except Exception as e:
                    continue

            if not qr_found:
                print("   ⚠ 未找到可见的二维码元素")

            # 检查是否有iframe
            print("\n4. 检查iframe...")
            for i, frame in enumerate(page.frames):
                print(f"   Frame {i+1}: {frame.url[:100]}")
                if "login" in frame.url.lower() or "passport" in frame.url.lower():
                    print(f"   ✓ 发现登录iframe: {frame.url}")
                    try:
                        await frame.screenshot(path=f"frame_{i+1}.png")
                        print(f"     ✓ iframe截图: frame_{i+1}.png")
                    except:
                        pass

            # 保存完整页面
            print("\n5. 保存完整页面信息...")
            await page.screenshot(path="03_final_full.png", full_page=True)
            print("   ✓ 完整截图: 03_final_full.png")

            html = await page.content()
            with open("qunar_login_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("   ✓ HTML: qunar_login_page.html")

            # 打印所有模态框
            print("\n6. 检查可见的模态框/弹窗...")
            modals = await page.locator("[style*='display'][style*='block'], [class*='show'], [class*='visible']").all()
            print(f"   找到 {len(modals)} 个可能的模态框")

            for i, modal in enumerate(modals[:10]):
                try:
                    visible = await modal.is_visible()
                    if visible:
                        class_name = await modal.get_attribute("class")
                        print(f"   Modal {i+1}: class={class_name}")
                except:
                    pass

            print("\n" + "=" * 70)
            print("分析完成！请检查生成的截图文件。")
            print("=" * 70)

            # 保持浏览器打开10秒供观察
            print("\n浏览器将保持打开10秒...")
            await asyncio.sleep(10)

        except Exception as e:
            print(f"\n✗ 错误: {e}")
            import traceback
            traceback.print_exc()

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(trigger_login_qrcode())
