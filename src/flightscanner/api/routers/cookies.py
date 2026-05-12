"""Cookie management API endpoints (Qunar / Ctrip)."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flightscanner.api.time_utils import iso_utc
from datetime import datetime, timezone

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_COOKIE_FILES: Dict[str, Path] = {
    "qunar": _PROJECT_ROOT / "qunar_cookies.json",
    "ctrip": _PROJECT_ROOT / "ctrip_cookies.json",
}

_KEY_COOKIES: Dict[str, set] = {
    "qunar": {"QN42", "JSESSIONID", "ctt_june"},
    "ctrip": {"ibu_uid", "UBT_VID", "uid", "suid"},
}

_PLATFORM_LABEL = {"qunar": "去哪儿", "ctrip": "携程"}
_LOGIN_TIMEOUT = 120  # seconds


def _check_platform(platform: str) -> None:
    if platform not in _COOKIE_FILES:
        raise HTTPException(status_code=400, detail=f"不支持的平台：{platform}")


# ── Cookie file I/O ───────────────────────────────────────────────────────


def _read_cookies(platform: str) -> List[Dict]:
    path = _COOKIE_FILES[platform]
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
    except Exception:
        pass
    return []


def _parse_content_to_cookies(platform: str, content: str) -> List[Dict]:
    """Parse either a JSON array or a raw 'name=value; ...' cookie string.

    Returns a normalized Playwright-format cookie list ready to persist.
    """
    content = content.strip()
    if not content:
        raise ValueError("内容为空")

    default_domain = ".qunar.com" if platform == "qunar" else ".ctrip.com"

    # JSON array branch
    if content.lstrip().startswith("["):
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败：{e}")
        if not isinstance(raw, list):
            raise ValueError("JSON 顶层必须是数组")
        result: List[Dict] = []
        for c in raw:
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                continue
            cookie: Dict = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", default_domain),
                "path": c.get("path", "/"),
            }
            for opt in ("expires", "httpOnly", "secure", "sameSite"):
                if opt in c:
                    cookie[opt] = c[opt]
            result.append(cookie)
        if not result:
            raise ValueError("未解析到任何有效 Cookie")
        return result

    # Raw cookie-string branch
    if content.lower().startswith("cookie:"):
        content = content[7:].strip()
    result = []
    for pair in content.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        name, value = name.strip(), value.strip()
        if not name:
            continue
        result.append({
            "name": name,
            "value": value,
            "domain": default_domain,
            "path": "/",
        })
    if not result:
        raise ValueError("未能解析出任何 name=value 对")
    return result


# ── Status endpoint ───────────────────────────────────────────────────────


class CookieStatus(BaseModel):
    platform: str
    label: str
    exists: bool
    valid: bool
    count: int
    updated_at: Optional[str] = None
    key_cookies_present: List[str] = []
    key_cookies_missing: List[str] = []


@router.get("/cookies/status", response_model=List[CookieStatus])
def get_cookies_status() -> List[CookieStatus]:
    """Return cookie validity status for all supported platforms."""
    result: List[CookieStatus] = []
    for platform, path in _COOKIE_FILES.items():
        cookies = _read_cookies(platform)
        names = {c.get("name") for c in cookies if c.get("value")}
        key_set = _KEY_COOKIES[platform]
        present = sorted(key_set & names)
        missing = sorted(key_set - names)
        mtime_dt: Optional[datetime] = None
        if path.exists():
            mtime_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        result.append(
            CookieStatus(
                platform=platform,
                label=_PLATFORM_LABEL[platform],
                exists=path.exists(),
                valid=bool(present),
                count=len(cookies),
                updated_at=iso_utc(mtime_dt),
                key_cookies_present=present,
                key_cookies_missing=missing,
            )
        )
    return result


# ── Upload endpoint ───────────────────────────────────────────────────────


class UploadCookieRequest(BaseModel):
    content: str


class UploadCookieResponse(BaseModel):
    platform: str
    count: int
    message: str


@router.post("/cookies/{platform}/upload", response_model=UploadCookieResponse)
def upload_cookies(platform: str, body: UploadCookieRequest) -> UploadCookieResponse:
    """Save uploaded cookies (JSON array or raw 'name=value; ...' string) to disk."""
    _check_platform(platform)
    try:
        cookies = _parse_content_to_cookies(platform, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    path = _COOKIE_FILES[platform]
    path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

    # 主动通知正在运行的 scraper 立即热重载，无需等下一轮采集
    _notify_scrapers_reload(platform)

    return UploadCookieResponse(
        platform=platform,
        count=len(cookies),
        message=f"已保存 {len(cookies)} 条 Cookie 到 {path.name}",
    )


def _notify_scrapers_reload(platform: str) -> None:
    """Trigger reload_cookies_if_changed on the live scraper instance.

    Only called from cookies upload / login-success / delete endpoints.
    No-op when scheduler is disabled or scrapers haven't been initialized yet.
    """
    import asyncio
    try:
        from flightscanner.api import main as api_main

        monitor = api_main._monitor
        if monitor is None:
            return
        loop = getattr(monitor, "_loop", None)
        if not loop or not loop.is_running():
            return
        # 把 reload 调度到调度器自己的事件循环里跑，避免线程安全问题
        for scraper in getattr(monitor, "scrapers", []):
            cls_name = type(scraper).__name__.lower()
            if platform in cls_name and hasattr(scraper, "reload_cookies_if_changed"):
                asyncio.run_coroutine_threadsafe(
                    scraper.reload_cookies_if_changed(), loop
                )
    except Exception:
        # 通知失败不影响 cookie 保存本身；下次 search_flights 自检也会兜底。
        pass


# ── Delete endpoint ───────────────────────────────────────────────────────


@router.delete("/cookies/{platform}", status_code=204)
def delete_cookies(platform: str) -> None:
    _check_platform(platform)
    path = _COOKIE_FILES[platform]
    if path.exists():
        path.unlink()
    # 通知 scraper 清空旧 cookies（mtime 变化会触发，但删除场景文件 mtime 不存在 → 让 scraper 自己处理）
    _notify_scrapers_reload(platform)


# ── QR login background manager ───────────────────────────────────────────


@dataclass
class LoginState:
    status: str = "idle"  # idle | starting | qr_ready | success | error
    qr_base64: Optional[str] = None
    message: str = ""
    done: bool = False
    success: bool = False
    started_at: float = field(default_factory=time.time)


_login_states: Dict[str, LoginState] = {
    "qunar": LoginState(),
    "ctrip": LoginState(),
}
_login_lock = threading.Lock()


def _reset_state(platform: str) -> None:
    with _login_lock:
        _login_states[platform] = LoginState()


def _start_login_thread(platform: str) -> None:
    state = _login_states[platform]
    state.status = "starting"
    state.message = "正在启动浏览器..."
    state.done = False
    state.success = False
    state.qr_base64 = None
    state.started_at = time.time()

    def _on_qr_ready(png_path: str) -> None:
        try:
            data = Path(png_path).read_bytes()
            state.qr_base64 = base64.b64encode(data).decode("ascii")
            state.status = "qr_ready"
            state.message = "请使用手机 App 扫描二维码"
        except Exception as exc:
            state.status = "error"
            state.message = f"读取二维码失败：{exc}"

    def _runner() -> None:
        try:
            if platform == "qunar":
                from scripts.qunar_login import qr_login
            else:
                from scripts.ctrip_login import qr_login  # type: ignore[assignment]

            success = asyncio.run(
                qr_login(headless=True, timeout=_LOGIN_TIMEOUT, on_qr_ready=_on_qr_ready)
            )
            state.success = success
            state.status = "success" if success else "error"
            state.message = "Cookie 已更新" if success else "登录失败或超时"
            if success:
                # 扫码登录成功后通知 scraper 立即重载新 cookie
                _notify_scrapers_reload(platform)
        except Exception as exc:
            state.status = "error"
            state.message = f"出错：{exc}"
        finally:
            state.done = True

    threading.Thread(target=_runner, daemon=True).start()


class StartLoginResponse(BaseModel):
    platform: str
    message: str


@router.post("/cookies/{platform}/login", response_model=StartLoginResponse)
def start_qr_login(platform: str) -> StartLoginResponse:
    """Kick off a headless browser QR-code login in a daemon thread."""
    _check_platform(platform)
    with _login_lock:
        current = _login_states[platform]
        if current.status not in ("idle", "success", "error"):
            raise HTTPException(
                status_code=409, detail="该平台已有进行中的扫码流程"
            )
        _login_states[platform] = LoginState()
    _start_login_thread(platform)
    return StartLoginResponse(
        platform=platform,
        message=f"{_PLATFORM_LABEL[platform]}扫码登录已启动",
    )


class LoginStateResponse(BaseModel):
    platform: str
    status: str
    message: str
    qr_base64: Optional[str] = None
    done: bool
    success: bool
    elapsed_seconds: float
    timeout_seconds: int


@router.get("/cookies/{platform}/login/status", response_model=LoginStateResponse)
def get_login_status(platform: str) -> LoginStateResponse:
    """Poll the current QR-login progress for a platform."""
    _check_platform(platform)
    state = _login_states[platform]
    return LoginStateResponse(
        platform=platform,
        status=state.status,
        message=state.message,
        qr_base64=state.qr_base64,
        done=state.done,
        success=state.success,
        elapsed_seconds=time.time() - state.started_at,
        timeout_seconds=_LOGIN_TIMEOUT,
    )


@router.post("/cookies/{platform}/login/reset", status_code=204)
def reset_login_state(platform: str) -> None:
    """Reset the login state machine (lets the UI dismiss a stale state)."""
    _check_platform(platform)
    _reset_state(platform)
