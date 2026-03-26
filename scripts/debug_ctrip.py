"""携程爬虫调试脚本。

功能：
- 打开携程航班搜索页（有头浏览器，方便观察）
- 拦截所有网络请求并记录 URL 和响应摘要
- 将捕获的 JSON 响应完整转储到文件供分析
- 尝试用现有 CtripScraper 直接搜索并打印结果

用法：
    # 有头模式调试（可看到浏览器）
    python scripts/debug_ctrip.py

    # 无头模式运行
    python scripts/debug_ctrip.py --headless

    # 自定义路线和日期
    python scripts/debug_ctrip.py --from 北京 --to 上海 --date 2026-04-15
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# ── 路径设置（允许从项目根目录以外运行）────────────────────────────────────────
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import SearchParams
from flightscanner.scrapers.ctrip_scraper import CtripScraper
from flightscanner.utils.city_codes import get_city_code

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("debug_ctrip")


async def capture_all_requests(
    dep_city: str,
    arr_city: str,
    dep_date: date,
    headless: bool,
) -> None:
    """直接用 Playwright 打开携程，捕获并分析所有 XHR/Fetch 请求。"""
    from playwright.async_api import async_playwright

    dep_code = (get_city_code(dep_city) or dep_city[:3]).lower()
    arr_code = (get_city_code(arr_city) or arr_city[:3]).lower()
    date_str = dep_date.strftime("%Y-%m-%d")
    url = (
        f"https://flights.ctrip.com/online/list/oneway-{dep_code}-{arr_code}"
        f"?depdate={date_str}&cabin=y_s_c_f&adult=1&child=0&infant=0"
    )

    print(f"\n{'='*60}")
    print(f"目标 URL: {url}")
    print(f"{'='*60}\n")

    all_requests: list[dict] = []
    json_responses: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
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

        # ── 拦截所有请求，记录 URL ────────────────────────────────────────────
        async def _route_handler(route) -> None:
            req = route.request
            req_record = {
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
            }

            # 只对 XHR/Fetch 读取响应体
            if req.resource_type in ("xhr", "fetch"):
                try:
                    response = await route.fetch()
                    body_bytes = await response.body()
                    body = body_bytes.decode("utf-8", errors="replace")
                    stripped = body.lstrip()

                    req_record["status"] = response.status
                    req_record["body_len"] = len(body)

                    if stripped.startswith(("{", "[")) and len(body) > 100:
                        try:
                            data = json.loads(body)
                            req_record["is_json"] = True
                            json_responses.append({
                                "url": req.url,
                                "status": response.status,
                                "data": data,
                            })
                            # 打印关键接口的摘要
                            _print_json_summary(req.url, data)
                        except Exception:
                            req_record["is_json"] = False

                    await route.fulfill(response=response, body=body_bytes)
                    all_requests.append(req_record)
                    return
                except Exception as e:
                    logger.debug("读取响应失败: %s — %s", req.url[:80], e)

            all_requests.append(req_record)
            await route.continue_()

        await page.route("**/*", _route_handler)

        print(f"[1/4] 导航到携程搜索页...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  ⚠  页面加载超时/出错: {e}")

        print(f"[2/4] 等待 12 秒让页面完成 API 请求...")
        await asyncio.sleep(12)

        # ── 截图 ─────────────────────────────────────────────────────────────
        screenshot_path = "ctrip_debug_screenshot.png"
        try:
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"[3/4] 截图已保存 → {screenshot_path}")
        except Exception as e:
            print(f"  ⚠  截图失败: {e}")

        await browser.close()

    # ── 汇总所有 XHR/Fetch 请求 ──────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"[4/4] 请求汇总 — 共 {len(all_requests)} 条（其中 {len(json_responses)} 条 JSON）")
    print(f"{'='*60}")

    xhr_requests = [r for r in all_requests if r.get("resource_type") in ("xhr", "fetch")]
    print(f"\n--- XHR/Fetch 请求（{len(xhr_requests)} 条）---")
    for r in xhr_requests:
        json_flag = "✓ JSON" if r.get("is_json") else "      "
        size = f"{r.get('body_len', 0):>8} B" if "body_len" in r else "         -"
        status = r.get("status", "-")
        short_url = r["url"].split("?")[0][-80:]
        print(f"  [{status}] {json_flag}  {size}  {short_url}")

    # ── 导出 JSON 响应到文件 ──────────────────────────────────────────────────
    out_path = Path("ctrip_debug_all_responses.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(json_responses, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n所有 JSON 响应已导出 → {out_path}  ({len(json_responses)} 条)")

    # ── 识别可能含有航班数据的响应 ───────────────────────────────────────────
    print(f"\n--- 可能含航班数据的接口 ---")
    flight_keywords = (
        "flight", "itinerary", "product", "list", "segment",
        "flightList", "flightItinerary", "depDate", "arrDate",
        "adultPrice", "priceList",
    )
    found = False
    for entry in json_responses:
        body_str = json.dumps(entry["data"], ensure_ascii=False)
        if any(kw.lower() in body_str.lower() for kw in flight_keywords):
            short_url = entry["url"].split("?")[0][-80:]
            size = len(body_str)
            print(f"  → [{size:>10} chars]  {short_url}")
            found = True
    if not found:
        print("  (未发现含航班关键词的 JSON 响应)")


def _print_json_summary(url: str, data: dict) -> None:
    """打印 JSON 响应的简要结构摘要。"""
    short_url = url.split("?")[0]
    if not isinstance(data, dict):
        return
    inner = data.get("data") or data
    if not isinstance(inner, dict):
        return
    for key in ("flightItineraryList", "flightList", "flights", "itineraryList"):
        val = inner.get(key)
        if isinstance(val, list) and val:
            print(f"  🛫  发现航班数据！key='{key}'，{len(val)} 条 @ {short_url.split('/')[-1]}")
            # 打印第一条的键名，帮助了解结构
            first = val[0]
            if isinstance(first, dict):
                print(f"      首条记录 keys: {list(first.keys())[:10]}")
            return


async def run_scraper(
    dep_city: str,
    arr_city: str,
    dep_date: date,
    headless: bool,
) -> None:
    """用 CtripScraper 直接搜索，打印结果。"""
    print(f"\n{'='*60}")
    print(f"使用 CtripScraper 搜索: {dep_city} → {arr_city} ({dep_date})")
    print(f"{'='*60}")

    scraper = CtripScraper(headless=headless, timeout=40000)
    try:
        params = SearchParams(
            departure_city=dep_city,
            arrival_city=arr_city,
            departure_date=dep_date,
        )
        prices = await scraper.search_flights(params)

        if not prices:
            print("  ❌  CtripScraper 返回 0 条结果")
            return

        print(f"  ✓  获取到 {len(prices)} 条航班价格:\n")
        for i, fp in enumerate(prices[:10], 1):
            fi = fp.flight_info
            print(
                f"  {i:2}. {fi.flight_no:8}  {fi.airline:12}  "
                f"{fi.departure_time}-{fi.arrival_time}  "
                f"¥{fp.price:>7}  [{fp.seat_class}]  来源:{fp.source}"
            )
        if len(prices) > 10:
            print(f"  ... (共 {len(prices)} 条，仅显示前10)")
    except Exception as e:
        print(f"  ❌  CtripScraper 出错: {type(e).__name__}: {e}")
        logger.debug("详细错误：", exc_info=True)
    finally:
        await scraper.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="携程爬虫调试脚本")
    parser.add_argument("--from", dest="dep_city", default="上海", help="出发城市（默认：上海）")
    parser.add_argument("--to", dest="arr_city", default="北京", help="到达城市（默认：北京）")
    parser.add_argument("--date", default=None, help="出发日期 YYYY-MM-DD（默认：7天后）")
    parser.add_argument("--headless", action="store_true", help="无头模式（默认有头）")
    parser.add_argument(
        "--mode",
        choices=["capture", "scraper", "both"],
        default="both",
        help="调试模式：capture=仅抓包分析，scraper=仅运行爬虫，both=全部（默认）",
    )
    args = parser.parse_args()

    dep_date = (
        date.fromisoformat(args.date)
        if args.date
        else date.today() + timedelta(days=7)
    )

    print(f"\n携程爬虫调试脚本")
    print(f"路线: {args.dep_city} → {args.arr_city}")
    print(f"日期: {dep_date}")
    print(f"城市代码: {get_city_code(args.dep_city) or '(未知)'} → {get_city_code(args.arr_city) or '(未知)'}")
    print(f"模式: {args.mode}  浏览器: {'无头' if args.headless else '有头（可见）'}")

    if args.mode in ("capture", "both"):
        await capture_all_requests(args.dep_city, args.arr_city, dep_date, args.headless)

    if args.mode in ("scraper", "both"):
        await run_scraper(args.dep_city, args.arr_city, dep_date, args.headless)


if __name__ == "__main__":
    asyncio.run(main())
