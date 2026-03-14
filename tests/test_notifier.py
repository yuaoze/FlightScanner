"""Unit tests for EmailNotifier."""

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice, PriceTrend
from flightscanner.notifiers import EmailNotifier, FeiShuNotifier
from flightscanner.utils.config import Settings


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    settings = Mock(spec=Settings)
    settings.smtp_host = "smtp.example.com"
    settings.smtp_port = 587
    settings.smtp_user = "test@example.com"
    settings.smtp_password = "test_password"
    return settings


@pytest.fixture
def notifier(mock_settings):
    """Create an EmailNotifier instance."""
    return EmailNotifier(mock_settings)


@pytest.fixture
def sample_flight_price():
    """Create a sample flight price for testing."""
    flight_info = FlightInfo(
        flight_no="CA1234",
        airline="中国国航",
        departure_city="北京",
        arrival_city="上海",
        departure_time="08:00",
        arrival_time="10:30",
        departure_date=date.today() + timedelta(days=7),
        direction=FlightDirection.DEPARTURE,
    )

    return FlightPrice(
        flight_info=flight_info,
        price=Decimal("680.00"),
        currency="CNY",
        seat_class="经济舱",
        available_seats=15,
        scraped_at=datetime.now(timezone.utc),
        source="ctrip",
    )


@pytest.fixture
def sample_trend():
    """Create a sample price trend for testing."""
    return PriceTrend(
        direction="down",
        confidence=0.8,
        recommendation="价格呈下降趋势，建议继续观察。",
        predicted_lowest_price=Decimal("650.00"),
        best_booking_time=datetime.now(),
    )


class TestEmailNotifier:
    """Test cases for EmailNotifier."""

    def test_init_with_valid_settings(self, mock_settings):
        """Test EmailNotifier initialization with valid settings."""
        notifier = EmailNotifier(mock_settings)

        assert notifier.smtp_host == "smtp.example.com"
        assert notifier.smtp_port == 587
        assert notifier.smtp_user == "test@example.com"
        assert notifier.smtp_password == "test_password"

    @pytest.mark.asyncio
    async def test_send_alert_success(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test successful email sending."""
        message = "Test alert message"

        with patch('smtplib.SMTP') as mock_smtp:
            # Configure mock SMTP server
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # Send alert
            result = await notifier.send_alert(sample_flight_price, sample_trend, message)

            # Verify success
            assert result is True

            # Verify SMTP was called correctly
            mock_smtp.assert_called_once_with("smtp.example.com", 587)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("test@example.com", "test_password")
            mock_server.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_without_smtp_config(self, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test that send_alert raises error when SMTP is not configured."""
        # Create settings without SMTP config
        settings = Mock(spec=Settings)
        settings.smtp_host = None
        settings.smtp_port = 587
        settings.smtp_user = None
        settings.smtp_password = None

        notifier = EmailNotifier(settings)

        with pytest.raises(ValueError, match="SMTP not configured"):
            await notifier.send_alert(sample_flight_price, sample_trend, "Test message")

    @pytest.mark.asyncio
    async def test_send_alert_with_partial_smtp_config(self, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test that send_alert raises error when SMTP is partially configured."""
        # Create settings with partial config
        settings = Mock(spec=Settings)
        settings.smtp_host = "smtp.example.com"
        settings.smtp_port = 587
        settings.smtp_user = "test@example.com"
        settings.smtp_password = None  # Missing password

        notifier = EmailNotifier(settings)

        with pytest.raises(ValueError, match="SMTP not configured"):
            await notifier.send_alert(sample_flight_price, sample_trend, "Test message")

    @pytest.mark.asyncio
    async def test_send_alert_handles_smtp_error(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test that send_alert handles SMTP errors gracefully."""
        message = "Test alert message"

        with patch('smtplib.SMTP') as mock_smtp:
            # Simulate SMTP error
            mock_smtp.side_effect = Exception("SMTP connection failed")

            # Should raise the exception
            with pytest.raises(Exception, match="SMTP connection failed"):
                await notifier.send_alert(sample_flight_price, sample_trend, message)

    def test_build_subject_down_trend(self, notifier: EmailNotifier, sample_flight_price: FlightPrice):
        """Test subject line for down trend."""
        trend = PriceTrend(
            direction="down",
            confidence=0.8,
            recommendation="Test",
            predicted_lowest_price=None,
            best_booking_time=None,
        )

        subject = notifier._build_subject(sample_flight_price, trend)

        assert "📉" in subject
        assert "北京" in subject
        assert "上海" in subject
        assert "680" in subject

    def test_build_subject_up_trend(self, notifier: EmailNotifier, sample_flight_price: FlightPrice):
        """Test subject line for up trend."""
        trend = PriceTrend(
            direction="up",
            confidence=0.8,
            recommendation="Test",
            predicted_lowest_price=None,
            best_booking_time=None,
        )

        subject = notifier._build_subject(sample_flight_price, trend)

        assert "📈" in subject

    def test_build_subject_stable_trend(self, notifier: EmailNotifier, sample_flight_price: FlightPrice):
        """Test subject line for stable trend."""
        trend = PriceTrend(
            direction="stable",
            confidence=0.8,
            recommendation="Test",
            predicted_lowest_price=None,
            best_booking_time=None,
        )

        subject = notifier._build_subject(sample_flight_price, trend)

        assert "➡️" in subject

    def test_build_text_body(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test plain text email body generation."""
        message = "Test message"
        body = notifier._build_text_body(sample_flight_price, sample_trend, message)

        assert "CA1234" in body
        assert "中国国航" in body
        assert "北京" in body
        assert "上海" in body
        assert "680" in body
        assert "down" in body
        assert "80%" in body
        # message 是普通文本（非 JSON），_parse_message 返回空字典，不显示增强信息，但函数不应崩溃

    def test_build_html_body(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test HTML email body generation."""
        message = "Test message"
        html = notifier._build_html_body(sample_flight_price, sample_trend, message)

        assert "<!DOCTYPE html>" in html
        assert "CA1234" in html
        assert "中国国航" in html
        assert "北京" in html
        assert "上海" in html
        assert "680" in html
        assert "down" in html
        assert "<style>" in html  # Contains CSS
        # message 是普通文本（非 JSON），_parse_message 返回空字典，不显示增强信息，但函数不应崩溃


# ── FeiShuNotifier 测试 ────────────────────────────────────────────────────────


@pytest.fixture
def feishu_notifier():
    """创建一个使用明确 webhook_url 的 FeiShuNotifier 实例（不依赖全局 settings）。"""
    return FeiShuNotifier(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test_token"
    )


@pytest.fixture
def feishu_notifier_with_secret():
    """创建一个带有签名密钥的 FeiShuNotifier 实例。"""
    return FeiShuNotifier(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test_token",
        webhook_secret="test_secret_key",
    )


class TestFeiShuNotifier:
    """FeiShuNotifier 单元测试。"""

    def test_init_with_explicit_url(self):
        """测试传入显式 webhook_url 时初始化正确。"""
        notifier = FeiShuNotifier(webhook_url="https://example.com/hook")
        assert notifier.webhook_url == "https://example.com/hook"
        assert notifier.webhook_secret is None

    def test_init_with_secret(self):
        """测试同时传入 webhook_url 和 webhook_secret 时初始化正确。"""
        notifier = FeiShuNotifier(
            webhook_url="https://example.com/hook",
            webhook_secret="my_secret",
        )
        assert notifier.webhook_url == "https://example.com/hook"
        assert notifier.webhook_secret == "my_secret"

    @pytest.mark.asyncio
    async def test_send_alert_raises_when_no_url(
        self,
        sample_flight_price: FlightPrice,
        sample_trend: PriceTrend,
    ):
        """未配置 webhook_url 时 send_alert 应抛出 ValueError。"""
        notifier = FeiShuNotifier(webhook_url=None)
        # 确保 settings.feishu_webhook_url 也为空
        with patch("flightscanner.notifiers.feishu_notifier.settings") as mock_settings:
            mock_settings.feishu_webhook_url = None
            mock_settings.feishu_webhook_secret = None
            notifier.webhook_url = None
            with pytest.raises(ValueError, match="FEISHU_WEBHOOK_URL"):
                await notifier.send_alert(sample_flight_price, sample_trend, "测试消息")

    @pytest.mark.asyncio
    async def test_send_alert_success(
        self,
        feishu_notifier: FeiShuNotifier,
        sample_flight_price: FlightPrice,
        sample_trend: PriceTrend,
    ):
        """飞书接口返回 code=0 时 send_alert 应返回 True。"""
        mock_response = Mock()
        mock_response.json.return_value = {"code": 0, "msg": "success"}
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("flightscanner.notifiers.feishu_notifier.httpx.AsyncClient", return_value=mock_client):
            result = await feishu_notifier.send_alert(
                sample_flight_price, sample_trend, "价格提醒测试"
            )

        assert result is True
        mock_client.post.assert_called_once()
        # 验证请求目标 URL 正确
        call_args = mock_client.post.call_args
        assert "feishu.cn" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_alert_feishu_api_error(
        self,
        feishu_notifier: FeiShuNotifier,
        sample_flight_price: FlightPrice,
        sample_trend: PriceTrend,
    ):
        """飞书接口返回 code != 0 时 send_alert 应抛出 RuntimeError。"""
        mock_response = Mock()
        mock_response.json.return_value = {"code": 9499, "msg": "Invalid token"}
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("flightscanner.notifiers.feishu_notifier.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Invalid token"):
                await feishu_notifier.send_alert(
                    sample_flight_price, sample_trend, "测试"
                )

    @pytest.mark.asyncio
    async def test_send_alert_http_error(
        self,
        feishu_notifier: FeiShuNotifier,
        sample_flight_price: FlightPrice,
        sample_trend: PriceTrend,
    ):
        """HTTP 请求失败时 send_alert 应透传 httpx.HTTPError。"""
        import httpx

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("flightscanner.notifiers.feishu_notifier.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPError):
                await feishu_notifier.send_alert(
                    sample_flight_price, sample_trend, "测试"
                )

    @pytest.mark.asyncio
    async def test_send_alert_with_signature_injects_timestamp_and_sign(
        self,
        feishu_notifier_with_secret: FeiShuNotifier,
        sample_flight_price: FlightPrice,
        sample_trend: PriceTrend,
    ):
        """配置了签名密钥时，payload 中应包含 timestamp 和 sign 字段。"""
        captured_payload = {}

        mock_response = Mock()
        mock_response.json.return_value = {"code": 0, "msg": "success"}
        mock_response.raise_for_status = Mock()

        async def fake_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("flightscanner.notifiers.feishu_notifier.httpx.AsyncClient", return_value=mock_client):
            result = await feishu_notifier_with_secret.send_alert(
                sample_flight_price, sample_trend, "签名测试"
            )

        assert result is True
        assert "timestamp" in captured_payload
        assert "sign" in captured_payload
        # timestamp 应为数字字符串
        assert captured_payload["timestamp"].isdigit()
        # sign 应为非空字符串（Base64）
        assert len(captured_payload["sign"]) > 0

    def test_build_payload_structure(
        self,
        feishu_notifier: FeiShuNotifier,
        sample_flight_price: FlightPrice,
        sample_trend: PriceTrend,
    ):
        """_build_payload 应返回符合飞书 Interactive Card 格式的 dict。"""
        import json as _json
        ctx_message = _json.dumps({
            "route": "北京 → 上海",
            "target_date": "2025-06-01",
            "current_price": 680.0,
            "target_price": 800.0,
            "avg_30d": 750.0,
            "min_30d": 650.0,
            "trigger_reason": "target_hit",
            "recommendation": "立即购买",
            "pct_vs_avg": -9.3,
            "pct_vs_target": -15.0,
            "flight_no": "CA1234",
            "airline": "中国国航",
            "departure_time": "08:00",
            "arrival_time": "10:30",
            "source": "qunar",
        })
        payload = feishu_notifier._build_payload(sample_flight_price, sample_trend, ctx_message)

        assert payload["msg_type"] == "interactive"
        card = payload["card"]
        assert "header" in card
        assert "elements" in card
        # 头部标题包含航线信息
        assert "北京" in card["header"]["title"]["content"]
        # target_hit 对应绿色
        assert card["header"]["template"] == "green"
        # elements 不为空
        assert len(card["elements"]) > 0
        # 序列化后应含价格信息
        payload_str = str(payload)
        assert "680" in payload_str
        assert "CA1234" in payload_str

    def test_gen_sign_returns_base64_string(self):
        """_gen_sign 应返回有效的 Base64 编码字符串。"""
        import base64

        timestamp = 1700000000
        secret = "test_secret"
        sign = FeiShuNotifier._gen_sign(timestamp, secret)

        # 应能解码为 32 字节（SHA-256 摘要长度）
        decoded = base64.b64decode(sign)
        assert len(decoded) == 32

    def test_gen_sign_is_deterministic(self):
        """相同 timestamp + secret 应产生相同签名。"""
        timestamp = 1700000000
        secret = "test_secret"
        sign1 = FeiShuNotifier._gen_sign(timestamp, secret)
        sign2 = FeiShuNotifier._gen_sign(timestamp, secret)
        assert sign1 == sign2

    def test_gen_sign_differs_with_different_inputs(self):
        """不同 timestamp 应产生不同签名。"""
        secret = "test_secret"
        sign1 = FeiShuNotifier._gen_sign(1700000000, secret)
        sign2 = FeiShuNotifier._gen_sign(1700000001, secret)
        assert sign1 != sign2


# ── 通知触发逻辑测试 ──────────────────────────────────────────────────────────


class TestNotifyTriggerLogic:
    """测试 PriceMonitorScheduler 的通知触发条件和防骚扰逻辑。"""

    @pytest.fixture
    def mock_route(self):
        """创建带有目标价的路线 Mock 对象。"""
        route = Mock()
        route.id = 1
        route.origin = "北京"
        route.destination = "上海"
        route.target_date = date.today() + timedelta(days=30)
        route.target_price = Decimal("800.00")
        route.last_notified_at = None
        route.last_notified_price = None
        return route

    @pytest.fixture
    def price_stats_with_history(self):
        """有充足历史数据的价格统计。"""
        return {"avg_30d": 900.0, "min_30d": 750.0, "max_30d": 1100.0}

    # ── _should_notify ────────────────────────────────────────────────────────

    def test_should_notify_target_hit(self, mock_route, price_stats_with_history):
        """当前价 <= 目标价时应触发 target_hit。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        should, reason = PriceMonitorScheduler._should_notify(
            mock_route, Decimal("799.00"), price_stats_with_history, 10
        )
        assert should is True
        assert reason == "target_hit"

    def test_should_notify_target_hit_exact(self, mock_route, price_stats_with_history):
        """当前价 == 目标价时应触发 target_hit。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        should, reason = PriceMonitorScheduler._should_notify(
            mock_route, Decimal("800.00"), price_stats_with_history, 10
        )
        assert should is True
        assert reason == "target_hit"

    def test_should_notify_near_30d_low(self, mock_route, price_stats_with_history):
        """当前价接近30天最低价（≤ min_30d * 1.05）时应触发 near_30d_low。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        # min_30d=750，750 * 1.05=787.5，设置价格 785（在5%范围内，高于目标价800不会target_hit）
        mock_route.target_price = Decimal("700.00")  # 目标价低于当前价，不触发target_hit
        should, reason = PriceMonitorScheduler._should_notify(
            mock_route, Decimal("785.00"), price_stats_with_history, 10
        )
        assert should is True
        assert reason == "near_30d_low"

    def test_should_notify_below_avg(self, mock_route):
        """当前价低于30天均价10%且数据量>=7时应触发 below_avg。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        mock_route.target_price = Decimal("700.00")  # 目标价低于当前价
        stats = {"avg_30d": 1000.0, "min_30d": 890.0, "max_30d": 1100.0}
        # 当前价 899，低于 min_30d * 1.05 = 934.5？899 <= 934.5，会触发 near_30d_low
        # 使用 895 < 890 * 1.05 = 934.5，所以仍会触发 near_30d_low
        # 需要确保不触发 near_30d_low：让价格高于 min_30d * 1.05
        # min_30d=890 * 1.05=934.5，使用 935 确保高于阈值
        # avg_30d=1000 * 0.9=900，使用 895 触发 below_avg（895 < 900）
        # 但 895 <= 934.5，所以还是触发 near_30d_low...需要调高 min_30d
        stats2 = {"avg_30d": 1000.0, "min_30d": 850.0, "max_30d": 1100.0}
        # min_30d * 1.05 = 892.5，当前价 895 > 892.5，不触发 near_30d_low
        # avg_30d * 0.9 = 900，当前价 895 < 900，触发 below_avg
        should, reason = PriceMonitorScheduler._should_notify(
            mock_route, Decimal("895.00"), stats2, 10
        )
        assert should is True
        assert reason == "below_avg"

    def test_should_notify_below_avg_insufficient_data(self, mock_route):
        """数据量不足7条时 below_avg 不应触发。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        mock_route.target_price = Decimal("700.00")
        stats = {"avg_30d": 1000.0, "min_30d": 850.0, "max_30d": 1100.0}
        should, reason = PriceMonitorScheduler._should_notify(
            mock_route, Decimal("895.00"), stats, 5  # 仅5条，不足
        )
        assert should is False

    def test_should_not_notify_high_price(self, mock_route, price_stats_with_history):
        """当前价高于目标价且不满足其他条件时不应触发。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        should, reason = PriceMonitorScheduler._should_notify(
            mock_route, Decimal("1050.00"), price_stats_with_history, 10
        )
        assert should is False
        assert reason == ""

    # ── _is_cooldown_active ───────────────────────────────────────────────────

    def test_cooldown_inactive_no_previous_notification(self, mock_route):
        """从未通知过时冷却不应生效。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        scheduler = object.__new__(PriceMonitorScheduler)
        mock_route.last_notified_at = None
        assert scheduler._is_cooldown_active(mock_route, Decimal("700.00")) is False

    def test_cooldown_inactive_past_cooldown_window(self, mock_route):
        """上次通知超过冷却小时数时冷却应失效。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        scheduler = object.__new__(PriceMonitorScheduler)
        # 上次通知 25 小时前（默认冷却 24 小时）
        mock_route.last_notified_at = datetime.now(timezone.utc) - timedelta(hours=25)
        mock_route.last_notified_price = Decimal("800.00")

        with patch("flightscanner.scheduler.price_monitor.settings") as mock_settings:
            mock_settings.notify_cooldown_hours = 24
            result = scheduler._is_cooldown_active(mock_route, Decimal("790.00"))
        assert result is False

    def test_cooldown_active_within_window_no_price_drop(self, mock_route):
        """冷却期内且价格未再降5%时冷却应生效。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        scheduler = object.__new__(PriceMonitorScheduler)
        mock_route.last_notified_at = datetime.now(timezone.utc) - timedelta(hours=12)
        mock_route.last_notified_price = Decimal("800.00")

        with patch("flightscanner.scheduler.price_monitor.settings") as mock_settings:
            mock_settings.notify_cooldown_hours = 24
            result = scheduler._is_cooldown_active(mock_route, Decimal("790.00"))
        assert result is True

    def test_cooldown_broken_by_price_drop(self, mock_route):
        """冷却期内价格再降 >= 5% 时冷却应被打破。"""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

        scheduler = object.__new__(PriceMonitorScheduler)
        mock_route.last_notified_at = datetime.now(timezone.utc) - timedelta(hours=12)
        mock_route.last_notified_price = Decimal("800.00")

        with patch("flightscanner.scheduler.price_monitor.settings") as mock_settings:
            mock_settings.notify_cooldown_hours = 24
            # 800 * 0.95 = 760，760 是恰好降了5%的边界，使用 759 确保 >= 5%
            result = scheduler._is_cooldown_active(mock_route, Decimal("759.00"))
        assert result is False

    # ── _build_alert_message_data ─────────────────────────────────────────────

    def test_build_alert_message_data_valid_json(self):
        """_build_alert_message_data 应返回有效的 JSON 字符串。"""
        import json as _json
        from flightscanner.scheduler.price_monitor import NotifyContext, PriceMonitorScheduler

        ctx = NotifyContext(
            route_id=1,
            origin="北京",
            destination="上海",
            target_date=date.today(),
            target_price=Decimal("800.00"),
            current_price=Decimal("750.00"),
            avg_30d=900.0,
            min_30d=730.0,
            max_30d=1100.0,
            price_count=15,
            trigger_reason="target_hit",
            recommendation="立即购买",
            pct_vs_avg=-16.7,
            pct_vs_target=-6.25,
            source="qunar",
            flight_no="CA1234",
            airline="中国国航",
            departure_time="08:00",
            arrival_time="10:30",
        )

        result = PriceMonitorScheduler._build_alert_message_data(ctx)
        data = _json.loads(result)  # 应能正常解析

        assert data["route"] == "北京 → 上海"
        assert data["current_price"] == 750.0
        assert data["target_price"] == 800.0
        assert data["avg_30d"] == 900.0
        assert data["trigger_reason"] == "target_hit"
        assert data["recommendation"] == "立即购买"
        assert data["flight_no"] == "CA1234"
