# Changelog

## [0.2.2] - 2026-03-11

### Added
- CtripScraper 完善：新增 32 个城市的 IATA 城市码映射表（`CITY_CODES`），修正 URL 格式为 `oneway-bjs-sha` / `round-bjs-sha`
- CtripScraper 双策略采集：优先拦截携程 XHR API 响应解析 JSON，API 无数据时降级为 DOM 解析
- CtripScraper 反爬注入：通过 `context.add_init_script()` 隐藏 `navigator.webdriver`、清除 CDP `cdc_` 属性、伪造 `window.chrome` 对象
- `ScraperRegistry` 爬虫工厂/注册表：支持按平台名动态构建爬虫实例，内置 `get()` / `build_enabled()` / `register()` 方法
- `config.py` `scraper_type` 支持逗号分隔多平台（如 `"qunar,ctrip"`），含格式校验和自动去重
- `PriceMonitorScheduler` 多源并行采集：`asyncio.gather` 并发调用所有启用爬虫，结果合并去重（相同 `(flight_no, seat_class)` 保留最低价）
- 新增 `tests/test_registry.py`（17 个测试用例，覆盖工厂方法和去重逻辑）

### Changed
- `PriceMonitorScheduler.scraper` 重构为 `scrapers: List[FlightScraper]`，支持多平台并行
- `PriceMonitorScheduler.__init__` 改用 `ScraperRegistry.get()` 替代硬编码类名

### Fixed
- 修复 `tests/test_scraper.py` 中 5 个因引用已删除方法（`_wait_for_results` / `_parse_flights`）导致失败的测试用例

## [0.2.1] - 2026-03-11

### Added
- FeiShuNotifier：通过飞书自定义机器人 Webhook 发送 Post 富文本格式价格提醒
- 支持可选 HMAC-SHA256 签名校验（需在飞书安全设置中开启）
- `config.py` 新增 `feishu_webhook_url` / `feishu_webhook_secret` 配置项
- `build_notifiers()` 工厂函数：统一管理四渠道（Email / Telegram / WeCom / 飞书）初始化逻辑
- 新增 `TestFeiShuNotifier` 单元测试（10 个测试用例）

### Changed
- `PriceMonitorScheduler.__init__` 改用 `build_notifiers()` 工厂函数替代手动 if-else 初始化

## [0.2.0] - 2026-03-10

### Fixed
- RuleBasedAnalyzer: 修复 `predict_trend()` 因列表排序方向错误导致趋势方向判断相反的 bug
- CtripScraper: 移除 `@retry` 装饰器，修复单元测试中 `page.close()` 多次调用断言失败

### Added
- TelegramNotifier: 通过 Telegram Bot API 推送价格提醒
- WeComNotifier: 通过企业微信群机器人 Webhook 推送价格提醒
- PriceMonitorScheduler: 支持多渠道同时通知（Email / Telegram / WeCom 并行发送）
- `config.py` 新增 `wecom_webhook_url` 配置项

## [0.1.0] - Phase 1 MVP

- Qunar Playwright 爬虫（Cookie 注入 + 扫码登录刷新）
- APScheduler 定时采集
- SQLite 三表结构
- Email SMTP 通知
- RuleBasedAnalyzer 规则型趋势分析
- Streamlit 仪表板（可展开路线卡片 + Altair 图）
- 31 个单元测试
