import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from flightscanner.weekend_radar.destinations import ALL_DESTINATIONS

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("fetch_city_images", ROOT / "scripts/fetch_city_images.py")
images = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(images)
PHOTOS = json.loads(images.MANIFEST.read_text(encoding="utf-8"))


def test_all_radar_destinations_have_local_photos():
    assert set(ALL_DESTINATIONS) <= set(PHOTOS)
    assert len({photo["image"] for photo in PHOTOS.values()}) == len(PHOTOS)


@pytest.mark.parametrize("city", sorted(PHOTOS))
def test_photo_content_and_attribution(city):
    photo = PHOTOS[city]
    images.validate_metadata(city, photo)
    path = images.asset_path(photo)
    images.validate_image(path.read_bytes(), photo)
    assert path.stat().st_size < 350_000
    assert 400 <= photo["width"] <= 960
    assert "picsum" not in photo["download_url"]
    assert "unsplash" not in photo["download_url"]


def test_source_is_not_selected_by_misleading_filename():
    assert PHOTOS["大理"]["source_file"] == "File:Erhai Lake in January 2023.jpg"
    assert "Anshunlang bridge Chengdu" in PHOTOS["成都"]["source_file"]
    assert "Qingdao Harbour" in PHOTOS["青岛"]["source_file"]


@pytest.mark.parametrize("path", ["/etc/passwd", "/images/destinations/../key.webp", "/images/destinations/evil.svg"])
def test_invalid_asset_path_rejected(path):
    with pytest.raises(ValueError, match="无效的本地图片路径"):
        images.asset_path({"image": path})


def test_modified_image_rejected():
    photo = PHOTOS["成都"]
    with pytest.raises(ValueError, match="校验值不匹配"):
        images.validate_image(b"not an image", photo)


def test_wrong_dimensions_rejected():
    photo = PHOTOS["成都"]
    wrong = {**photo, "width": photo["width"] + 1}
    with pytest.raises(ValueError, match="格式或尺寸"):
        images.validate_image(images.asset_path(photo).read_bytes(), wrong)


def test_non_webp_rejected():
    output = io.BytesIO()
    Image.new("RGB", (400, 300)).save(output, format="PNG")
    raw = output.getvalue()
    photo = {"sha256": hashlib.sha256(raw).hexdigest(), "width": 400, "height": 300}
    with pytest.raises(ValueError, match="格式或尺寸"):
        images.validate_image(raw, photo)


def test_license_attribution_required():
    photo = {**PHOTOS["青岛"], "license_url": ""}
    with pytest.raises(ValueError, match="缺少许可链接"):
        images.validate_metadata("青岛", photo)


def test_non_commons_source_rejected():
    photo = {**PHOTOS["成都"], "source_url": "https://example.com/image.jpg"}
    with pytest.raises(ValueError, match="不是 Wikimedia Commons"):
        images.validate_metadata("成都", photo)


def test_default_validation_does_not_download(monkeypatch):
    monkeypatch.setattr("sys.argv", ["fetch_city_images.py", "--city", "成都"])

    def unexpected_download(_photo):
        pytest.fail("本地校验不应访问网络")

    monkeypatch.setattr(images, "download_image", unexpected_download)
    assert images.main() == 0


def test_failed_download_does_not_overwrite_existing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(images, "ASSET_DIR", tmp_path)
    photo = PHOTOS["成都"]
    path = images.asset_path(photo)
    original = b"existing file awaiting verification"
    path.write_bytes(original)
    monkeypatch.setattr("sys.argv", ["fetch_city_images.py", "--download", "--city", "成都"])

    def invalid_download(_photo):
        raise ValueError("download checksum mismatch")

    monkeypatch.setattr(images, "download_image", invalid_download)
    assert images.main() == 1
    assert path.read_bytes() == original


def test_download_validates_before_returning_bytes(monkeypatch):
    output = io.BytesIO()
    Image.new("RGB", (960, 640), "blue").save(output, format="JPEG")
    monkeypatch.setattr(images.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(output.getvalue()))
    with pytest.raises(ValueError, match="校验值不匹配"):
        images.download_image(PHOTOS["成都"])
