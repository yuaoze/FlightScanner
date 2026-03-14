"""去哪儿网 Cookie 刷新工具

运行方式：
    python scripts/qunar_login.py

功能：
  弹出浏览器窗口显示去哪儿登录二维码，扫码登录后自动将 Cookie
  保存到项目根目录的 qunar_cookies.json 文件，供 QunarScraper 使用。

Cookie 有效期：通常数周到数月，失效后重新运行本脚本刷新即可。
"""

import asyncio
import sys
from pathlib import Path

# 将 src/ 加入搜索路径，支持直接运行（无需 pip install）
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))

from flightscanner.scrapers.qunar_scraper import QunarScraper  # noqa: E402

if __name__ == "__main__":
    output = str(_project_root / "qunar_cookies.json")
    success = asyncio.run(
        QunarScraper.refresh_cookies_via_login(output_path=output)
    )
    sys.exit(0 if success else 1)
