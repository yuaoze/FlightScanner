"""飞书群机器人通知实现。

通过飞书自定义机器人 Webhook 发送 Post（富文本）格式的机票价格提醒。
支持可选的签名校验（需在飞书 Webhook 设置中开启安全设置）。
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import List, Optional

import httpx

from flightscanner.interfaces import FlightPrice, Notifier, PriceTrend
from flightscanner.utils.config import settings

logger = logging.getLogger(__name__)


class FeiShuNotifier(Notifier):
    """飞书群机器人通知器。

    通过飞书自定义机器人 Webhook API 发送 Post 富文本格式消息。
    当配置了 webhook_secret 时自动启用签名校验，增强安全性。

    Attributes:
        webhook_url: 飞书自定义机器人 Webhook URL。
        webhook_secret: 飞书 Webhook 签名校验密钥（可选）。
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        """初始化飞书通知器。

        Args:
            webhook_url: 飞书 Webhook URL，默认读取 settings.feishu_webhook_url。
            webhook_secret: 签名校验密钥，默认读取 settings.feishu_webhook_secret。
        """
        self.webhook_url = webhook_url or settings.feishu_webhook_url
        self.webhook_secret = webhook_secret or settings.feishu_webhook_secret

    async def send_alert(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str,
    ) -> bool:
        """通过飞书群机器人发送价格提醒。

        Args:
            flight_price: 航班价格信息。
            trend: 价格趋势分析结果。
            message: 提醒消息文本。

        Returns:
            成功发送返回 True。

        Raises:
            ValueError: 未配置 webhook_url 时抛出。
            httpx.HTTPError: HTTP 请求失败时抛出。
            RuntimeError: 飞书接口返回错误码时抛出。
        """
        if not self.webhook_url:
            raise ValueError("飞书 Webhook URL 未配置 (FEISHU_WEBHOOK_URL)")

        payload = self._build_payload(flight_price, trend, message)

        # 若配置了签名密钥，注入时间戳与签名
        if self.webhook_secret:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = self._gen_sign(timestamp, self.webhook_secret)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
                data = response.json()

            # 飞书接口返回 code=0 表示成功，非 0 为业务错误
            if data.get("code", 0) != 0:
                err_msg = data.get("msg", "未知错误")
                logger.error(
                    f"飞书接口返回错误：code={data.get('code')}, msg={err_msg}"
                )
                raise RuntimeError(f"飞书推送失败：{err_msg}")

            logger.info(
                f"飞书提醒已发送：{flight_price.flight_info.flight_no}"
            )
            return True

        except httpx.HTTPError as e:
            logger.error(f"飞书 HTTP 请求失败：{e}")
            raise

    # ── 消息构建 ──────────────────────────────────────────────────────────────

    def _build_payload(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str,
    ) -> dict:
        """构建飞书 Interactive Card 消息 payload。

        将 message（JSON 字符串）反序列化为上下文字典，并根据触发原因
        选择卡片头部颜色，生成包含价格统计、航班信息和买点建议的富交互卡片。

        Args:
            flight_price: 航班价格信息（用于兜底数据）。
            trend: 价格趋势分析结果。
            message: NotifyContext 序列化的 JSON 字符串。

        Returns:
            符合飞书 Interactive Card API 规范的 JSON dict。
        """
        ctx = self._parse_message(message)

        color_map = {
            "target_hit": "green",
            "near_30d_low": "orange",
            "below_avg": "blue",
        }
        header_color = color_map.get(ctx.get("trigger_reason", ""), "blue")

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"✈️ 机票价格提醒：{ctx['route']}",
                    },
                    "template": header_color,
                },
                "elements": [
                    {
                        "tag": "div",
                        "fields": [
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**当前价格**\n¥{ctx['current_price']:.0f}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**目标价格**\n¥{ctx['target_price']:.0f}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**30天均价**\n¥{ctx['avg_30d']:.0f}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**30天最低**\n¥{ctx['min_30d']:.0f}",
                                },
                            },
                        ],
                    },
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**出发日期**：{ctx['target_date']}\n"
                                f"**航班**：{ctx['flight_no']} {ctx['airline']}  "
                                f"{ctx['departure_time']} → {ctx['arrival_time']}\n"
                                f"**来源**：{ctx['source']}\n"
                                f"**触发原因**："
                                f"{self._reason_label(ctx['trigger_reason'])}\n"
                                f"**买点建议**：{ctx.get('recommendation', '–')}"
                            ),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": (
                                    f"低于均价 {abs(ctx.get('pct_vs_avg', 0)):.1f}%"
                                    f"  ·  低于目标价 {abs(ctx.get('pct_vs_target', 0)):.1f}%"
                                ),
                            }
                        ],
                    },
                ],
            },
        }

    def _reason_label(self, reason: str) -> str:
        """将触发原因代码转换为可读标签。

        Args:
            reason: 触发原因代码字符串。

        Returns:
            带 emoji 的中文标签字符串。
        """
        return {
            "target_hit": "已达目标价 🎯",
            "near_30d_low": "接近30天最低价 📉",
            "below_avg": "显著低于均价 💡",
        }.get(reason, reason)

    def _parse_message(self, message: str) -> dict:
        """将 JSON 消息字符串反序列化为字典，解析失败时返回兜底字典。

        Args:
            message: JSON 格式的消息字符串（或普通文本）。

        Returns:
            包含通知上下文字段的字典；解析失败时返回含 message 文本的兜底字典。
        """
        try:
            return json.loads(message)
        except Exception:
            return {
                "route": message,
                "current_price": 0,
                "target_price": 0,
                "avg_30d": 0,
                "min_30d": 0,
                "target_date": "",
                "trigger_reason": "",
                "recommendation": "",
                "pct_vs_avg": 0,
                "pct_vs_target": 0,
                "flight_no": "",
                "airline": "",
                "departure_time": "",
                "arrival_time": "",
                "source": "",
            }

    @staticmethod
    def _gen_sign(timestamp: int, secret: str) -> str:
        """生成飞书 Webhook 签名。

        签名算法：HMAC-SHA256(key=f"{timestamp}\\n{secret}", msg=b"") → Base64。

        Args:
            timestamp: Unix 时间戳（秒）。
            secret: 飞书 Webhook 安全设置中的签名密钥。

        Returns:
            Base64 编码的签名字符串。
        """
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            key=string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")
