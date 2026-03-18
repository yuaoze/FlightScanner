"""Cookie 管理 UI 组件。

提供去哪儿 / 携程 Cookie 状态查看和扫码刷新功能。

架构说明：
- `get_login_manager()` — @st.cache_resource 单例，整个进程共享
- `render_cookie_manager_dialog()` — @st.dialog 对话框入口
- `_cookie_dialog_fragment()` — @st.fragment，对话框内容的全部 UI 逻辑
  使用 st.rerun(scope="fragment") 轮询，不关闭对话框；
  只有"完成"/"关闭"按钮才调用 st.rerun()（完整刷新）来关闭对话框。
"""

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

# ── 常量 ─────────────────────────────────────────────────────────────────────

_project_root = Path(__file__).resolve().parent.parent.parent

_COOKIE_FILES: dict[str, Path] = {
    "qunar": _project_root / "qunar_cookies.json",
    "ctrip": _project_root / "ctrip_cookies.json",
}

_PLATFORM_NAMES: dict[str, str] = {
    "qunar": "去哪儿",
    "ctrip": "携程",
}

# 与各平台 login 脚本中的检测集合保持一致
_KEY_COOKIES: dict[str, set[str]] = {
    "qunar": {"QN42", "JSESSIONID", "ctt_june"},
    "ctrip": {"ibu_uid", "UBT_VID", "uid", "suid"},
}

_LOGIN_TIMEOUT = 120  # 秒


# ── 共享状态 ──────────────────────────────────────────────────────────────────


@dataclass
class CookieLoginState:
    """后台登录线程与 Streamlit 主线程之间的共享状态（读写均在线程内/主线程中完成）。"""

    status: str = "idle"  # idle | starting | qr_ready | success | error
    qr_path: str | None = None
    message: str = ""
    done: bool = False
    success: bool = False
    started_at: float = field(default_factory=time.time)


# ── Cookie 登录管理器 ─────────────────────────────────────────────────────────


class CookieLoginManager:
    """管理后台扫码登录线程，由 get_login_manager() 缓存为进程级单例。"""

    def __init__(self) -> None:
        self._states: dict[str, CookieLoginState] = {
            "qunar": CookieLoginState(),
            "ctrip": CookieLoginState(),
        }

    def get_state(self, platform: str) -> CookieLoginState:
        return self._states[platform]

    def is_running(self, platform: str) -> bool:
        """仅在后台线程正在运行时返回 True（不包含 idle/success/error）。"""
        return self._states[platform].status not in ("idle", "success", "error")

    def reset(self, platform: str) -> None:
        """丢弃当前状态，恢复初始 idle 状态（后台线程若仍在跑会自然超时退出）。"""
        self._states[platform] = CookieLoginState()

    def start_login(self, platform: str) -> None:
        """在 daemon 后台线程中启动扫码登录流程。"""
        state = self._states[platform]
        state.status = "starting"
        state.message = "正在启动浏览器..."
        state.done = False
        state.success = False
        state.qr_path = None
        state.started_at = time.time()

        def _on_qr_ready(png_path: str) -> None:
            state.qr_path = png_path
            state.status = "qr_ready"
            state.message = "请用 APP 扫描二维码"

        def _thread_target() -> None:
            try:
                if platform == "qunar":
                    from scripts.qunar_login import qr_login
                else:
                    from scripts.ctrip_login import qr_login  # type: ignore[assignment]

                success = asyncio.run(
                    qr_login(headless=True, on_qr_ready=_on_qr_ready)
                )
                state.success = success
                state.status = "success" if success else "error"
                state.message = "Cookie 已更新！" if success else "登录失败或超时"
            except Exception as exc:
                state.status = "error"
                state.message = f"出错：{exc}"
            finally:
                state.done = True

        threading.Thread(target=_thread_target, daemon=True).start()


@st.cache_resource
def get_login_manager() -> CookieLoginManager:
    """返回进程级别的 CookieLoginManager 单例（@st.cache_resource 保证唯一实例）。"""
    return CookieLoginManager()


# ── Cookie 文件工具函数 ───────────────────────────────────────────────────────


def _get_cookie_info(platform: str) -> dict:
    """读取 Cookie 文件，返回有效性信息。"""
    path = _COOKIE_FILES[platform]
    if not path.exists():
        return {"exists": False, "valid": False, "mtime": None, "count": 0}

    try:
        cookies: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"exists": True, "valid": False, "mtime": path.stat().st_mtime, "count": 0}

    # 过滤空值 Cookie 后与关键集合求交
    cookie_names = {c["name"] for c in cookies if c.get("value")}
    has_key = bool(_KEY_COOKIES[platform] & cookie_names)

    return {
        "exists": True,
        "valid": has_key,
        "mtime": path.stat().st_mtime,
        "count": len(cookies),
    }


def _fmt_age(mtime: float | None) -> str:
    """将文件修改时间格式化为"N 分钟前"/"N 小时前"/"N 天前"。"""
    if mtime is None:
        return "—"
    elapsed = time.time() - mtime
    if elapsed < 3600:
        return f"{max(int(elapsed // 60), 1)} 分钟前"
    if elapsed < 86400:
        return f"{int(elapsed // 3600)} 小时前"
    return f"{int(elapsed // 86400)} 天前"


# ── Fragment：对话框内的全部 UI 逻辑 ─────────────────────────────────────────


@st.fragment
def _cookie_dialog_fragment() -> None:
    """对话框内容片段；st.rerun(scope='fragment') 只刷新此片段，不关闭对话框。"""
    manager = get_login_manager()
    active = st.session_state.get("cookie_mgr_active")

    # ── 登录进行中 / 完成：显示扫码进度 ─────────────────────────────
    if active:
        state = manager.get_state(active)
        platform_name = _PLATFORM_NAMES[active]

        # 防御：服务重启导致 manager 被重置但 session_state 残留 active
        if state.status == "idle":
            st.session_state.pop("cookie_mgr_active", None)
            st.rerun(scope="fragment")
            return

        # ── 已结束（成功或失败）────────────────────────────────────
        if state.done:
            if state.success:
                st.success(f"✓ {state.message}")
                info = _get_cookie_info(active)
                if info["valid"]:
                    st.caption(
                        f"已保存 {info['count']} 条 Cookie　·　"
                        f"更新于 {_fmt_age(info['mtime'])}"
                    )
            else:
                st.error(f"✗ {state.message}")

            st.divider()
            col_done, col_retry = st.columns(2)
            with col_done:
                if st.button(
                    "完成",
                    key="qr_done_close",
                    type="primary",
                    use_container_width=True,
                ):
                    manager.reset(active)
                    st.session_state.pop("cookie_mgr_active", None)
                    st.rerun()  # 完整刷新 → 关闭对话框

            if not state.success:
                with col_retry:
                    if st.button("重试", key="qr_retry", use_container_width=True):
                        manager.reset(active)
                        manager.start_login(active)
                        st.rerun(scope="fragment")
            return

        # ── 正在进行中：显示二维码 + 进度 ─────────────────────────
        elapsed = time.time() - state.started_at
        st.markdown(f"**正在刷新 {platform_name} Cookie**")

        col_qr, col_info = st.columns([1, 2])

        with col_qr:
            if state.qr_path and Path(state.qr_path).exists():
                st.image(state.qr_path, width=220)
            else:
                # 二维码尚未生成（浏览器还在启动或加载页面中）
                st.markdown(
                    "<div style='height:220px;display:flex;align-items:center;"
                    "justify-content:center;color:#94a3b8;font-size:0.9rem'>"
                    "⏳ 二维码加载中…</div>",
                    unsafe_allow_html=True,
                )

        with col_info:
            st.write(f"**状态：** {state.message or '正在启动浏览器…'}")
            st.progress(
                min(elapsed / _LOGIN_TIMEOUT, 1.0),
                text=f"已等待 {int(elapsed)}s / {_LOGIN_TIMEOUT}s",
            )
            st.caption("打开手机 APP，扫描左侧二维码完成登录")
            if st.button("取消", key="qr_cancel"):
                manager.reset(active)
                st.session_state.pop("cookie_mgr_active", None)
                st.rerun(scope="fragment")  # 返回状态视图，不关闭对话框

        # 轮询：每 2s 刷新一次片段（不影响对话框外的页面）
        time.sleep(2)
        st.rerun(scope="fragment")

    # ── 默认：显示 Cookie 状态面板 ───────────────────────────────────
    else:
        tab_qunar, tab_ctrip = st.tabs(["去哪儿", "携程"])

        for tab, platform in ((tab_qunar, "qunar"), (tab_ctrip, "ctrip")):
            with tab:
                info = _get_cookie_info(platform)
                platform_name = _PLATFORM_NAMES[platform]

                if not info["exists"]:
                    st.warning(
                        f"Cookie 文件不存在（{_COOKIE_FILES[platform].name}）"
                    )
                else:
                    col_s, col_t = st.columns([2, 3])
                    with col_s:
                        if info["valid"]:
                            st.success(f"✓ Cookie 有效（{info['count']} 条）")
                        else:
                            st.error("✗ 关键 Cookie 缺失，需要重新登录")
                    with col_t:
                        st.caption(f"最后更新：{_fmt_age(info['mtime'])}")

                st.divider()

                if st.button(
                    f"刷新 {platform_name} Cookie",
                    key=f"refresh_{platform}",
                    type="primary",
                    use_container_width=True,
                ):
                    manager.start_login(platform)
                    st.session_state["cookie_mgr_active"] = platform
                    st.rerun(scope="fragment")  # 片段刷新，对话框保持开启


# ── 对话框入口 ────────────────────────────────────────────────────────────────


@st.dialog("Cookie 管理", width="large")
def render_cookie_manager_dialog() -> None:
    """弹出 Cookie 管理对话框（状态显示 + 扫码刷新全部在对话框内完成）。"""
    _cookie_dialog_fragment()
