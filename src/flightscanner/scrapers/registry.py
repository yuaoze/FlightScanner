"""爬虫工厂/注册表。

提供按平台名动态构建 FlightScraper 实例的统一入口，
支持单平台选择和多平台批量构建。

用法::

    # 获取单个爬虫实例
    scraper = ScraperRegistry.get("qunar", headless=True)

    # 批量构建多个爬虫（多源聚合场景）
    scrapers = ScraperRegistry.build_enabled(["qunar", "ctrip"], headless=True)
"""

import logging
from typing import Any, Dict, List, Optional, Type

from flightscanner.interfaces import FlightScraper

logger = logging.getLogger(__name__)


class ScraperRegistry:
    """爬虫平台注册表。

    以类变量存储平台名称到爬虫类的映射关系，提供工厂方法
    按需实例化爬虫，无需在调用方硬编码具体类型。

    Attributes:
        _registry: 平台名（小写） → FlightScraper 子类 的映射字典。
    """

    # 延迟导入以避免循环依赖；在首次访问 _registry 时填充
    _registry: Optional[Dict[str, Type[FlightScraper]]] = None

    @classmethod
    def _get_registry(cls) -> Dict[str, Type[FlightScraper]]:
        """返回（并延迟初始化）注册表字典。

        采用延迟导入避免模块加载时的循环依赖。

        Returns:
            平台名 → 爬虫类 的映射字典。
        """
        if cls._registry is None:
            from flightscanner.scrapers.ctrip_scraper import CtripScraper
            from flightscanner.scrapers.qunar_scraper import QunarScraper

            cls._registry = {
                "qunar": QunarScraper,
                "ctrip": CtripScraper,
            }
        return cls._registry

    @classmethod
    def list_platforms(cls) -> List[str]:
        """返回所有已注册的平台名称列表（字母升序）。

        Returns:
            平台名字符串列表，例如 ``["ctrip", "qunar"]``。
        """
        return sorted(cls._get_registry().keys())

    @classmethod
    def get(cls, platform: str, **kwargs: Any) -> FlightScraper:
        """按平台名实例化并返回对应爬虫。

        Args:
            platform: 平台名称（不区分大小写），如 ``"qunar"``、``"ctrip"``。
            **kwargs: 透传给爬虫构造函数的关键字参数，例如
                ``headless=True``、``timeout=30000``。

        Returns:
            已初始化的 FlightScraper 实例。

        Raises:
            ValueError: 当 ``platform`` 未在注册表中找到时抛出。
        """
        key = platform.strip().lower()
        registry = cls._get_registry()
        if key not in registry:
            raise ValueError(
                f"未知爬虫平台：'{platform}'。"
                f"已注册平台：{cls.list_platforms()}"
            )
        scraper_cls = registry[key]
        logger.debug("ScraperRegistry：创建 %s 实例（参数=%s）", scraper_cls.__name__, kwargs)
        return scraper_cls(**kwargs)

    @classmethod
    def build_enabled(
        cls,
        platforms: List[str],
        **kwargs: Any,
    ) -> List[FlightScraper]:
        """批量构建多个爬虫实例。

        按 ``platforms`` 列表顺序依次实例化，所有实例共享相同的构造参数。
        列表为空时直接返回空列表，不会抛出异常。

        Args:
            platforms: 需要启用的平台名称列表，例如 ``["qunar", "ctrip"]``。
            **kwargs: 透传给所有爬虫构造函数的关键字参数。

        Returns:
            按输入顺序排列的 FlightScraper 实例列表。

        Raises:
            ValueError: 当列表中包含未知平台名时抛出。
        """
        scrapers: List[FlightScraper] = []
        for platform in platforms:
            scraper = cls.get(platform, **kwargs)
            scrapers.append(scraper)
            logger.info("已启用爬虫：%s", platform)
        return scrapers

    @classmethod
    def register(
        cls,
        platform: str,
        scraper_cls: Type[FlightScraper],
    ) -> None:
        """向注册表添加自定义爬虫平台（扩展用途）。

        Args:
            platform: 平台名称（将自动转为小写）。
            scraper_cls: 实现了 FlightScraper ABC 的爬虫类。

        Raises:
            TypeError: 当 ``scraper_cls`` 不是 FlightScraper 子类时抛出。
        """
        if not (isinstance(scraper_cls, type) and issubclass(scraper_cls, FlightScraper)):
            raise TypeError(
                f"{scraper_cls} 不是 FlightScraper 的子类"
            )
        registry = cls._get_registry()
        registry[platform.strip().lower()] = scraper_cls
        logger.info("ScraperRegistry：已注册平台 '%s' → %s", platform, scraper_cls.__name__)
