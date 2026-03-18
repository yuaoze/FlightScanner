"""去哪儿 Cookie 刷新工具（扫码登录）

去哪儿登录页默认同时显示手机号登录（左侧）和扫码登录（右侧），无需切换 tab。
二维码为普通 <img> 标签，直接从 src 属性下载图片 URL，比携程的 canvas 方式更简单。

运行方式：
    # 有头模式（本地调试，可见浏览器）
    python scripts/qunar_login.py

    # 无头模式（服务器，纯终端显示二维码）
    python scripts/qunar_login.py --headless

    # 自定义超时时间（默认 120 秒）
    python scripts/qunar_login.py --headless --timeout 180

登录成功后 Cookie 保存至项目根目录 qunar_cookies.json，供 QunarScraper 使用。
Cookie 有效期通常数周到数月，过期后重新运行本脚本。
"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# ── 路径设置 ──────────────────────────────────────────────────────────────────
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("qunar_login")

# ── 常量 ─────────────────────────────────────────────────────────────────────
LOGIN_URL = (
    "https://user.qunar.com/passport/login.jsp"
    "?ret=https%3A%2F%2Fwww.qunar.com%2F"
)
OUTPUT_PATH = str(_project_root / "qunar_cookies.json")
QR_PNG_PATH = str(_project_root / "qunar_qr_login.png")

# 检测登录成功的特征 Cookie（继承自 QunarScraper.refresh_cookies_via_login）
_LOGIN_COOKIES = {"QN44", "quinn", "_qnauthtoken"}


def _print_qr_terminal(png_path: str) -> None:
    """用 Unicode 半块字符在终端打印二维码（仅需 Pillow）。"""
    try:
        from PIL import Image

        img = Image.open(png_path).convert("L")  # 转灰度
        target_w = 60
        w, h = img.size
        target_h = int(h * target_w / w)
        if target_h % 2 != 0:
            target_h += 1
        img = img.resize((target_w, target_h), Image.NEAREST)
        pixels = list(img.getdata())

        print()
        for row in range(0, target_h, 2):
            line = ""
            for col in range(target_w):
                top = pixels[row * target_w + col] < 128        # True = 黑像素
                bot = pixels[(row + 1) * target_w + col] < 128  # True = 黑像素
                if top and bot:
                    line += "█"
                elif top:
                    line += "▀"
                elif bot:
                    line += "▄"
                else:
                    line += " "
            print(line)
        print()
    except ImportError:
        logger.debug("Pillow 未安装，跳过终端二维码渲染（pip install pillow）")
    except Exception as e:
        logger.debug("终端二维码渲染失败（不影响流程）: %s", e)


async def _download_qr_image(url: str, save_path: str) -> bool:
    """从 URL 下载二维码图片并保存到本地。

    优先使用 httpx（异步），若未安装则 fallback 到 urllib（标准库）。
    """
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(resp.content)
        return True
    except ImportError:
        pass  # httpx 未安装，使用标准库
    except Exception as e:
        logger.debug("httpx 下载失败: %s，尝试 urllib", e)

    # urllib fallback
    try:
        import urllib.request
        urllib.request.urlretrieve(url, save_path)
        return True
    except Exception as e:
        logger.error("下载二维码图片失败: %s", e)
        return False


async def qr_login(
    headless: bool = False,
    output_path: str = OUTPUT_PATH,
    timeout: int = 120,
    on_qr_ready: Callable[[str], None] | None = None,
) -> bool:
    """执行去哪儿扫码登录流程。

    流程：
      1. 打开去哪儿登录页（QR 默认显示在右侧，无需切换）
      2. 读取 img.QRcodeImg 的 src 属性 → 下载二维码 PNG
      3. 终端打印 Unicode 二维码 + macOS 自动打开图片
      4. 轮询 Cookie：检测到 QN44 / quinn / _qnauthtoken 后保存

    Args:
        headless: 是否无头模式（服务器用 True，本地调试用 False）。
        output_path: Cookie 输出文件路径（默认 qunar_cookies.json）。
        timeout: 等待用户扫码的最大秒数（默认 120）。

    Returns:
        登录成功返回 True。
    """
    from playwright.async_api import async_playwright

    print("\n" + "=" * 60)
    print("去哪儿 Cookie 刷新工具（扫码登录）")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = await context.new_page()

        # ── 步骤 1：打开登录页 ─────────────────────────────────────────────
        print("\n[1/3] 打开去哪儿登录页...")
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error("打开登录页失败: %s", e)
            await browser.close()
            return False

        # ── 步骤 2：获取并展示二维码 ───────────────────────────────────────
        print("[2/3] 获取扫码登录二维码...")

        # 去哪儿登录页的 QR 码是 <img class='QRcodeImg' src='...'>
        qr_img = await page.query_selector("img.QRcodeImg")
        if not qr_img:
            # 兼容备选选择器
            for sel in ["img[class*='QRcode']", "img[class*='qrcode']", ".qr-code img"]:
                qr_img = await page.query_selector(sel)
                if qr_img:
                    logger.debug("使用备选选择器找到二维码: %s", sel)
                    break

        if not qr_img:
            logger.error("  未找到二维码图片元素，截图调试中...")
            try:
                await page.screenshot(path=str(_project_root / "qunar_login_debug.png"))
                logger.info("  截图已保存 → qunar_login_debug.png")
            except Exception:
                pass
            await browser.close()
            return False

        qr_src = await qr_img.get_attribute("src")
        if not qr_src:
            logger.error("  二维码 img 的 src 属性为空")
            await browser.close()
            return False

        logger.info("  ✓ 找到二维码 URL: %s", qr_src[:80])

        # 下载二维码图片
        downloaded = await _download_qr_image(qr_src, QR_PNG_PATH)
        if not downloaded:
            logger.error("  下载二维码失败")
            await browser.close()
            return False

        logger.info("  ✓ 二维码 PNG 已保存 → %s", QR_PNG_PATH)
        if on_qr_ready:
            on_qr_ready(QR_PNG_PATH)  # 通知调用方 QR 已就绪，Streamlit 可立即显示

        # 终端打印 Unicode 二维码
        print("\n" + "─" * 60)
        print("请用去哪儿 APP 扫描以下二维码登录：")
        _print_qr_terminal(QR_PNG_PATH)
        print(f"（二维码图片：{QR_PNG_PATH}）")
        print("─" * 60 + "\n")

        # macOS 自动打开图片（服务器环境会静默失败）
        try:
            subprocess.Popen(
                ["open", QR_PNG_PATH],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        # ── 步骤 3：轮询 Cookie，等待登录成功 ────────────────────────────
        print(f"[3/3] 等待扫码（最多 {timeout} 秒）...")
        login_success = False
        qr_src_current = qr_src
        qr_refreshed = False  # 防止重复刷新

        for i in range(timeout // 2):
            await asyncio.sleep(2)

            # 检查登录特征 Cookie
            cookies = await context.cookies()
            cookie_names = {c["name"] for c in cookies if c["value"]}
            if _LOGIN_COOKIES & cookie_names:
                login_success = True
                await asyncio.sleep(1)  # 等待所有 Cookie 写入完毕
                break

            # 检查 URL 跳转（登录后会跳转到 qunar.com 首页）
            current_url = page.url
            if "user.qunar.com" not in current_url and "qunar.com" in current_url:
                login_success = True
                break

            # 二维码过期检测（通常约 60s 后 src 会变化）
            if not qr_refreshed and i == 25:  # ~50s 后检查是否需要刷新
                try:
                    new_qr = await page.query_selector("img.QRcodeImg")
                    if new_qr:
                        new_src = await new_qr.get_attribute("src")
                        if new_src and new_src != qr_src_current:
                            logger.info("  二维码已更新，正在刷新...")
                            if await _download_qr_image(new_src, QR_PNG_PATH):
                                print("\n二维码已更新，请重新扫描：")
                                _print_qr_terminal(QR_PNG_PATH)
                                qr_src_current = new_src
                                qr_refreshed = True
                except Exception:
                    pass

            if i > 0 and i % 10 == 0:
                elapsed = i * 2
                print(f"  等待中... {elapsed}/{timeout}s — 请打开去哪儿 APP 扫码")

        # ── 保存 Cookie（无论是否检测到登录成功，均落盘）────────────────
        all_cookies = await context.cookies()
        # 仅保留 qunar.com 相关域名的 Cookie（与 QunarScraper 行为一致）
        qunar_cookies = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "expires": c.get("expires", -1),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": c.get("sameSite", "Lax"),
            }
            for c in all_cookies
            if "qunar.com" in c.get("domain", "")
        ]

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(qunar_cookies, f, ensure_ascii=False, indent=2)

        await browser.close()

    # ── 输出结果 ──────────────────────────────────────────────────────────────
    cookie_names = {c["name"] for c in qunar_cookies if c["value"]}
    has_login_cookie = bool(_LOGIN_COOKIES & cookie_names)

    print(f"\n{'='*60}")
    if has_login_cookie or login_success:
        username = next(
            (c["value"] for c in qunar_cookies if c["name"] == "QN44"),
            "（未知）",
        )
        print(f"✓ 登录成功！账号：{username}")
        print(f"  Cookie 已保存 → {output_path}")
        print(f"  共保存 {len(qunar_cookies)} 条 qunar.com Cookie")
        present = _LOGIN_COOKIES & cookie_names
        if present:
            print(f"  关键 Cookie: {', '.join(sorted(present))}")
        print(f"{'='*60}\n")
        return True
    else:
        print("✗ 未检测到登录成功状态（二维码可能已过期或未扫码）")
        print(f"  Cookie 仍已保存（{len(qunar_cookies)} 条），可手动检查 {output_path}")
        print(f"{'='*60}\n")
        return len(qunar_cookies) > 5


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="去哪儿 Cookie 刷新工具（扫码登录）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例：
    # 有头模式（本地，可见浏览器窗口 + 终端二维码）
    python scripts/qunar_login.py

    # 无头模式（服务器，仅终端 Unicode 二维码）
    python scripts/qunar_login.py --headless

    # 延长等待时间（网速慢时）
    python scripts/qunar_login.py --headless --timeout 180
        """,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式（服务器使用，默认有头）",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_PATH,
        help=f"Cookie 输出路径（默认: {OUTPUT_PATH}）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="等待扫码的最大秒数（默认 120）",
    )
    args = parser.parse_args()

    success = await qr_login(
        headless=args.headless,
        output_path=args.output,
        timeout=args.timeout,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
