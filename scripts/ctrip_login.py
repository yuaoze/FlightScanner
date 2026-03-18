"""携程 Cookie 刷新工具（短信验证码登录）

支持有头和无头两种模式，适用于本地调试和无 GUI 服务器。

运行方式：
    # 有头模式（本地调试，可见浏览器）
    python scripts/ctrip_login.py

    # 无头模式（服务器，纯终端）
    python scripts/ctrip_login.py --headless

    # 指定手机号（跳过交互输入）
    python scripts/ctrip_login.py --phone 13800138000

登录成功后 Cookie 保存至项目根目录 ctrip_cookies.json，供 CtripScraper 使用。
Cookie 有效期通常 7~14 天，过期后重新运行本脚本。
"""

import argparse
import asyncio
import json
import logging
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
logger = logging.getLogger("ctrip_login")

# ── 携程登录相关常量 ───────────────────────────────────────────────────────────
LOGIN_URL = "https://passport.ctrip.com/user/login.html"
FLIGHTS_URL = "https://flights.ctrip.com/"
OUTPUT_PATH = str(_project_root / "ctrip_cookies.json")

# ── 选择器（按优先级排列，兼容携程多版本前端）────────────────────────────────────
# 手机号输入框（验证码登录模式下的手机号输入框）
PHONE_SELECTORS = [
    "input[name='mobile']",
    "input[placeholder*='手机号']",
    "input[placeholder*='手机']",
    "#user_input",
    ".login-input input",
    "input[type='tel']",
]

# "验证码登录"切换链接（携程页面底部）
SMS_TAB_SELECTORS = [
    "a:has-text('验证码登录')",          # 实际页面文字（最高优先级）
    "text=验证码登录",
    "li[data-type='vercode']",
    ".login-tab-vercode",
    "a:has-text('短信验证码')",
    "span:has-text('短信验证码')",
    ".tab-item:has-text('验证码')",
]

# "发送验证码"链接/按钮（携程实际是 <a class='btn-primary-s'>）
GET_CODE_SELECTORS = [
    "a.btn-primary-s",                  # 实际元素：<a class='btn-primary-s '>
    "a:has-text('发送验证码')",           # 实际文字
    ".getCodeBtn",
    ".get-code-btn",
    "button:has-text('获取验证码')",
    "button:has-text('发送验证码')",
    "a:has-text('获取验证码')",
    "[class*='getCode']",
    "[class*='send-code']",
]

# 验证码输入框（实际 placeholder='请输入验证码'）
CODE_INPUT_SELECTORS = [
    "input[placeholder*='验证码']",      # 最高优先级：placeholder='请输入验证码'
    "input[name='verifyCode']",
    "#verification_code",
    ".verify-code-input input",
    "input[maxlength='6']",
    "input[maxlength='4']",
]

# 登录提交按钮（携程实际是 <input type='button' class='form_btn form_btn--block'>）
SUBMIT_SELECTORS = [
    "input.form_btn",                   # 实际元素：<input type='button' class='form_btn ...'>
    "input[class*='form_btn']",
    ".login-btn",
    "button[type='submit']",
    "button:has-text('登录')",
    ".btn-login",
    "[class*='login-btn']",
    "[class*='submit']",
]

# 登录成功后出现的特征元素（判断是否已登录）
SUCCESS_SELECTORS = [
    ".header-user-info",
    ".user-avatar",
    ".my-account",
    "[class*='user-name']",
    "[class*='avatar']",
]

# 需要等待的登录后域名特征
SUCCESS_URL_PATTERNS = [
    "my.ctrip.com",
    "passport.ctrip.com/user/checkin",
    "passport.ctrip.com/user/sec/",
]


async def _try_click(page, selectors: list[str], label: str) -> bool:
    """尝试多个选择器依次点击，成功则返回 True。
    对每个选择器遍历所有匹配元素，找到第一个可见的并点击。
    """
    for sel in selectors:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                if await el.is_visible():
                    await el.click()
                    logger.info("  ✓ 点击 %s: %s", label, sel)
                    return True
        except Exception:
            pass
    return False


async def _try_fill(page, selectors: list[str], value: str, label: str) -> bool:
    """尝试多个选择器依次填入文字，成功则返回 True。
    对每个选择器遍历所有匹配元素，找到第一个可见的并填入。
    """
    for sel in selectors:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                if await el.is_visible():
                    await el.click()
                    await el.fill(value)
                    logger.info("  ✓ 填入 %s", label)
                    return True
        except Exception:
            pass
    return False


async def _screenshot(page, name: str) -> None:
    """保存调试截图到项目根目录（失败不中断流程）。"""
    try:
        path = str(_project_root / f"ctrip_login_{name}.png")
        await page.screenshot(path=path)
        logger.debug("截图已保存 → %s", path)
    except Exception:
        pass


async def _save_cookies(context, output_path: str) -> list[dict]:
    """保存当前 BrowserContext 的全部 Cookie 到文件。"""
    cookies = await context.cookies()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    return cookies


def _get_key_cookies(cookies: list[dict]) -> dict:
    """提取关键 Cookie 字段用于状态判断。"""
    key_names = {"GUID", "ibu_uid", "login_uid", "uid", "uin", "ticket", "_bfa"}
    return {c["name"]: c["value"][:20] + "..." for c in cookies
            if c["name"] in key_names and c["value"]}


def _print_qr_terminal(png_path: str) -> None:
    """用 Unicode 半块字符在终端打印二维码（无需额外依赖，仅需 Pillow）。"""
    try:
        from PIL import Image
        img = Image.open(png_path).convert("L")  # 转灰度
        # 缩放到适合终端的尺寸（每个字符代表 2 行像素）
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
                top = pixels[row * target_w + col] < 128        # True = 黑
                bot = pixels[(row + 1) * target_w + col] < 128  # True = 黑
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
    except Exception as e:
        logger.debug("终端二维码渲染失败（不影响流程）: %s", e)


async def qr_login(
    headless: bool = True,
    output_path: str = OUTPUT_PATH,
    timeout: int = 120,
    on_qr_ready: Callable[[str], None] | None = None,
) -> bool:
    """执行携程扫码登录流程（适合无头/服务器环境）。

    流程：
      1. 打开登录页，切换到「扫码登录」
      2. 提取 canvas 二维码 → 保存 PNG + 终端打印
      3. 拦截 qrCodeLogin API，等待 returnCode=0（扫码确认）
      4. 保存 Cookie

    Args:
        headless: 是否无头模式。
        output_path: Cookie 输出文件路径。
        timeout: 等待用户扫码的最大秒数（默认 120）。

    Returns:
        登录成功返回 True。
    """
    import base64
    import subprocess
    from playwright.async_api import async_playwright

    print("\n" + "="*60)
    print("携程 Cookie 刷新工具（扫码登录）")
    print("="*60)

    qr_png_path = str(_project_root / "ctrip_qr_login.png")
    login_detected = asyncio.Event()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
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
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = await context.new_page()

        # ── 拦截 qrCodeLogin API，检测扫码成功 ──────────────────────────
        async def _route_handler(route) -> None:
            req = route.request
            if req.resource_type in ("xhr", "fetch"):
                try:
                    resp = await route.fetch()
                    body = await resp.body()
                    if "qrCodeLogin" in req.url:
                        try:
                            data = json.loads(body.decode("utf-8", errors="replace"))
                            if data.get("returnCode") == 0:
                                logger.info("API 检测到扫码成功（returnCode=0）")
                                login_detected.set()
                        except Exception:
                            pass
                    await route.fulfill(response=resp, body=body)
                    return
                except Exception:
                    pass
            await route.continue_()

        await page.route("**/*", _route_handler)

        # ── 步骤 1：打开登录页 ─────────────────────────────────────────
        print("\n[1/3] 打开携程登录页...")
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error("打开登录页失败: %s", e)
            await browser.close()
            return False

        # ── 步骤 2：切换到扫码登录，提取二维码 ────────────────────────
        print("[2/3] 切换到扫码登录...")
        clicked = False
        for sel in ["div.login-code", "a:has-text('扫码登录')", "[class*='login-code']"]:
            els = await page.query_selector_all(sel)
            for el in els:
                if await el.is_visible():
                    await el.click()
                    logger.info("  ✓ 点击扫码登录: %s", sel)
                    clicked = True
                    break
            if clicked:
                break

        if not clicked:
            logger.error("  未找到扫码登录入口")
            await browser.close()
            return False

        await asyncio.sleep(2)  # 等待 canvas 渲染

        # 提取 canvas 二维码
        canvas = await page.query_selector("canvas")
        if not canvas or not await canvas.is_visible():
            logger.error("  未找到二维码 canvas")
            await browser.close()
            return False

        data_url = await canvas.evaluate("c => c.toDataURL('image/png')")
        if not data_url or data_url == "data:,":
            logger.error("  canvas 内容为空")
            await browser.close()
            return False

        _, b64 = data_url.split(",", 1)
        with open(qr_png_path, "wb") as f:
            f.write(base64.b64decode(b64))
        logger.info("  ✓ 二维码 PNG 已保存 → %s", qr_png_path)
        if on_qr_ready:
            on_qr_ready(qr_png_path)  # 通知调用方 QR 已就绪，Streamlit 可立即显示

        # 终端打印二维码
        print("\n" + "─"*60)
        print("请用携程 APP 扫描以下二维码登录：")
        _print_qr_terminal(qr_png_path)
        print(f"（二维码图片：{qr_png_path}）")
        print("─"*60 + "\n")

        # macOS 自动打开图片（服务器环境会静默失败）
        try:
            subprocess.Popen(["open", qr_png_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # ── 步骤 3：等待扫码 ──────────────────────────────────────────
        print(f"[3/3] 等待扫码（最多 {timeout} 秒）...")
        login_success = False

        for i in range(timeout // 2):
            await asyncio.sleep(2)

            if login_detected.is_set():
                login_success = True
                await asyncio.sleep(2)  # 等待 Cookie 写入
                break

            current_url = page.url
            if any(pat in current_url for pat in SUCCESS_URL_PATTERNS):
                login_success = True
                break

            # 检查成功特征元素
            for sel in SUCCESS_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        login_success = True
                        break
                except Exception:
                    pass
            if login_success:
                break

            if i > 0 and i % 10 == 0:
                elapsed = i * 2
                print(f"  等待中... {elapsed}/{timeout}s — 请打开携程 APP 扫码")

        await _screenshot(page, "qr_final")

        # ── 保存 Cookie ───────────────────────────────────────────────
        cookies = await _save_cookies(context, output_path)
        key_cookies = _get_key_cookies(cookies)
        login_indicators = {"ibu_uid", "login_uid", "uID", "uin"}
        has_login_cookie = any(c["name"] in login_indicators for c in cookies
                                if c["value"] and c["value"] != "0")

        await browser.close()

        # ── 输出结果 ──────────────────────────────────────────────────
        print(f"\n{'='*60}")
        if has_login_cookie or login_success:
            print(f"✓ 登录成功！Cookie 已保存 → {output_path}")
            print(f"  共保存 {len(cookies)} 条 Cookie")
            if key_cookies:
                print("  关键 Cookie:")
                for name, val in key_cookies.items():
                    print(f"    {name}: {val}")
            print(f"{'='*60}\n")
            return True
        else:
            print("✗ 未检测到登录成功状态（二维码可能已过期或未扫码）")
            print(f"  Cookie 仍已保存（{len(cookies)} 条），可手动检查 {output_path}")
            print(f"{'='*60}\n")
            return len(cookies) > 10


async def login(
    phone: str | None = None,
    headless: bool = False,
    output_path: str = OUTPUT_PATH,
    timeout: int = 60,
) -> bool:
    """执行携程短信验证码登录流程。

    Args:
        phone: 手机号码，为 None 时在终端交互输入。
        headless: 是否无头模式。
        output_path: Cookie 输出文件路径。
        timeout: 等待用户操作的最大秒数。

    Returns:
        登录成功返回 True。
    """
    from playwright.async_api import async_playwright

    print("\n" + "="*60)
    print("携程 Cookie 刷新工具（短信验证码登录）")
    print("="*60)

    # 获取手机号
    if not phone:
        phone = input("\n请输入携程账号手机号: ").strip()
    if not phone:
        logger.error("手机号不能为空")
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(
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
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = await context.new_page()

        # ── 步骤 1：打开登录页 ─────────────────────────────────────────────
        print("\n[1/5] 打开携程登录页...")
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error("打开登录页失败: %s", e)
            await browser.close()
            return False

        await _screenshot(page, "1_login_page")

        # ── 步骤 2：切换到"验证码登录"模式 ─────────────────────────────────
        print("[2/5] 切换到验证码登录...")
        clicked = await _try_click(page, SMS_TAB_SELECTORS, "验证码登录链接")
        if not clicked:
            logger.warning("  未找到「验证码登录」链接，可能已在验证码登录页，继续...")
        await asyncio.sleep(2)   # 等待页面切换到验证码登录表单
        await _screenshot(page, "2_sms_tab")

        # ── 步骤 3：填入手机号 ─────────────────────────────────────────────
        print(f"[3/5] 填入手机号 {phone[:3]}****{phone[-4:]}...")
        filled = await _try_fill(page, PHONE_SELECTORS, phone, "手机号")
        if not filled:
            logger.error("  未能找到手机号输入框，尝试截图后退出")
            await _screenshot(page, "3_phone_fail")
            # 打印当前页面所有 input 元素，辅助调试
            inputs = await page.query_selector_all("input")
            print(f"  当前页面 input 元素数量: {len(inputs)}")
            for inp in inputs[:10]:
                ph = await inp.get_attribute("placeholder") or ""
                nm = await inp.get_attribute("name") or ""
                tp = await inp.get_attribute("type") or ""
                print(f"    type={tp!r}  name={nm!r}  placeholder={ph!r}")
            await browser.close()
            return False

        await asyncio.sleep(0.5)

        # ── 步骤 4：点击"获取验证码" ─────────────────────────────────────
        print("[4/5] 点击获取验证码按钮...")
        clicked = await _try_click(page, GET_CODE_SELECTORS, "获取验证码")
        if not clicked:
            logger.error("  未找到获取验证码按钮")
            await _screenshot(page, "4_getcode_fail")
            # 打印当前页面所有按钮，辅助调试
            buttons = await page.query_selector_all("button, a.btn, a[class*='btn']")
            print(f"  当前页面按钮数量: {len(buttons)}")
            for btn in buttons[:10]:
                txt = (await btn.inner_text()).strip()[:30]
                cls = await btn.get_attribute("class") or ""
                print(f"    text={txt!r}  class={cls[:40]!r}")
            await browser.close()
            return False

        await _screenshot(page, "4_code_sent")
        print(f"  验证码已发送到 {phone[:3]}****{phone[-4:]}")

        # ── 步骤 5：输入验证码并提交 ─────────────────────────────────────
        print("[5/5] 等待输入验证码...")
        code = input("\n请输入收到的短信验证码（6位）: ").strip()
        if not code:
            logger.error("验证码不能为空")
            await browser.close()
            return False

        filled = await _try_fill(page, CODE_INPUT_SELECTORS, code, "验证码")
        if not filled:
            logger.error("  未能找到验证码输入框")
            await _screenshot(page, "5_codeinput_fail")
            await browser.close()
            return False

        await asyncio.sleep(0.5)
        await _screenshot(page, "5_before_submit")

        # 点击登录按钮
        clicked = await _try_click(page, SUBMIT_SELECTORS, "登录按钮")
        if not clicked:
            logger.warning("  未找到登录按钮，尝试按 Enter 提交")
            await page.keyboard.press("Enter")

        # ── 等待登录完成 ──────────────────────────────────────────────────
        print(f"\n等待登录完成（最多 {timeout} 秒）...")
        login_success = False

        for _ in range(timeout // 2):
            await asyncio.sleep(2)
            current_url = page.url

            # 检查 URL 跳转（已登录会重定向）
            if any(pat in current_url for pat in SUCCESS_URL_PATTERNS):
                login_success = True
                break

            # 检查页面上的登录成功特征元素
            for sel in SUCCESS_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        login_success = True
                        break
                except Exception:
                    pass
            if login_success:
                break

            # 检查是否有错误提示
            error_selectors = [".error-msg", ".alert-msg", "[class*='error']"]
            for sel in error_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        msg = (await el.inner_text()).strip()
                        if msg:
                            logger.warning("  页面提示: %s", msg)
                except Exception:
                    pass

        await _screenshot(page, "6_final")

        # ── 保存 Cookie（无论是否检测到登录成功）──────────────────────────
        cookies = await _save_cookies(context, output_path)

        # 判断关键 Cookie 是否存在
        key_cookies = _get_key_cookies(cookies)
        login_indicators = {"ibu_uid", "login_uid", "uID", "uin"}
        has_login_cookie = any(c["name"] in login_indicators for c in cookies
                                if c["value"] and c["value"] != "0")

        await browser.close()

        # ── 输出结果 ──────────────────────────────────────────────────────
        print(f"\n{'='*60}")
        if has_login_cookie or login_success:
            print(f"✓ 登录成功！Cookie 已保存 → {output_path}")
            print(f"  共保存 {len(cookies)} 条 Cookie")
            if key_cookies:
                print("  关键 Cookie:")
                for name, val in key_cookies.items():
                    print(f"    {name}: {val}")
            print(f"{'='*60}\n")
            return True
        else:
            print("✗ 未检测到登录成功状态")
            print(f"  Cookie 仍已保存（{len(cookies)} 条），可手动检查 {output_path}")
            print(f"  调试截图保存在项目根目录（ctrip_login_*.png）")
            print(f"{'='*60}\n")
            # 仍返回 True——Cookie 已落盘，让调用方自行判断
            return len(cookies) > 10


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="携程 Cookie 刷新工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
登录方式：
  扫码登录（推荐，无需人工验证）：
      python scripts/ctrip_login.py --qr
      python scripts/ctrip_login.py --qr --headless   # 服务器

  短信验证码登录（备选，可能触发滑动验证）：
      python scripts/ctrip_login.py --phone 13800138000
        """,
    )
    parser.add_argument("--qr", action="store_true",
                        help="扫码登录模式（推荐，终端显示二维码）")
    parser.add_argument("--phone", default=None,
                        help="短信验证码登录：手机号（不填则交互输入）")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式（服务器使用，默认有头）")
    parser.add_argument("--output", default=OUTPUT_PATH,
                        help=f"Cookie 输出路径（默认: {OUTPUT_PATH}）")
    parser.add_argument("--timeout", type=int, default=None,
                        help="等待操作的最大秒数（扫码默认120，短信默认60）")
    args = parser.parse_args()

    if args.qr:
        success = await qr_login(
            headless=args.headless,
            output_path=args.output,
            timeout=args.timeout or 120,
        )
    else:
        success = await login(
            phone=args.phone,
            headless=args.headless,
            output_path=args.output,
            timeout=args.timeout or 60,
        )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
