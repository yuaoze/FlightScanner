# Changelog

## [1.4.2] - 2026-04-30

### Fixed

- **去哪儿国内线 DOM 抓取**：国内线 `touchInnerList` 的 `data` 字段是反爬混淆的 scrambled JSON + 内嵌 IIFE，改为从 React 渲染完成后的 `ul.list-content li.list-row.item` DOM 直接取数
- **国内往返分程采集**：`search_flights()` 增加国内往返分支，复用 `_search_inter_roundtrip_fallback` 分程搜索
- **方向感知 top-N 截断**：`_scrape_oneway` 按 DEPARTURE/RETURN 分别排序切 top N，避免单侧空导致配对失败
- **路线过滤器前置**：截断前先按路线时间窗/机场筛，保证截到的是"合规集合的最便宜 N 条"而非"最便宜的 N 条里合规的"
- **回程时间窗字段**：`routes` 表新增 `ret_dep_time_from/to`、`ret_arr_time_from/to` 4 列；过滤器按记录类型分流校验（组合记录两段都查、RETURN 单程用 `ret_*`、机场反向）
- **组合记录继承 `batch_id`**：`_combine_roundtrip_prices` 创建新 `FlightPrice` 时漏传 `batch_id`，导致 Qunar 往返数据被 UI 静默过滤
- **来源映射前缀容错**：`_source_label()` 前缀匹配 `qunar_*` / `ctrip_*`；同时把爬虫内 `qunar_api` / `qunar_mobile` 统一为 `qunar`
- 详见 [feature_log/v1.4.2.md](feature_log/v1.4.2.md)

## [1.2.0] - 2026-03-26

### Added

- **per-route 采集上限**：`max_results` 从全局设置下沉为每路线独立配置
  - `Route` 表新增 `max_results INTEGER NOT NULL DEFAULT 20` 列（`_apply_migrations` 幂等迁移）
  - `RouteWithLatestPrice` / `add_route()` / `get_all_routes()` 全链路透传 `max_results`
  - `PriceMonitorScheduler.scrape_route()` 在每次采集前将 `route.max_results` 写入对应爬虫实例
  - `_scrape_oneway()` 每平台上限改为 `getattr(scraper, "max_results", 20)`，删除旧的类变量 `_PER_PLATFORM_LIMIT` 和 `update_per_platform_limit()` 方法
  - 「添加监控」弹窗新增 **每平台采集上限** 滑块（5~100，步长 5）；删除页头独立的 **⚙️ 采集设置** popover

- **AI 简报自动触发**（`ui/components/ai_brief.py`）：
  - 缓存键从「路线 ID + 今日日期」改为 **12 小时窗口**（`_12h_window_id()`），每半日自动失效重建
  - 新增 `_should_auto_trigger()` 函数：满足以下任一条件时无需手动点击、自动生成简报：
    1. 积累 ≥ 5 批独立采集数据（数据里程碑）
    2. 当前批次最低价相对历史中位数偏离 ≥ 8%（价格异动）
  - 不满足自动触发条件时仍保留「✨ 生成 AI 简报」手动按钮（数据积累阶段）

- **`_compute_price_stats()` 鲁棒性改进**（`price_monitor.py`）：
  - `avg_30d` 由所有历史条目均值改为**各批次最低价的中位数**（`median(_batch_min_prices(history))`），大幅降低偶发特价票对均值的干扰
  - 新增 `batch_count` 字段，供通知逻辑统计有效批次数

### Fixed

- **去哪儿 wbdflightlist API 数据被丢弃**（`qunar_scraper.py`）：
  - **根因**：`wbdflightlist` 一次性返回全部 147 条航班，但 DOM 已渲染约 20 个节点（非零），`search_flights()` 直接走 DOM 路径，API 数据被忽略；滚动也无法触发新 API 请求（虚拟列表）
  - **修复（流程）**：`search_flights()` 在 DOM 解析前优先检测 `wbdflightlist` 捕获，有数据则解析并返回，跳过 DOM 和移动端回退路径
  - **修复（解析器）**：`_parse_api_responses` wbdflightlist 分支原先从记录顶层读 `flightNo`/`airlineName`/`depTime` 等字段（均为 `None`），改为从 `binfo` 子对象读取；中转航班使用 `binfo1`（起飞段）+ `binfo2`（到达段）

- **携程 batchSearch API 数据全部被丢弃**（`ctrip_scraper.py`）：
  - **根因 1（过滤器 bug）**：`_parse_itinerary()` 检测 `seatsLeft is None` 即跳过，但 `batchSearch` 接口从不返回 `seatsLeft`（永远为 `None`）——导致 172 × ~15 条价格条目 100% 被过滤；修复为仅在 `seatsLeft == 0`（明确售罄）时才跳过
  - **根因 2（重复条目）**：原逻辑为每个价格档位（退改签套餐）各创建一条 `FlightPrice`，同一航班产生 7~15 条重复记录；改为每行程仅保留最低 `adultPrice`，每行程输出一条记录
  - **根因 3（舱位代码）**：`batchSearch` 使用 `cabin` 字段（非 `cabinType`）；新增 `@Y-Y`→经济舱、`@C-C`→商务舱 映射；字段查找顺序改为 `cabin` 优先
  - **新增 `max_results` 参数**：`CtripScraper.__init__` 新增 `max_results: int = 20`；API 解析后按价格升序截取前 N 条
  - 修正测试 `test_parse_itinerary_filters_no_seats`：更新断言以反映新的 `seatsLeft=None` 语义（保留而非过滤）
  - **国际航班含税价格**：`adultTax` 字段（国际航班单独列出的机场税/燃油费）未被加入最终价格，导致国际航班价格比携程页面展示值偏低（如 adultPrice=186 + adultTax=144，实际总价应为 330 而非 186）；修复为 `total = adultPrice + (adultTax or 0)`，国内航班 `adultTax=None` 不受影响

### Test

- Ctrip 测试从 15/18 通过 → **18/18 全部通过**
- 全套测试（排除 e2e）从 116 通过 → **134 通过**

## [1.1.0] - 2026-03-25

### Added

- **滚动加载（去哪儿桌面端）**：`QunarScraper._parse_flights()` 在首次获取 `.b-airfly` 元素后自动执行滚动循环，持续滚动直至采集到目标数量或连续两次无新元素后停止（最多 8 次滚动）；每次滚动随机等待 1.0~2.0 秒规避反爬

- **最大采集数量配置**：
  - `Settings.max_results_per_platform`（默认 20，范围 5~100）
  - `QunarScraper.__init__` 新增 `max_results` 参数
  - `PriceMonitorScheduler` 读取配置并传递给 QunarScraper，新增 `update_per_platform_limit()` 方法支持运行时动态调整
  - 仪表板页头新增 **⚙️ 采集设置** popover，包含采集上限滑块（5~100，步长 5），保存到 `session_state` 并即时更新后台调度器及立即采集流程

- **图表视图重设计**：
  - 删除 `st.tabs` 双视图包装，统一为单一视图 `_render_unified_view`
  - 过滤面板改为 3 列内联布局（起飞时间 | 到达时间 | 出发/到达机场 multi-select），移除 `st.expander`
  - 图表类型选择改为 `st.radio` 水平按钮（折线图 / 价格区间），置于过滤面板内
  - 统计指标精简为 4 个核心指标（历史最低/高、最近采集区间、最低价 vs 目标）
  - 单程记录表格新增 **时长** 和 **经停** 列；往返表格新增 **去程时长** 列
  - 飞行时长使用实际 `arrival_date` 避免 +N 天误差；经停数从联程航班号 "/" 计数

- **AI 价格简报**（`src/flightscanner/analyzers/deepseek_analyzer.py` + `ui/components/ai_brief.py`）：
  - 路线卡片展开后显示「✨ 生成 AI 简报」按钮，点击后按需调用 DeepSeek API
  - 输出包含：趋势方向、置信度、关键因素、7日预测、建议操作、告警级别
  - 自动降级逻辑：历史记录 < 7 条 → 规则引擎；无 `DEEPSEEK_API_KEY` → 规则引擎；API 失败（tenacity 3 次重试后）→ 规则引擎
  - 结果按「路线ID + 今日日期」缓存到 `st.session_state`，每日只生成一次；支持手动 🔄 重新生成

### Changed

- `PriceMonitorScheduler._scrape_oneway()` 每平台上限由类变量 `_PER_PLATFORM_LIMIT = 20` 改为实例变量 `self._per_platform_limit`，与配置联动

## [1.0.5] - 2026-03-23

### Added

- **`arrival_date` 字段全链路支持**：修正跨日/多日航班到达时间标注只能显示 `+1` 的限制
  - `FlightInfo` 新增可选字段 `arrival_date: Optional[date]`，表示实际到达日期
  - `flights` 表新增 `arrival_date DATE` 列（`_apply_migrations` 幂等迁移，旧数据库自动补列）
  - 去哪儿爬虫全路径（wwwsearch API / DOM 解析 / 移动端 API / 通用 API 启发式）均计算并写入 `arrival_date`：
    - wwwsearch API：直接读取末段 `arrDate` 字段（可覆盖 +2、+3 等多日场景）
    - 其余路径：依据 `departure_date` + HH:MM 比较估算（+1 精度，作为兜底）
  - `RouteService`：`_find_or_create_flight` 写库时保存 `arrival_date`；`get_route_price_history` 读库时携带 `arrival_date`

### Changed

- **`+N` 到达标记从 `+1` 升级为精确 `+N`**：
  - `charts.py` 新增 `_day_offset_marker()` 函数，优先使用 `arrival_date − departure_date` 计算天数差，无 `arrival_date` 时降级为 HH:MM 字符串比较（最多 `+1`）
  - `_build_dataframe` 的 `arr_time` / `ret_arr_time` 列改用 `_day_offset_marker()`
  - `overview.py` `_fmt_arrival()` 同步升级：新增 `dep_date` / `arrival_date` 可选参数，往返程 caption 行传入完整日期信息

## [1.0.4] - 2026-03-23

### Added

- **最新采集 · 最低10条航班列表**：在每个路线卡片展开后的价格区域新增紧凑表格，汇总所有平台最新批次的前10条最低价记录，显示价格、日期、时间段、航班号、平台来源，取代以往只展示每平台最低价单一指标的局限
  - 单程路线：表格展示起飞→到达时间（含 `+1` 标记）、航班号、平台
  - 往返路线：每行同时展示去程和回程的日期、时间段、航班号
- **跨日到达 `+N` 标记**：当到达时间（HH:MM）早于起飞时间时，自动在到达时间后显示 `+1`，提示次日到达；适用于最低价表格和往返程详情标注行

### Changed

- 往返程最低价详情标注（卡片底部 caption 行）改为展示最新批次中价格最低的航班组合，而非时间戳最新的记录
- `_render_source_price_summary` 内部重构：复用新抽取的 `_collect_latest_batch_records` 辅助函数，消除与 `_render_top10_latest_flights` 之间的重复批次查找逻辑

## [1.0.3] - 2026-03-17

### Fixed

- **最低价计算错误（核心 bug）**：同一次采集会话中，多条记录因 Playwright 异步写入而产生微秒级时间差。旧逻辑以精确 `scraped_at` 时间戳分组，导致只取最后几条记录的最低价（如 ¥1563），而非整批次的最低价（如 ¥303）
  - 新增 `PriceHistory.batch_id` 列（`VARCHAR(100) nullable`）及复合索引 `(route_id, batch_id)` / `(source, batch_id)`
  - `FlightPrice` dataclass 新增 `batch_id: Optional[str]` 字段
  - `scrape_route()` 在存库前为本次采集所有记录统一生成并写入 `batch_id`（格式：`route_{id}_{timestamp}_{hash8}`）
  - `RouteService.get_all_routes()` 子查询由 `MAX(scraped_at)` 精确时间匹配改为 `MAX(batch_id)` 批次匹配，确保取整批最低价
  - `save_price_for_route()` 写入 `batch_id`；`get_route_price_history()` 返回含 `batch_id` 的 FlightPrice
  - `_apply_migrations()` 补充 `batch_id` 迁移语句，兼容已存在的旧数据库

- **平台最新价展示错误**：`_render_source_price_summary`（overview.py）同样使用精确时间戳匹配，与上述 bug 同根同源；改为按 `batch_id` 分组取最低价，保留旧数据无 `batch_id` 时的时间戳兜底逻辑

- **SQLite 并发写入异常**：APScheduler 采集线程与 Streamlit 主线程共享同一 `engine`，`check_same_thread` 默认为 `True` 会触发跨线程写入报错；`init_db()` 新增 `check_same_thread=False` 及 WAL 日志模式（`PRAGMA journal_mode=WAL`），支持读写并发

### Removed

- 趋势图"最新价蓝圈"标注（`latest_markers` 图层）：该标注基于有缺陷的时间戳匹配逻辑生成，且视觉上与折线点重叠造成混淆，一并移除
- `_agg_by_session()` 的 `show_latest_only` 参数及相关代码路径（已无调用方）

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
