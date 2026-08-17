"""Cookie management API endpoints (Qunar / Ctrip / Tongcheng)."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flightscanner.api.time_utils import iso_utc

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_COOKIE_FILES: dict[str, Path] = {
    "qunar": _PROJECT_ROOT / "qunar_cookies.json",
    "ctrip": _PROJECT_ROOT / "ctrip_cookies.json",
    "tongcheng": _PROJECT_ROOT / "tongcheng_cookies.json",
}

_KEY_COOKIES: dict[str, set] = {
    "qunar": {"QN42", "JSESSIONID", "ctt_june"},
    "ctrip": {"ibu_uid", "UBT_VID", "uid", "suid"},
    # 同程的公开机票列表接口无需固定登录 Cookie；只要上传内容可解析即可
    # 注入浏览器，因此不以不稳定的营销/会话 Cookie 名误判有效性。
    "tongcheng": set(),
}

_PLATFORM_LABEL = {
    "qunar": "去哪儿",
    "ctrip": "携程",
    "tongcheng": "同程旅行",
}
_COOKIE_DOMAINS = {
    "qunar": ".qunar.com",
    "ctrip": ".ctrip.com",
    "tongcheng": ".ly.com",
}
_LOGIN_SUPPORTED = {"qunar", "ctrip", "tongcheng"}
_LOGIN_TIMEOUT = 120  # default for the existing Qunar/Ctrip flows
_LOGIN_TIMEOUTS = {
    "qunar": _LOGIN_TIMEOUT,
    "ctrip": _LOGIN_TIMEOUT,
    # The official WeChat OAuth QR remains valid for roughly five minutes.
    "tongcheng": 300,
}


def _check_platform(platform: str) -> None:
    if platform not in _COOKIE_FILES:
        raise HTTPException(status_code=400, detail=f"不支持的平台：{platform}")


def _check_login_supported(platform: str) -> None:
    """Reject QR-login calls for platforms that only support manual upload."""
    _check_platform(platform)
    if platform not in _LOGIN_SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"{_PLATFORM_LABEL[platform]}暂不支持扫码登录，请手动上传 Cookie",
        )


# ── Cookie file I/O ───────────────────────────────────────────────────────


def _read_cookies(platform: str) -> list[dict]:
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


def _parse_content_to_cookies(platform: str, content: str) -> list[dict]:
    """Parse either a JSON array or a raw 'name=value; ...' cookie string.

    Returns a normalized Playwright-format cookie list ready to persist.
    """
    content = content.strip()
    if not content:
        raise ValueError("内容为空")

    _check_platform(platform)
    default_domain = _COOKIE_DOMAINS[platform]

    # JSON array branch
    if content.lstrip().startswith("["):
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败：{e}") from e
        if not isinstance(raw, list):
            raise ValueError("JSON 顶层必须是数组")
        result: list[dict] = []
        for c in raw:
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                continue
            cookie: dict = {
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
    login_supported: bool
    updated_at: str | None = None
    key_cookies_present: list[str] = []
    key_cookies_missing: list[str] = []


@router.get("/cookies/status", response_model=list[CookieStatus])
def get_cookies_status() -> list[CookieStatus]:
    """Return cookie validity status for all supported platforms."""
    result: list[CookieStatus] = []
    for platform, path in _COOKIE_FILES.items():
        cookies = _read_cookies(platform)
        names = {c.get("name") for c in cookies if c.get("value")}
        key_set = _KEY_COOKIES[platform]
        present = sorted(key_set & names)
        missing = sorted(key_set - names)
        valid = bool(cookies) if not key_set else bool(present)
        mtime_dt: datetime | None = None
        if path.exists():
            mtime_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        result.append(
            CookieStatus(
                platform=platform,
                label=_PLATFORM_LABEL[platform],
                exists=path.exists(),
                valid=valid,
                count=len(cookies),
                login_supported=platform in _LOGIN_SUPPORTED,
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
        raise HTTPException(status_code=400, detail=str(e)) from e

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
    # 通知 scraper 清空旧 cookies。删除场景没有新的 mtime，
    # 因此由各 scraper 的 reload 实现识别文件消失。
    _notify_scrapers_reload(platform)


# ── QR login background manager ───────────────────────────────────────────


@dataclass
class LoginState:
    status: str = "idle"  # idle | starting | qr_ready | success | error
    qr_base64: str | None = None
    message: str = ""
    done: bool = False
    success: bool = False
    started_at: float = field(default_factory=time.time)


_login_states: dict[str, LoginState] = {
    "qunar": LoginState(),
    "ctrip": LoginState(),
    "tongcheng": LoginState(),
}
_login_lock = threading.Lock()

_ACTIVE_LOGIN_STATUSES = {"starting", "qr_ready"}
QRLogin = Callable[..., Coroutine[Any, Any, bool]]


def _reset_state(platform: str) -> None:
    with _login_lock:
        if _login_states[platform].status in _ACTIVE_LOGIN_STATUSES:
            # Replacing the state object would not stop the daemon thread.  It
            # would instead allow another login to race the first one and both
            # processes could overwrite the same Cookie file.
            raise HTTPException(status_code=409, detail="扫码流程仍在进行中，暂时无法重置")
        _login_states[platform] = LoginState()


def _load_qr_login(platform: str) -> QRLogin:
    """Load a platform's QR-login coroutine without importing Playwright eagerly."""
    if platform == "qunar":
        from scripts.qunar_login import qr_login
    elif platform == "ctrip":
        from scripts.ctrip_login import qr_login
    elif platform == "tongcheng":
        from scripts.tongcheng_login import qr_login
    else:  # Defensive: callers validate the platform before reaching here.
        raise ValueError(f"不支持扫码登录的平台：{platform}")
    return cast(QRLogin, qr_login)


def _finish_login_state(
    platform: str,
    state: LoginState,
    *,
    success: bool,
    message: str,
) -> bool:
    """Atomically complete ``state`` if it is still the active login attempt."""
    with _login_lock:
        if _login_states.get(platform) is not state or state.done:
            return False
        state.success = success
        state.status = "success" if success else "error"
        state.message = message
        state.done = True
        return True


def _start_login_thread(platform: str, state: LoginState) -> None:
    """Run one already-reserved login attempt in a daemon thread."""

    def _on_qr_ready(png_path: str) -> None:
        try:
            data = Path(png_path).read_bytes()
        except Exception as exc:
            message = f"读取二维码失败：{exc}"
            _finish_login_state(platform, state, success=False, message=message)
            # Stop the login coroutine as well as marking the public state
            # terminal; otherwise it could later overwrite this error with a
            # false success result.
            raise RuntimeError(message) from exc

        qr_base64 = base64.b64encode(data).decode("ascii")
        with _login_lock:
            if _login_states.get(platform) is not state or state.done:
                return
            state.qr_base64 = qr_base64
            state.status = "qr_ready"
            state.message = (
                "请使用微信“扫一扫”并确认登录同程旅行"
                if platform == "tongcheng"
                else "请使用对应手机 App 扫描二维码"
            )

    def _runner() -> None:
        try:
            # 确保项目根目录在 sys.path 中，解决从 frontend/ 目录启动时找不到
            # scripts 模块的问题。
            import sys
            from pathlib import Path as _Path
            _root = str(_Path(__file__).resolve().parents[4])
            if _root not in sys.path:
                sys.path.insert(0, _root)

            qr_login = _load_qr_login(platform)
            success: bool = asyncio.run(
                qr_login(
                    headless=True,
                    output_path=str(_COOKIE_FILES[platform]),
                    timeout=_LOGIN_TIMEOUTS[platform],
                    on_qr_ready=_on_qr_ready,
                )
            )
            transitioned = _finish_login_state(
                platform,
                state,
                success=success,
                message=(
                    "Cookie 已更新"
                    if success
                    else (
                        "微信扫码登录失败或超时；请重新生成二维码，确认微信已绑定"
                        "同程账号，或手动上传 Cookie"
                        if platform == "tongcheng"
                        else "登录失败或超时"
                    )
                ),
            )
            if success and transitioned:
                # 扫码登录成功后通知 scraper 立即重载新 cookie
                _notify_scrapers_reload(platform)
        except Exception as exc:
            _finish_login_state(
                platform,
                state,
                success=False,
                message=f"出错：{exc}",
            )

    threading.Thread(target=_runner, daemon=True).start()


class StartLoginResponse(BaseModel):
    platform: str
    message: str


@router.post("/cookies/{platform}/login", response_model=StartLoginResponse)
def start_qr_login(platform: str) -> StartLoginResponse:
    """Kick off a headless browser QR-code login in a daemon thread."""
    _check_login_supported(platform)
    with _login_lock:
        current = _login_states[platform]
        if current.status in _ACTIVE_LOGIN_STATUSES:
            raise HTTPException(
                status_code=409, detail="该平台已有进行中的扫码流程"
            )
        # Reserve the running state while holding the lock.  A second request
        # can no longer observe an intermediate idle state before the daemon
        # thread has had a chance to start.
        state = LoginState(status="starting", message="正在启动浏览器...")
        _login_states[platform] = state
    _start_login_thread(platform, state)
    return StartLoginResponse(
        platform=platform,
        message=f"{_PLATFORM_LABEL[platform]}扫码登录已启动",
    )


class LoginStateResponse(BaseModel):
    platform: str
    status: str
    message: str
    qr_base64: str | None = None
    done: bool
    success: bool
    elapsed_seconds: float
    timeout_seconds: int


@router.get("/cookies/{platform}/login/status", response_model=LoginStateResponse)
def get_login_status(platform: str) -> LoginStateResponse:
    """Poll the current QR-login progress for a platform."""
    _check_login_supported(platform)
    # Copy all fields under one lock so the response cannot mix values from
    # different state transitions (for example status=success, done=false).
    with _login_lock:
        state = _login_states[platform]
        status = state.status
        message = state.message
        qr_base64 = state.qr_base64
        done = state.done
        success = state.success
        elapsed_seconds = max(0.0, time.time() - state.started_at)
    return LoginStateResponse(
        platform=platform,
        status=status,
        message=message,
        qr_base64=qr_base64,
        done=done,
        success=success,
        elapsed_seconds=elapsed_seconds,
        timeout_seconds=_LOGIN_TIMEOUTS[platform],
    )


@router.post("/cookies/{platform}/login/reset", status_code=204)
def reset_login_state(platform: str) -> None:
    """Reset the login state machine (lets the UI dismiss a stale state)."""
    _check_login_supported(platform)
    _reset_state(platform)
