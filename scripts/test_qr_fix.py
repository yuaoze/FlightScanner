#!/usr/bin/env python3
"""快速测试去哪儿网二维码捕获功能"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.scrapers import QunarScraper
from flightscanner.interfaces import SearchParams
from datetime import date


async def test_qr_capture():
    """测试二维码捕获"""
    print("=" * 70)
    print("测试去哪儿网二维码捕获功能")
    print("=" * 70)

    # 创建scraper（非无头模式便于观察）
    scraper = QunarScraper(headless=False, timeout=30000)

    try:
        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 6),
        )

        print("\n1. 开始搜索航班...")
        print(f"   路线: {params.departure_city} → {params.arrival_city}")
        print(f"   日期: {params.departure_date}")

        # 尝试搜索（会触发登录检测）
        results = await scraper.search_flights(params)

        if results:
            print(f"\n✓ 成功! 找到 {len(results)} 个航班")
        else:
            print("\n⚠ 未找到航班（可能需要登录）")

    except Exception as e:
        print(f"\n捕获到异常: {type(e).__name__}")
        print(f"信息: {e}")

        # 检查是否生成了二维码文件
        qr_file = Path("qunar_login_qr.png")
        if qr_file.exists():
            print(f"\n✓ 二维码已保存: {qr_file}")
            print(f"  文件大小: {qr_file.stat().st_size} bytes")
            print("\n请检查二维码文件是否包含完整的二维码图片")
        else:
            print("\n✗ 未生成二维码文件")

    finally:
        await scraper.close()
        print("\n" + "=" * 70)
        print("测试完成")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_qr_capture())
