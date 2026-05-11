"""Settings API: read + edit configuration."""

from pathlib import Path
from typing import Optional

from dotenv import set_key
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flightscanner.utils.config import Settings, get_settings, settings as live_settings

router = APIRouter()


def _mask(value: Optional[str]) -> Optional[str]:
    """Mask sensitive values, showing only first 4 chars."""
    if not value:
        return None
    if len(value) <= 6:
        return "***"
    return value[:4] + "***"


# ── Read schemas ────────────────────────────────────────────────────────


class ScraperSettings(BaseModel):
    scraper_type: str
    headless: bool
    timeout: int
    retry_count: int
    max_results_per_platform: int


class NotificationChannelStatus(BaseModel):
    email: bool
    telegram: bool
    wecom: bool
    feishu: bool


class CooldownSettings(BaseModel):
    target_hit: float
    near_30d_low: float
    rebound_warning: float
    below_avg: float
    trend_down: float
    departure_approaching: float


class AISettings(BaseModel):
    model: str
    base_url: str
    api_key_configured: bool


class SettingsResponse(BaseModel):
    scraper: ScraperSettings
    notifications: NotificationChannelStatus
    cooldowns: CooldownSettings
    ai: AISettings
    database_url: str
    notify_below_avg_threshold: float


@router.get("/settings", response_model=SettingsResponse)
def get_current_settings() -> SettingsResponse:
    """Get current application settings (sensitive values masked)."""
    s = get_settings()
    return SettingsResponse(
        scraper=ScraperSettings(
            scraper_type=s.scraper_type,
            headless=s.scraper_headless,
            timeout=s.scraper_timeout,
            retry_count=s.scraper_retry_count,
            max_results_per_platform=s.max_results_per_platform,
        ),
        notifications=NotificationChannelStatus(
            email=bool(s.smtp_host and s.smtp_user),
            telegram=bool(s.telegram_bot_token and s.telegram_chat_id),
            wecom=bool(s.wecom_webhook_url),
            feishu=bool(s.feishu_webhook_url),
        ),
        cooldowns=CooldownSettings(
            target_hit=s.notify_cooldown_target_hit,
            near_30d_low=s.notify_cooldown_near_30d_low,
            rebound_warning=s.notify_cooldown_rebound_warning,
            below_avg=s.notify_cooldown_below_avg,
            trend_down=s.notify_cooldown_trend_down,
            departure_approaching=s.notify_cooldown_departure_approaching,
        ),
        ai=AISettings(
            model=s.deepseek_model,
            base_url=s.deepseek_base_url,
            api_key_configured=bool(s.deepseek_api_key),
        ),
        database_url=s.database_url,
        notify_below_avg_threshold=s.notify_below_avg_threshold,
    )


# ── Update schemas ──────────────────────────────────────────────────────


class UpdateSettingsRequest(BaseModel):
    """Editable subset of Settings.

    Omitted fields are left unchanged. Empty string clears optional fields
    (e.g., webhook URLs). API keys / passwords are write-only — GET masks them.
    """

    # Scraper
    scraper_type: Optional[str] = None
    scraper_headless: Optional[bool] = None
    scraper_timeout: Optional[int] = Field(default=None, ge=5000, le=120000)
    scraper_retry_count: Optional[int] = Field(default=None, ge=0, le=10)
    max_results_per_platform: Optional[int] = Field(default=None, ge=1, le=200)

    # Cooldowns (hours)
    notify_cooldown_target_hit: Optional[float] = Field(default=None, ge=0)
    notify_cooldown_near_30d_low: Optional[float] = Field(default=None, ge=0)
    notify_cooldown_rebound_warning: Optional[float] = Field(default=None, ge=0)
    notify_cooldown_below_avg: Optional[float] = Field(default=None, ge=0)
    notify_cooldown_trend_down: Optional[float] = Field(default=None, ge=0)
    notify_cooldown_departure_approaching: Optional[float] = Field(default=None, ge=0)

    # Threshold
    notify_below_avg_threshold: Optional[float] = Field(default=None, ge=0, le=100)

    # AI
    deepseek_model: Optional[str] = None
    deepseek_base_url: Optional[str] = None
    deepseek_api_key: Optional[str] = None

    # Notification channels — write-only, empty string clears
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    wecom_webhook_url: Optional[str] = None
    feishu_webhook_url: Optional[str] = None
    feishu_webhook_secret: Optional[str] = None


# Map of "field name on Settings" → ".env key (uppercase)".
# pydantic-settings is case_sensitive=False so any case works at read time, but
# we write uppercase by convention.
_ENV_KEYS = {
    "scraper_type": "SCRAPER_TYPE",
    "scraper_headless": "SCRAPER_HEADLESS",
    "scraper_timeout": "SCRAPER_TIMEOUT",
    "scraper_retry_count": "SCRAPER_RETRY_COUNT",
    "max_results_per_platform": "MAX_RESULTS_PER_PLATFORM",
    "notify_cooldown_target_hit": "NOTIFY_COOLDOWN_TARGET_HIT",
    "notify_cooldown_near_30d_low": "NOTIFY_COOLDOWN_NEAR_30D_LOW",
    "notify_cooldown_rebound_warning": "NOTIFY_COOLDOWN_REBOUND_WARNING",
    "notify_cooldown_below_avg": "NOTIFY_COOLDOWN_BELOW_AVG",
    "notify_cooldown_trend_down": "NOTIFY_COOLDOWN_TREND_DOWN",
    "notify_cooldown_departure_approaching": "NOTIFY_COOLDOWN_DEPARTURE_APPROACHING",
    "notify_below_avg_threshold": "NOTIFY_BELOW_AVG_THRESHOLD",
    "deepseek_model": "DEEPSEEK_MODEL",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "wecom_webhook_url": "WECOM_WEBHOOK_URL",
    "feishu_webhook_url": "FEISHU_WEBHOOK_URL",
    "feishu_webhook_secret": "FEISHU_WEBHOOK_SECRET",
}


def _project_env_path() -> Path:
    """Locate .env at the project root (cwd is typically the project root)."""
    here = Path(__file__).resolve()
    # api/routers/settings.py → up 4 → project root
    return here.parents[4] / ".env"


@router.put("/settings", response_model=SettingsResponse)
def update_settings(body: UpdateSettingsRequest) -> SettingsResponse:
    """Update settings: persist to .env AND apply to the live singleton.

    Validation runs by re-instantiating Settings from a draft dict; on success
    the file is written and the in-memory singleton's attributes are mutated
    so background scrapers/notifiers see the new values without restart.
    """
    payload = body.model_dump(exclude_unset=True)

    if not payload:
        raise HTTPException(status_code=400, detail="未提供任何待更新字段")

    # Validate by constructing a temporary Settings using merged values.
    # pydantic_settings will run all field validators (e.g. scraper_type list).
    current_dict = live_settings.model_dump()
    draft_dict = {**current_dict, **payload}
    try:
        # Bypass .env loading by passing _env_file=None; supply all fields explicitly.
        validated = Settings(_env_file=None, **draft_dict)  # type: ignore[arg-type]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置校验失败：{e}")

    # Persist to .env.
    env_path = _project_env_path()
    if not env_path.exists():
        env_path.touch()

    for field, val in payload.items():
        env_key = _ENV_KEYS.get(field)
        if env_key is None:
            continue
        if val is None:
            continue  # absence treated as "unchanged" — already filtered by exclude_unset

        # Empty string for sensitive/optional fields → clear
        if val == "":
            try:
                from dotenv import unset_key

                unset_key(str(env_path), env_key)
            except Exception:
                pass
            continue

        # bool/int/float → str
        if isinstance(val, bool):
            str_val = "true" if val else "false"
        else:
            str_val = str(val)
        set_key(str(env_path), env_key, str_val, quote_mode="never")

    # Refresh the live singleton so subsequent code sees new values immediately.
    for field, _val in payload.items():
        if hasattr(live_settings, field):
            setattr(live_settings, field, getattr(validated, field))

    # Bust the lru_cache so a fresh get_settings() call re-reads .env if needed.
    get_settings.cache_clear()

    return get_current_settings()
