"""Configuration management using Pydantic Settings.

This module provides a centralized configuration management system
that loads settings from environment variables and .env files.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden via environment variables or .env file.
    Environment variables take precedence over .env file values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # DeepSeek API Configuration (Compatible with OpenAI API format)
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")
    deepseek_model: str = Field(
        default="deepseek-chat",
        description="DeepSeek model to use for analysis",
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        description="DeepSeek API base URL",
    )

    # Database Configuration
    database_url: str = Field(
        default="sqlite:///flightscanner.db",
        description="Database connection URL",
    )

    # Email Notification Configuration
    smtp_host: Optional[str] = Field(default=None, description="SMTP server host")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: Optional[str] = Field(default=None, description="SMTP username")
    smtp_password: Optional[str] = Field(default=None, description="SMTP password")

    # Telegram Notification Configuration
    telegram_bot_token: Optional[str] = Field(
        default=None, description="Telegram bot token"
    )
    telegram_chat_id: Optional[str] = Field(
        default=None, description="Telegram chat ID"
    )

    # Scraper Configuration
    scraper_type: str = Field(
        default="qunar", description="Scraper to use: 'qunar' or 'ctrip'"
    )
    scraper_headless: bool = Field(
        default=True, description="Run browser in headless mode"
    )
    scraper_timeout: int = Field(
        default=30000, description="Page load timeout in milliseconds"
    )
    scraper_retry_count: int = Field(
        default=3, description="Number of retry attempts for failed requests"
    )
    qunar_cookies: Optional[str] = Field(
        default=None,
        description="Qunar cookies JSON string for authentication (optional)",
    )

    # Alert Configuration
    alert_price_threshold: int = Field(
        default=800, description="Default price threshold for alerts (CNY)"
    )

    @field_validator("scraper_type")
    @classmethod
    def validate_scraper_type(cls, v: str) -> str:
        """Validate scraper type."""
        allowed = ["qunar", "ctrip"]
        if v.lower() not in allowed:
            raise ValueError(f"Scraper type must be one of {allowed}")
        return v.lower()

    @field_validator("deepseek_api_key")
    @classmethod
    def validate_deepseek_key(cls, v: str) -> str:
        """Validate DeepSeek API key format."""
        if v and not v.startswith("sk-"):
            raise ValueError("DeepSeek API key must start with 'sk-'")
        return v

    @field_validator("scraper_timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        """Validate scraper timeout is reasonable."""
        if v < 5000:
            raise ValueError("Scraper timeout should be at least 5000ms")
        return v


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Settings: Application settings instance.

    Note:
        Uses lru_cache to ensure settings are only loaded once.
    """
    return Settings()


# Convenience export
settings = get_settings()
