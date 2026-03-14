# CLAUDE.md — FlightScanner AI 开发配置

> 本文件是 Claude Code 与本项目协作时的**系统上下文**。每次开发会话开始前自动加载。

---

## 项目概述

**FlightScanner** 是一套基于 Python 的 AI 驱动机票价格监控系统。核心链路：

```
Playwright 爬虫 → SQLite 价格历史 → DeepSeek AI 趋势分析 → 多渠道推送提醒
                                  ↕
                        Streamlit 可视化仪表板
```

当前阶段：**Phase 1 MVP 已完成，Phase 2 开发中。**

---

## 技术栈规范

### 运行环境

| 项目 | 版本 |
|------|------|
| Python | ≥ 3.10（使用 `match`、`TypeAlias`、`ParamSpec` 等新特性可）|
| 虚拟环境 | `venv/`（项目根，勿提交）|
| 包管理 | `pip install -e ".[dev]"`，依赖定义在 `pyproject.toml` |
| 测试运行 | `pytest tests/ -q`（QunarScraper 测试：31 个，应全部通过）|

### 核心依赖

```
playwright ≥ 1.40    # 浏览器自动化（Chromium）
sqlalchemy ≥ 2.0     # ORM，使用 declarative_base()
streamlit ≥ 1.30     # Web UI（无 JS，纯 Python）
altair ≥ 5.0         # 数据可视化图表
apscheduler ≥ 3.10   # 定时任务
openai ≥ 1.12        # DeepSeek API（OpenAI 兼容格式）
pydantic-settings ≥ 2.1  # 配置管理（.env）
httpx ≥ 0.27         # 异步 HTTP 客户端
tenacity ≥ 8.2       # 重试机制装饰器
```

### 项目结构（重要路径）

```
src/flightscanner/
├── interfaces.py          # ⭐ 所有抽象基类（FlightScraper / DataRepository /
│                          #    PriceAnalyzer / Notifier / FlightPrice / SearchParams）
├── scrapers/
│   ├── qunar_scraper.py   # ⭐ 主爬虫（Playwright + Cookie，已完善）
│   └── ctrip_scraper.py   # 携程爬虫（开发中，有 3 个测试失败）
├── analyzers/
│   └── rule_based_analyzer.py  # 规则型分析器（均值/方差）
├── core/services/
│   └── route_service.py   # 路线 CRUD + 价格历史查询
├── models/database.py     # SQLAlchemy 模型：Flight / Route / PriceHistory
├── repositories/
│   └── sqlalchemy_repository.py
├── notifiers/
│   └── email_notifier.py  # SMTP 邮件通知
├── scheduler/
│   └── price_monitor.py   # APScheduler 定时采集
└── utils/config.py        # pydantic-settings Settings 类

ui/
├── app.py                 # Streamlit 入口，trigger_immediate_scrape()
└── components/
    ├── overview.py        # render_overview_cards() + render_route_list()
    ├── charts.py          # render_price_trend_chart()
    └── sidebar.py         # render_sidebar()

scripts/
└── qunar_login.py         # 调用 QunarScraper.refresh_cookies_via_login()
```

---

## 代码风格规范

### 通用原则

1. **行长度**：100 字符（`pyproject.toml` 中 `ruff` 配置）
2. **格式化**：`black`，目标版本 py310/py311/py312
3. **类型注解**：**所有公开函数和方法必须有完整类型注解**（`mypy` 严格模式）
4. **字符串**：优先双引号（`black` 默认）
5. **导入顺序**：`isort`（profile = black）：标准库 → 三方库 → 本地包

### 注释规范

```python
# ── 段落分隔注释（用于长函数中的逻辑分段）─────────────────────────────
```

- 模块/类/方法：使用 Google 风格 docstring
- 复杂逻辑内联注释用**中文**（面向中文用户的项目）
- 简单赋值不写注释，注释只解释"为什么"而非"做了什么"

```python
def search_flights(self, params: SearchParams) -> List[FlightPrice]:
    """Search for flights on Qunar.

    Args:
        params: Search parameters including cities and dates.

    Returns:
        List of flight prices found.

    Raises:
        NetworkTimeoutError: When network request times out.
        ParseError: When page parsing fails.
        AntiCrawlerDetectedError: When anti-crawler mechanism blocks access.
    """
```

### 错误处理

1. **自定义异常层次**（定义在 `interfaces.py`）：
   - `ScraperError` → `NetworkTimeoutError` / `ParseError` / `AntiCrawlerDetectedError` / `LoginRequiredError`
2. **爬虫方法**：只 `raise` 上述自定义异常，不暴露 Playwright 内部异常
3. **日志级别**：
   - `logger.debug()`：调试用，原始响应体、DOM 内容
   - `logger.info()`：正常流程节点（导航、成功采集、Cookie 注入）
   - `logger.warning()`：可恢复异常（0 条结果、Cookie 失效提示）
   - `logger.error()`：不可恢复错误（需人工干预）
4. **绝不**在 `except` 块中静默吞掉异常（至少 `logger.warning`）

### 异步规范

- 所有 I/O 操作（爬虫、DB、HTTP）使用 `async/await`
- **不在异步函数中调用 `asyncio.run()`**，会导致嵌套事件循环错误
- Playwright 实例通过 `async with async_playwright() as p:` 或 `await async_playwright().start()` 管理
- 定时任务使用 APScheduler，在 Streamlit 中触发采集时用 `asyncio.run()`（Streamlit 是同步框架）

---

## 核心业务逻辑注意事项

### 爬虫 — 反爬策略

#### Qunar（去哪儿）

**关键机制：Bella 指纹令牌**
- `wbdflightlist` POST 请求的 `Bella` 参数由页面 JS 基于 Canvas/WebGL 计算
- Playwright 控制的浏览器**无法通过 Bella 验证**（服务端识别自动化工具）
- **唯一可行方案**：注入完整的已登录 Cookie，使服务端信任该请求

**Cookie 注入流程：**
```python
# QunarScraper.__init__ 自动加载
self.cookies = self.load_cookies_from_file("qunar_cookies.json")
# _ensure_browser() 中注入
await self._context.add_cookies(self.cookies)
```

**Cookie 格式支持（`load_cookies_from_file`）：**
- 格式一：JSON 数组（`[{"name": "QN1", "value": "...", "domain": ".qunar.com"}]`）
- 格式二：原始字符串（`QN1=xxx; QN44=yyy; ...`，从 DevTools Network 请求头复制）
- `Set-Cookie` 响应头格式的字符串也可解析，但会产生无害的额外键值对

**Cookie 失效处理：**
- `search_flights()` 返回 0 条结果时，自动调用 `_maybe_refresh_and_retry()`
- 交互模式（`sys.stdin.isatty()`）下弹出确认提示，调用 `refresh_cookies_via_login()`
- `refresh_cookies_via_login()` 打开**非无头浏览器**，等待 `QN44`/`quinn` Cookie 出现

**两阶段响应（移动端 touchInnerList）：**
- 首次响应：`{"ret": true, "code": 0, "data": ""}` — 搜索中，data 为空字符串
- 后续响应：`{"ret": true, "code": 0, "data": "<JSON 字符串>"}` — data 需要二次 `json.loads()`
- 使用 `page.route("**/touchInnerList**", handler)` 拦截（`on_response` 无法读取 body）
- 轮询等待：`for _ in range(60): await asyncio.sleep(0.5)` — 最多等 30 秒

**数据流向：**
```
page.goto(search_url)
    → .b-airfly DOM 元素出现（桌面端首选）
    → _parse_flights(page) / _parse_api_responses(captured)
    → 若 0 条：_search_via_mobile_api(params)（需 Cookie）
    → 若仍 0 条：_maybe_refresh_and_retry()（交互模式）
```

#### Ctrip（携程）— 注意事项

- 当前爬虫实现有 3 个测试失败，**不可在生产中依赖**
- 携程反爬比去哪儿更严，优先考虑：
  1. 找官方数据 API（`openapi.ctrip.com`）
  2. 使用 `camoufox` 替代标准 Playwright

#### 通用采集规范

- 每次采集前 `await asyncio.sleep(random.uniform(2, 5))`（避免频率过高封号）
- 所有导航使用 `wait_until="domcontentloaded"`（`networkidle` 等待时间过长）
- Playwright 浏览器实例**复用**（`_ensure_browser()` 仅在首次调用时初始化），不要每次采集都创建新实例
- User-Agent 固定为桌面 Chrome（见 `_ensure_browser()` 中的 `user_agent` 参数）

### 定时任务 — 性能优化

- APScheduler 使用 `AsyncIOScheduler`，与 Playwright 异步循环兼容
- 每条路线独立 `IntervalTrigger`（`hours=route.scrape_interval`）
- **不要**所有路线使用同一个触发时间（会导致并发采集 → 浏览器内存峰值）
- `PriceMonitorScheduler` 持有单个 `QunarScraper` 实例，跨路线复用
- Streamlit 中通过 `asyncio.run(monitor.scrape_route(route))` 触发立即采集（阻塞主线程，有进度条）

### 数据库操作规范

- **始终使用上下文管理器**：`with get_session() as session:`
- `RouteService` 负责所有业务查询，不要在 UI 层直接操作 ORM 模型
- `PriceHistory` 写入前通过 `uix_flight_unique` 约束去重 `Flight` 记录
- 时间戳统一存储 UTC（`datetime.now(timezone.utc)`），UI 层展示时保持 UTC（用户知晓）
- SQLite 并发写入限制：多路线同时采集时使用 `check_same_thread=False` + 连接池配置

### AI 分析器（DeepSeek）— 开发注意

- `DEEPSEEK_API_KEY` 通过 `pydantic-settings` 从 `.env` 读取，**不得硬编码**
- API 调用使用 `openai` 库（`base_url=settings.deepseek_base_url`）
- 要求 AI 输出 JSON：`response_format={"type": "json_object"}`
- 调用失败时**必须降级**到 `RuleBasedAnalyzer`（`tenacity` `@retry` 装饰器 + except 降级）
- 仅在有 ≥ 7 条历史记录时调用 AI（数据太少则直接用规则）
- Prompt 中包含：价格序列（最近 30 条）、目标出行日期、距今天数、节假日信息

### Streamlit UI 规范

- **每次交互后调用 `st.rerun()`** 刷新状态（路线操作、价格采集等）
- 使用 `st.session_state[f"key_{route.id}"]` 跨次渲染传递触发信号
- `st.expander(label, expanded=False)` 作为路线卡片，label 用 `**粗体**` 和全角空格 `　` 排版
- `st.popover()` 用于采集间隔设置（比 expander 更轻量）
- `st.altair_chart(chart, use_container_width=True)` — 始终 use_container_width，卡片内图表不指定固定宽度

---

## 测试规范

### 运行方式

```bash
# 仅 Qunar 爬虫测试（31 个，需 ~2min，会启动真实浏览器）
pytest tests/test_qunar_scraper.py -q

# 排除已知失败的 e2e 和 ctrip 测试
pytest tests/ -q --ignore=tests/test_e2e.py -k "not Ctrip"
```

### 已知失败（预期失败，暂不处理）

| 测试 | 原因 |
|------|------|
| `test_e2e.py::test_complete_workflow_with_alert` | `predict_trend` 返回 `up` 而非 `down`，规则分析器 bug |
| `test_scraper.py::TestCtripScraper::*` | Ctrip 爬虫实现不完整 |

### Mock 模式

- Playwright 测试使用 `pytest-mock`，对 `page.goto()` / `page.wait_for_selector()` 打桩
- `AsyncMock` 用于所有 `async def` 方法的 mock
- 不要在单元测试中发起真实网络请求（集成测试除外）

---

## 敏感文件说明

| 文件 | 内容 | 处理方式 |
|------|------|----------|
| `qunar_cookies.json` | 去哪儿登录 Cookie | 已加入 `.gitignore`，**勿提交** |
| `.env` | API Key、密码等 | 已加入 `.gitignore`，**勿提交** |
| `flightscanner.db` | 本地 SQLite 数据库 | 已加入 `.gitignore` |
| `qunar_cookies.json.example` | Cookie 格式示例 | 可提交，无实际凭据 |
| `.env.example` | 环境变量模板 | 可提交，值留空或用占位符 |

---

## 常见开发任务速查

### 添加新爬虫平台

1. 在 `src/flightscanner/scrapers/` 新建 `xxx_scraper.py`
2. 继承 `FlightScraper` ABC，实现 `search_flights()` 和 `close()`
3. 返回 `List[FlightPrice]`，`source` 字段填平台名（如 `"trip"`)
4. 在 `scheduler/price_monitor.py` 中注册到 scraper 列表

### 添加新通知渠道

1. 在 `src/flightscanner/notifiers/` 新建 `xxx_notifier.py`
2. 继承 `Notifier` ABC，实现 `send_alert()`
3. 在 `utils/config.py` 中添加对应配置字段
4. 在 `PriceMonitorScheduler` 中按配置启用

### 新增数据库字段

1. 修改 `models/database.py` 中对应 ORM 类
2. 写 Alembic 迁移脚本（Phase 4 前可直接删除 `flightscanner.db` 重建）
3. 更新 `RouteService` 相关查询和 `RouteWithLatestPrice` 数据类
4. 更新 Streamlit 中相关展示逻辑

### 调试采集问题

```bash
# 快速调试脚本（非无头，可看到浏览器）
python scripts/debug_scrape.py

# 刷新 Cookie
python scripts/qunar_login.py

# 查看采集日志（DEBUG 级别）
PYTHONUNBUFFERED=1 python scripts/debug_scrape.py 2>&1 | grep -E "\[移动端\]|INFO|WARNING"
```
