"""从 Wikipedia / Unsplash 批量获取目的地城市风景图，自动更新 destinations.py。

优先级：
  1. Unsplash API（需 UNSPLASH_ACCESS_KEY，图片质量最好）
  2. Wikipedia REST summary 接口（无需 Key，质量次之）
  3. 保留原有 picsum 占位图

用法：
    # 预览 URL，不修改文件
    python scripts/fetch_city_images.py --dry-run

    # 直接更新 destinations.py（使用 Wikipedia）
    python scripts/fetch_city_images.py

    # 使用 Unsplash（更好的图片），需先在 .env 设置 UNSPLASH_ACCESS_KEY
    python scripts/fetch_city_images.py --unsplash
"""

import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import httpx

# ── 城市列表（与 destinations.py 保持一致）────────────────────────────────────
CITIES = [
    "三亚", "成都", "重庆", "昆明", "大理", "丽江", "西安", "桂林",
    "贵阳", "厦门", "青岛", "大连", "沈阳", "哈尔滨", "长沙",
    "武汉", "广州", "深圳", "南宁", "张家界", "西双版纳",
    "东京", "大阪", "首尔", "济州", "香港", "澳门",
    "曼谷", "新加坡", "吉隆坡", "普吉岛",
]

# ── 中文城市名 → 英文搜索关键词 ──────────────────────────────────────────────
# Unsplash 搜索关键词（英文，加上 "city" 使结果更聚焦）
_UNSPLASH_QUERY: Dict[str, str] = {
    "三亚":    "Sanya beach China",
    "成都":    "Chengdu city China",
    "重庆":    "Chongqing skyline night",
    "昆明":    "Kunming Yunnan China",
    "大理":    "Dali Yunnan ancient town",
    "丽江":    "Lijiang old town Yunnan",
    "西安":    "Xian city wall China",
    "桂林":    "Guilin karst mountains",
    "贵阳":    "Guiyang city China",
    "厦门":    "Xiamen city coast China",
    "青岛":    "Qingdao beach city China",
    "大连":    "Dalian coast city China",
    "沈阳":    "Shenyang city China",
    "哈尔滨":  "Harbin ice festival China",
    "长沙":    "Changsha city China",
    "武汉":    "Wuhan Yellow Crane Tower",
    "广州":    "Guangzhou skyline China",
    "深圳":    "Shenzhen skyline night",
    "南宁":    "Nanning city China green",
    "张家界":  "Zhangjiajie mountains pillar",
    "西双版纳":"Xishuangbanna tropical forest",
    "东京":    "Tokyo skyline Japan",
    "大阪":    "Osaka castle Japan",
    "首尔":    "Seoul cityscape South Korea",
    "济州":    "Jeju Island Korea",
    "香港":    "Hong Kong skyline harbor",
    "澳门":    "Macau city lights night",
    "曼谷":    "Bangkok temple Thailand",
    "新加坡":  "Singapore Marina Bay night",
    "吉隆坡":  "Kuala Lumpur Petronas Towers",
    "普吉岛":  "Phuket beach Thailand",
}

# Wikipedia 英文文章标题（用于 REST summary fallback）
_WIKI_EN_TITLE: Dict[str, str] = {
    "三亚":    "Sanya",
    "成都":    "Chengdu",
    "重庆":    "Chongqing",
    "昆明":    "Kunming",
    "大理":    "Dali, Yunnan",
    "丽江":    "Lijiang",
    "西安":    "Xi'an",
    "桂林":    "Guilin",
    "贵阳":    "Guiyang",
    "厦门":    "Xiamen",
    "青岛":    "Qingdao",
    "大连":    "Dalian",
    "沈阳":    "Shenyang",
    "哈尔滨":  "Harbin",
    "长沙":    "Changsha",
    "武汉":    "Wuhan",
    "广州":    "Guangzhou",
    "深圳":    "Shenzhen",
    "南宁":    "Nanning",
    "张家界":  "Zhangjiajie",
    "西双版纳":"Xishuangbanna",
    "东京":    "Tokyo",
    "大阪":    "Osaka",
    "首尔":    "Seoul",
    "济州":    "Jeju Island",
    "香港":    "Hong Kong",
    "澳门":    "Macau",
    "曼谷":    "Bangkok",
    "新加坡":  "Singapore",
    "吉隆坡":  "Kuala Lumpur",
    "普吉岛":  "Phuket",
}

# ── 某些城市的 Wikipedia 主图是旗帜/地图而非风景，强制跳过 Wikipedia ──────────
_WIKI_SKIP = {"香港", "澳门", "新加坡", "济州"}

_THUMB_SIZE = 800


# ── Unsplash ─────────────────────────────────────────────────────────────────

def fetch_unsplash(city: str, access_key: str, client: httpx.Client) -> Optional[str]:
    """从 Unsplash 搜索城市风景图，返回可直接嵌入 HTML 的图片 URL。"""
    query = _UNSPLASH_QUERY.get(city, city)
    params = {
        "query":       query,
        "per_page":    1,
        "orientation": "landscape",
        "client_id":   access_key,
    }
    try:
        resp = client.get(
            "https://api.unsplash.com/search/photos",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            # 使用 raw URL + 尺寸参数，800x500 适合卡片背景
            raw = results[0]["urls"]["raw"]
            return f"{raw}&w=800&h=500&fit=crop&q=80"
    except Exception as exc:
        print(f"    [unsplash warn] {city}: {exc}")
    return None


# ── Wikipedia REST summary ────────────────────────────────────────────────────

def _wiki_summary_thumbnail(lang: str, title: str, client: httpx.Client) -> Optional[str]:
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
    try:
        resp = client.get(url, timeout=12)
        if resp.status_code != 200:
            return None
        data = resp.json()
        thumb = data.get("thumbnail", {}).get("source")
        # 将缩略图替换为更大尺寸（Wikipedia URL 格式：/NNNpx-filename → /800px-filename）
        if thumb:
            thumb = re.sub(r"/\d+px-", f"/{_THUMB_SIZE}px-", thumb)
        return thumb
    except Exception as exc:
        print(f"    [wiki warn] {lang}/{title}: {exc}")
    return None


def fetch_wikipedia(city: str, client: httpx.Client) -> Optional[str]:
    """先查中文 Wikipedia REST summary，再查英文 Wikipedia。"""
    if city in _WIKI_SKIP:
        return None

    # 中文版
    url = _wiki_summary_thumbnail("zh", city, client)
    if url:
        return url

    # 英文版
    en_title = _WIKI_EN_TITLE.get(city, city)
    url = _wiki_summary_thumbnail("en", en_title, client)
    return url


# ── 主函数 ────────────────────────────────────────────────────────────────────

def main() -> None:
    dry_run     = "--dry-run" in sys.argv
    use_unsplash = "--unsplash" in sys.argv

    # 读取 Unsplash Key（支持 --unsplash 时从 .env 自动加载）
    access_key: Optional[str] = None
    if use_unsplash:
        # 尝试从 .env 加载
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("UNSPLASH_ACCESS_KEY"):
                    _, _, val = line.partition("=")
                    access_key = val.strip().strip('"').strip("'")
                    break
        if not access_key:
            access_key = os.environ.get("UNSPLASH_ACCESS_KEY")
        if not access_key:
            print("❌ 未找到 UNSPLASH_ACCESS_KEY。")
            print("   请在 .env 中添加：UNSPLASH_ACCESS_KEY=你的AccessKey")
            print("   申请地址：https://unsplash.com/developers")
            sys.exit(1)

    mode_label = "Unsplash" if use_unsplash else "Wikipedia"
    print("=" * 60)
    print(f"批量获取城市风景图（来源：{mode_label}）")
    print("=" * 60)

    results: Dict[str, str] = {}
    failed:  list[str] = []

    headers = {"User-Agent": "FlightScanner/1.0 (city image fetcher)"}
    with httpx.Client(headers=headers) as client:
        for city in CITIES:
            if use_unsplash and access_key:
                url = fetch_unsplash(city, access_key, client)
            else:
                url = fetch_wikipedia(city, client)

            if url:
                display = url if len(url) <= 72 else url[:69] + "..."
                print(f"  ✓ {city:<8} {display}")
                results[city] = url
            else:
                print(f"  ✗ {city:<8} 未找到，将保留 picsum 占位图")
                failed.append(city)
            time.sleep(0.35)

    print()
    print(f"成功: {len(results)} / {len(CITIES)} 座城市")
    if failed:
        print(f"失败: {', '.join(failed)}")

    # ── 生成新的 DESTINATION_IMAGE 块 ────────────────────────────────────────
    lines = ["DESTINATION_IMAGE: Dict[str, str] = {"]
    for city in CITIES:
        if city in results:
            url = results[city]
        else:
            slug = _UNSPLASH_QUERY.get(city, city).split()[0].lower()
            url = f"https://picsum.photos/seed/{slug}/400/220"
        pad = " " * max(0, 4 - len(city))
        lines.append(f'    "{city}":{pad}   "{url}",')
    lines.append('    "_default": "https://picsum.photos/seed/travel-sky/400/220",')
    lines.append("}")

    new_block = "\n".join(lines)

    if dry_run:
        print()
        print("─" * 60)
        print("【dry-run】以下为将要写入的 DESTINATION_IMAGE 内容：")
        print("─" * 60)
        print(new_block)
        return

    # ── 写入 destinations.py ─────────────────────────────────────────────────
    dest_file = (
        Path(__file__).parent.parent
        / "src/flightscanner/weekend_radar/destinations.py"
    )
    if not dest_file.exists():
        print(f"\n❌ 找不到文件：{dest_file}")
        print("请手动将以上 DESTINATION_IMAGE 粘贴到 destinations.py")
        return

    content = dest_file.read_text(encoding="utf-8")
    pattern = r"DESTINATION_IMAGE: Dict\[str, str\] = \{.*?\n\}"
    new_content, n = re.subn(pattern, new_block, content, flags=re.DOTALL)

    if n == 0:
        print(f"\n⚠️  未在 {dest_file.name} 中匹配到 DESTINATION_IMAGE，请手动粘贴。")
    else:
        dest_file.write_text(new_content, encoding="utf-8")
        print(f"\n✅ 已更新 {dest_file}")
        print("重启 Streamlit 即可看到新图片。")


if __name__ == "__main__":
    main()
