"""Notifier implementations for alert delivery."""

from typing import List

from .email_notifier import EmailNotifier
from .feishu_notifier import FeiShuNotifier
from .telegram_notifier import TelegramNotifier
from .wecom_notifier import WeComNotifier

__all__ = ["EmailNotifier", "FeiShuNotifier", "TelegramNotifier", "WeComNotifier", "build_notifiers"]


def build_notifiers(settings, enable_notifications: bool = True) -> List:
    """根据配置构建并返回已启用的通知器列表。

    按顺序检测 Email、Telegram、WeCom、飞书四个渠道的配置项是否就绪，
    将已配置的通知器实例追加到列表中返回。

    Args:
        settings: 应用配置对象（pydantic Settings 实例）。
        enable_notifications: 全局通知开关，False 时直接返回空列表。

    Returns:
        已初始化且配置完整的 Notifier 实例列表。
    """
    from flightscanner.interfaces import Notifier
    import logging

    logger = logging.getLogger(__name__)
    notifiers: List[Notifier] = []

    if not enable_notifications:
        return notifiers

    if settings.smtp_host and settings.smtp_user:
        notifiers.append(EmailNotifier(settings))
        logger.info("EmailNotifier 已启用")

    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers.append(TelegramNotifier())
        logger.info("TelegramNotifier 已启用")

    if settings.wecom_webhook_url:
        notifiers.append(WeComNotifier())
        logger.info("WeComNotifier 已启用")

    if settings.feishu_webhook_url:
        notifiers.append(FeiShuNotifier())
        logger.info("FeiShuNotifier 已启用")

    return notifiers
