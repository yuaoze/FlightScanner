"""Live debug script for QunarScraper and CtripScraper.

Tests both domestic one-way and international one-way routes.
Usage: PYTHONPATH=src python scripts/debug_scrape.py [--ctrip] [--intl-only]
"""
import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from flightscanner.scrapers.qunar_scraper import QunarScraper
from flightscanner.scrapers.ctrip_scraper import CtripScraper
from flightscanner.interfaces import SearchParams

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("debug_scrape")


def _print_results(label: str, flights, limit: int = 5) -> None:
    if not flights:
        log.error("%s — 未返回航班，请查看 *_debug_* 调试文件", label)
        return
    log.info("%s — 共 %d 条", label, len(flights))
    for i, fp in enumerate(flights[:limit], 1):
        fi = fp.flight_info
        log.info(
            "  [%d] %s (%s)  %s→%s  ¥%s  [%s]",
            i, fi.flight_no, fi.airline,
            fi.departure_time, fi.arrival_time,
            fp.price, fp.source,
        )
    if len(flights) > limit:
        log.info("  ... 以及另外 %d 条", len(flights) - limit)


async def test_qunar(domestic: bool = True, intl: bool = True) -> None:
    scraper = QunarScraper(headless=False, timeout=60000, max_retries=1)
    try:
        if domestic:
            params = SearchParams(
                departure_city="上海",
                arrival_city="成都",
                departure_date=date(2026, 3, 21),
            )
            log.info("=== Qunar 国内单程：上海 → 成都，2026-03-21 ===")
            flights = await scraper.search_flights(params)
            _print_results("Qunar 国内单程", flights)

        if intl:
            params_out = SearchParams(
                departure_city="上海",
                arrival_city="马尼拉",
                departure_date=date(2026, 5, 1),
            )
            log.info("=== Qunar 国际去程：上海 → 马尼拉，2026-05-01 ===")
            flights_out = await scraper.search_flights(params_out)
            _print_results("Qunar 国际去程", flights_out)

            params_ret = SearchParams(
                departure_city="马尼拉",
                arrival_city="上海",
                departure_date=date(2026, 5, 5),
            )
            log.info("=== Qunar 国际回程：马尼拉 → 上海，2026-05-05 ===")
            flights_ret = await scraper.search_flights(params_ret)
            _print_results("Qunar 国际回程", flights_ret)
    finally:
        await scraper.close()


async def test_ctrip(domestic: bool = True, intl: bool = True) -> None:
    scraper = CtripScraper(headless=False, timeout=30000)
    try:
        if domestic:
            params = SearchParams(
                departure_city="上海",
                arrival_city="成都",
                departure_date=date(2026, 3, 21),
            )
            log.info("=== Ctrip 国内单程：上海 → 成都，2026-03-21 ===")
            flights = await scraper.search_flights(params)
            _print_results("Ctrip 国内单程", flights)

        if intl:
            params_out = SearchParams(
                departure_city="上海",
                arrival_city="马尼拉",
                departure_date=date(2026, 5, 1),
            )
            log.info("=== Ctrip 国际去程：上海 → 马尼拉，2026-05-01 ===")
            flights_out = await scraper.search_flights(params_out)
            _print_results("Ctrip 国际去程", flights_out)

            params_ret = SearchParams(
                departure_city="马尼拉",
                arrival_city="上海",
                departure_date=date(2026, 5, 5),
            )
            log.info("=== Ctrip 国际回程：马尼拉 → 上海，2026-05-05 ===")
            flights_ret = await scraper.search_flights(params_ret)
            _print_results("Ctrip 国际回程", flights_ret)
    finally:
        await scraper.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="FlightScanner 爬虫调试脚本")
    parser.add_argument("--ctrip", action="store_true", help="同时测试携程爬虫")
    parser.add_argument("--intl-only", action="store_true", help="仅测试国际航班路线")
    parser.add_argument("--dom-only", action="store_true", help="仅测试国内航班路线")
    args = parser.parse_args()

    run_domestic = not args.intl_only
    run_intl = not args.dom_only

    log.info("开始调试抓取（国内=%s，国际=%s，携程=%s）", run_domestic, run_intl, args.ctrip)

    await test_qunar(domestic=run_domestic, intl=run_intl)

    if args.ctrip:
        await test_ctrip(domestic=run_domestic, intl=run_intl)

    log.info("调试完成")


if __name__ == "__main__":
    asyncio.run(main())
