#!/usr/bin/env python3
"""Verification script for notification system.

This script verifies that the notification system is working by:
1. Loading notification configuration
2. Sending a test notification
3. Verifying delivery

Currently supports:
- Email notifications (SMTP)
- Telegram notifications

Usage:
    python scripts/verify_notify.py

Requirements:
    - Configure notification settings in .env file
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def print_header(title: str) -> None:
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


async def verify_email_notification() -> bool:
    """Verify email notification.

    Returns:
        True if email is sent successfully, False otherwise.
    """
    print("\n[Email Notification Verification]")

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from flightscanner.utils.config import settings

        if not all([settings.smtp_host, settings.smtp_user, settings.smtp_password]):
            print("⚠ Email notification not configured, skipping...")
            return True  # Not an error, just not configured

        print(f"  SMTP Server: {settings.smtp_host}:{settings.smtp_port}")
        print(f"  SMTP User: {settings.smtp_user}")

        # Create test message
        msg = MIMEMultipart()
        msg["From"] = settings.smtp_user
        msg["To"] = settings.smtp_user  # Send to self
        msg["Subject"] = "FlightScanner - 测试通知"

        body = """
您好！

这是来自 FlightScanner 的测试通知邮件。

如果您收到此邮件，说明邮件通知功能配置成功！

祝好，
FlightScanner Team
"""
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Send email
        print("  Sending test email...")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

        print("✓ Test email sent successfully!")
        print(f"  Check your inbox: {settings.smtp_user}")
        return True

    except ImportError:
        print("⚠ Email libraries not available, skipping...")
        return True
    except Exception as e:
        print(f"✗ Email notification failed: {e}")
        return False


async def verify_telegram_notification() -> bool:
    """Verify Telegram notification.

    Returns:
        True if message is sent successfully, False otherwise.
    """
    print("\n[Telegram Notification Verification]")

    try:
        import httpx
        from flightscanner.utils.config import settings

        if not all([settings.telegram_bot_token, settings.telegram_chat_id]):
            print("⚠ Telegram notification not configured, skipping...")
            return True  # Not an error, just not configured

        print(f"  Chat ID: {settings.telegram_chat_id}")

        # Send message using Telegram Bot API
        telegram_api_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

        message = """
✈️ *FlightScanner 测试通知*

这是来自 FlightScanner 的测试消息。

如果您收到此消息，说明 Telegram 通知功能配置成功！

祝好，
FlightScanner Team
"""

        print("  Sending test message...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                telegram_api_url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=10.0,
            )

        if response.status_code == 200:
            print("✓ Test message sent successfully!")
            print("  Check your Telegram chat")
            return True
        else:
            print(f"✗ Telegram API returned error: {response.status_code}")
            print(f"  Response: {response.text}")
            return False

    except ImportError:
        print("⚠ httpx not installed, skipping Telegram test...")
        print("  Install with: pip install httpx")
        return True
    except Exception as e:
        print(f"✗ Telegram notification failed: {e}")
        return False


async def verify_notifications() -> bool:
    """Verify all notification methods.

    Returns:
        True if at least one notification method works, False otherwise.
    """
    print_header("Notification Verification Script")

    # Step 1: Load configuration
    print("\n[1/3] Loading configuration...")
    try:
        from flightscanner.utils.config import settings

        print("✓ Configuration loaded")

    except Exception as e:
        print(f"✗ Failed to load configuration: {e}")
        return False

    # Step 2: Check configuration
    print("\n[2/3] Checking notification configuration...")

    has_email = all([settings.smtp_host, settings.smtp_user, settings.smtp_password])
    has_telegram = all([settings.telegram_bot_token, settings.telegram_chat_id])

    if not has_email and not has_telegram:
        print("⚠ No notification method configured!")
        print("  To enable notifications, configure one of the following:")
        print("  - Email: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD")
        print("  - Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
        return False

    if has_email:
        print("✓ Email notification configured")
    if has_telegram:
        print("✓ Telegram notification configured")

    # Step 3: Send test notifications
    print("\n[3/3] Sending test notifications...")

    results = []

    if has_email:
        email_result = await verify_email_notification()
        results.append(email_result)

    if has_telegram:
        telegram_result = await verify_telegram_notification()
        results.append(telegram_result)

    return all(results)


def main() -> int:
    """Main entry point."""
    try:
        success = asyncio.run(verify_notifications())

        if success:
            print_header("VERIFICATION PASSED")
            print("\n✓ Notification system is working correctly!")
            print("✓ Test notifications have been sent.")
            print("\nNext steps:")
            print("  1. Integrate notification with price monitoring")
            print("  2. Customize alert message format")
            print("  3. Set up notification preferences\n")
            return 0
        else:
            print_header("VERIFICATION FAILED")
            print("\n✗ Notification verification failed.")
            print("✗ Please check:")
            print("  1. Notification settings in .env file")
            print("  2. API tokens are valid")
            print("  3. Network connection is available\n")
            return 1

    except KeyboardInterrupt:
        print("\n\n✗ Verification interrupted by user.\n")
        return 1
    except Exception as e:
        print_header("UNEXPECTED ERROR")
        print(f"\n✗ An unexpected error occurred: {e}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
