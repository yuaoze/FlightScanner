"""校验或恢复经过人工核对的城市照片；不再自动搜索首图或生成随机占位图。"""

import argparse
import hashlib
import io
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "frontend/src/assets/destination-images.json"
ASSET_DIR = ROOT / "frontend/public/images/destinations"


def asset_path(photo: dict) -> Path:
    image = photo["image"]
    if not re.fullmatch(r"/images/destinations/[a-z0-9-]+\.webp", image):
        raise ValueError(f"无效的本地图片路径: {image}")
    return ASSET_DIR / Path(image).name


def validate_metadata(city: str, photo: dict) -> None:
    for field in ("title", "author", "source_file", "modifications"):
        if not photo.get(field):
            raise ValueError(f"{city}: 缺少 {field}")
    if not photo["license"].startswith(("CC BY", "CC0", "Public domain")):
        raise ValueError(f"{city}: 需要人工核对许可")
    if photo["license"].startswith("CC BY") and not photo.get("license_url"):
        raise ValueError(f"{city}: 缺少许可链接")
    source = urlparse(photo["source_url"])
    if source.scheme != "https" or source.hostname != "commons.wikimedia.org":
        raise ValueError(f"{city}: 图片来源不是 Wikimedia Commons")
    download = urlparse(photo["download_url"])
    if download.scheme != "https" or download.hostname not in (
        "upload.wikimedia.org", "thumb.wikimedia.org"
    ):
        raise ValueError(f"{city}: 下载地址不是 Wikimedia 图片服务器")
    asset_path(photo)


def validate_image(raw: bytes, photo: dict) -> None:
    if hashlib.sha256(raw).hexdigest() != photo["sha256"]:
        raise ValueError("图片校验值不匹配，需要人工核对来源或编码版本")
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "WEBP" or image.size != (photo["width"], photo["height"]):
            raise ValueError("图片格式或尺寸与清单不一致")
        image.verify()


def download_image(photo: dict) -> bytes:
    request = urllib.request.Request(
        photo["download_url"],
        headers={"User-Agent": "FlightScanner/2.2 (reviewed destination image restore)"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        original = response.read()
    with Image.open(io.BytesIO(original)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((960, 960), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=82, method=6)
    raw = output.getvalue()
    validate_image(raw, photo)
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="从已审核来源恢复缺失或损坏的图片")
    parser.add_argument("--dry-run", action="store_true", help="仅显示来源和许可，不访问网络或修改文件")
    parser.add_argument("--city", help="只检查指定城市")
    args = parser.parse_args()
    photos = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.city:
        if args.city not in photos:
            parser.error(f"未配置城市: {args.city}")
        photos = {args.city: photos[args.city]}

    failed = 0
    for city, photo in photos.items():
        try:
            validate_metadata(city, photo)
            if args.dry_run:
                print(f"{city}: {photo['title']} / {photo['author']} / {photo['license']}")
                continue
            path = asset_path(photo)
            try:
                validate_image(path.read_bytes(), photo)
            except (OSError, ValueError):
                if not args.download:
                    raise
                raw = download_image(photo)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            print(f"通过: {city}")
        except Exception as exc:
            failed += 1
            print(f"失败: {city}: {exc}")
    print(f"共 {len(photos)} 个城市，失败 {failed} 个")
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
