"""周末低价雷达模块。

提供周末往返低价航班发现与监控功能：
- destinations: 目的地池、签证元数据、视觉资源
- scanner: WeekendRadarScanner 核心扫描引擎
- brief_generator: AI 种草文案生成（签证感知，带规则降级）
"""

from flightscanner.weekend_radar.scanner import (
    WeekendDeal,
    WeekendRadarScanner,
    get_upcoming_weekends,
    get_weekend_label,
)
from flightscanner.weekend_radar.brief_generator import generate_weekend_brief
from flightscanner.weekend_radar.destinations import (
    ALL_DESTINATIONS,
    DOMESTIC_DESTINATIONS,
    INTERNATIONAL_DESTINATIONS,
    HSR_EXCLUSION_FROM_SHANGHAI,
    VISA_INFO,
    DESTINATION_EMOJI,
    DESTINATION_GRADIENT,
    DESTINATION_IMAGE,
)

__all__ = [
    "WeekendDeal",
    "WeekendRadarScanner",
    "get_upcoming_weekends",
    "get_weekend_label",
    "generate_weekend_brief",
    "ALL_DESTINATIONS",
    "DOMESTIC_DESTINATIONS",
    "INTERNATIONAL_DESTINATIONS",
    "HSR_EXCLUSION_FROM_SHANGHAI",
    "VISA_INFO",
    "DESTINATION_EMOJI",
    "DESTINATION_GRADIENT",
    "DESTINATION_IMAGE",
]
