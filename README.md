# FlightScanner — AI 驱动的机票价格监控与预测系统

> 定时/实时监控国内外机票价格，结合 AI 大模型分析历史规律，在最佳买点自动推送提醒。

---

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [技术架构](#技术架构)
- [数据库设计](#数据库设计)
- [快速开始](#快速开始)
- [配置说明](#配置说明)

---

## 项目简介

FlightScanner 是一套面向个人用户的**机票价格智能监控系统**。它通过 Playwright 浏览器自动化持续抓取去哪儿、携程等平台的实时票价，将价格历史持久化存储，并借助 AI 大模型（DeepSeek）分析价格走势，在达到"最佳买点"时通过 Email、Telegram、飞书、企业微信等渠道推送提醒。

**解决的核心问题：**
- 机票价格波动频繁，手动比价耗时且易错过低价窗口
- 缺乏基于历史规律的"是否现在买"决策依据
- 多平台信息分散，无统一监控入口

---

## 核心功能

### ✅ 已实现

| 功能 | 说明 |
|------|------|
| 国内/国际单程机票监控 | 去哪儿（Qunar）+ 携程（Ctrip），自动区分国内/跨境页面路由 |
| 往返程监控 | 支持往返总价采集，自动配对去程/回程 |
| 多平台并行采集 | `asyncio.gather` 并发调用多个爬虫，结果合并去重 |
| 定时价格采集 | APScheduler，可按路线设置独立采集间隔（1～24 小时）|
| 价格历史存储 | SQLite，三表结构（flights / routes / price_history），WAL 并发模式 |
| 批次标记（batch_id）| 同一采集会话的所有记录共享 batch_id，确保最低价统计正确 |
| Streamlit 可视化仪表板 | 可展开路线卡片，内嵌 Altair 价格折线图 |
| 机场/时间段过滤 | 按出发/到达机场代码、出发/到达时间段过滤采集结果 |
| Cookie 扫码自动刷新 | `python scripts/qunar_login.py` 弹出浏览器完成登录 |
| 规则型趋势分析 | 基于均值/方差判断涨跌，输出 `PriceTrend` 对象 |
| Email 价格提醒 | SMTP，价格达到目标价或低于均价时触发 |
| Telegram 推送 | Bot API，支持 Markdown 格式消息 |
| 企业微信推送 | 群机器人 Webhook |
| 飞书推送 | 群机器人 Webhook，Interactive Card 富文本格式，支持签名校验 |
| 防骚扰冷却 | 同一路线通知间隔限制，避免频繁推送 |

### 🚧 规划中

| 功能 | 阶段 |
|------|------|
| AI 趋势分析（DeepSeek）| Phase 3 |
| 灵活日期 ±N 天监控 | Phase 3 |
| 节假日/淡旺季感知 | Phase 3 |
| 多用户支持 + 认证 | Phase 4 |
| Docker 一键部署 | Phase 4 |

---

## 技术架构

```
┌─────────────────────────────────────────────────────────┐
│                     用户界面层                           │
│   Streamlit Dashboard  /  (Phase 4: Next.js + FastAPI)  │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                     业务逻辑层                           │
│  RouteService  │  PriceMonitorScheduler  │  Analyzers   │
└────────┬───────────────┬────────────────────────────────┘
         │               │
┌────────▼──────┐  ┌─────▼────────────────────────────────┐
│  数据访问层   │  │            外部集成层                  │
│ SQLAlchemy ORM│  │  Playwright Scrapers  │  AI APIs      │
│ SQLite / PG   │  │  (Qunar / Ctrip)      │  (DeepSeek)   │
└───────────────┘  └──────────────────────────────────────┘
```

### 技术栈

| 层级 | 技术选型 | 版本要求 |
|------|----------|------|
| **UI 框架** | Streamlit | ≥ 1.30 |
| **后端语言** | Python (async/await) | ≥ 3.10 |
| **ORM** | SQLAlchemy | ≥ 2.0 |
| **数据库** | SQLite（WAL 模式）| — |
| **浏览器自动化** | Playwright | ≥ 1.40 |
| **HTTP 客户端** | httpx | ≥ 0.27 |
| **AI 接口** | DeepSeek API（OpenAI 兼容）| — |
| **定时任务** | APScheduler | ≥ 3.10 |
| **数据可视化** | Altair + pandas | ≥ 5.0 / ≥ 2.0 |
| **配置管理** | pydantic-settings | ≥ 2.1 |
| **测试框架** | pytest + pytest-asyncio | ≥ 8.0 |

### 目录结构

```
FlightScanner/
├── src/flightscanner/          # 核心 Python 包
│   ├── interfaces.py           # 所有核心抽象接口（ABC）
│   ├── scrapers/
│   │   ├── qunar_scraper.py    # 去哪儿（Playwright + Cookie）
│   │   └── ctrip_scraper.py   # 携程（Playwright + XHR 拦截）
│   ├── analyzers/
│   │   └── rule_based_analyzer.py
│   ├── core/services/
│   │   └── route_service.py
│   ├── models/database.py      # SQLAlchemy ORM 模型
│   ├── notifiers/              # 通知渠道
│   │   ├── email_notifier.py
│   │   ├── telegram_notifier.py
│   │   ├── wecom_notifier.py
│   │   └── feishu_notifier.py
│   ├── scheduler/
│   │   └── price_monitor.py    # APScheduler + 批次采集逻辑
│   └── utils/config.py         # pydantic-settings 配置
├── ui/                         # Streamlit 前端
│   ├── app.py
│   ├── utils/db.py
│   └── components/
│       ├── overview.py         # 路线卡片
│       ├── charts.py           # Altair 图表
│       └── sidebar.py          # 添加路线表单
├── scripts/
│   ├── qunar_login.py          # 扫码刷新去哪儿 Cookie
│   └── verify_notify.py        # 测试通知渠道连通性
├── tests/
├── feature_log/                # 版本更新记录
├── qunar_cookies.json          # 去哪儿登录 Cookie（本地，勿提交）
├── ctrip_cookies.json          # 携程登录 Cookie（本地，勿提交）
├── .env                        # 环境变量（本地，勿提交）
└── CLAUDE.md                   # AI 开发上下文配置
```

---

## 数据库设计

### ER 关系

```
routes (1) ──── (N) price_history (N) ──── (1) flights
```

### 核心表结构

#### `routes` — 监控路线配置

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| origin | VARCHAR(50) | 出发城市 |
| destination | VARCHAR(50) | 到达城市 |
| target_date | DATE | 目标出行日期 |
| return_date | DATE | 返程日期（往返程，可空）|
| trip_type | TEXT | `oneway` / `roundtrip` |
| target_price | NUMERIC(10,2) | 目标心理价位 |
| scrape_interval | INTEGER | 采集间隔（小时，默认 6）|
| is_active | INTEGER | 1=监控中 / 0=已暂停 |
| is_international | INTEGER | 1=国际/跨境 / 0=国内 |
| dep_airport_code | TEXT | 出发机场过滤（IATA，可空）|
| arr_airport_code | TEXT | 到达机场过滤（IATA，可空）|
| dep_time_from/to | TEXT | 出发时间段过滤（HH:MM，可空）|
| arr_time_from/to | TEXT | 到达时间段过滤（HH:MM，可空）|
| last_notified_at | DATETIME | 最近通知时间（防骚扰）|
| created_at | DATETIME | 创建时间戳（UTC）|

#### `flights` — 航班基础信息

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| flight_no | VARCHAR(20) | 航班号（CA1234）|
| airline | VARCHAR(100) | 航空公司 |
| departure_city / arrival_city | VARCHAR(50) | 城市 |
| departure_airport / arrival_airport | TEXT | 机场全名 |
| departure_airport_code / arrival_airport_code | TEXT | IATA 代码 |
| departure_time / arrival_time | VARCHAR(10) | HH:MM 时刻 |
| departure_date | DATE | 航班日期 |
| direction | VARCHAR(20) | `departure` / `return` |

唯一约束：`(flight_no, departure_date, departure_city, arrival_city, direction)`

#### `price_history` — 价格快照（核心时序数据）

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| flight_id | INTEGER FK | → flights.id（去程）|
| return_flight_id | INTEGER FK | → flights.id（回程，往返程）|
| route_id | INTEGER FK | → routes.id |
| price | NUMERIC(10,2) | 抓取价格 |
| currency | VARCHAR(10) | 默认 CNY |
| seat_class | VARCHAR(50) | 经济舱/商务舱等 |
| available_seats | INTEGER | 剩余座位（可空）|
| source | VARCHAR(50) | `qunar` / `ctrip` 等 |
| scraped_at | DATETIME | 抓取时间戳（UTC）|
| batch_id | VARCHAR(100) | 采集批次 ID，同一次会话共享，用于正确计算最低价 |

---

## 快速开始

### 1. 克隆并安装依赖

```bash
git clone <repo-url> && cd FlightScanner

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 安装项目及开发依赖
pip install -e ".[dev]"

# 安装 Playwright 浏览器内核
playwright install chromium
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填写以下内容：
# - DATABASE_URL（默认 sqlite:///flightscanner.db，可直接使用）
# - 至少一个通知渠道（Email / Telegram / 飞书 / 企业微信）
```

### 3. 获取去哪儿 Cookie（首次使用）

```bash
# 弹出浏览器窗口，扫码或账号密码登录后自动保存 Cookie
python scripts/qunar_login.py
```

> Cookie 保存在 `qunar_cookies.json`，有效期约 7 天，过期后重新运行此命令。

### 4. 启动仪表板

```bash
# 确保在项目根目录下运行
streamlit run ui/app.py
```

浏览器访问 `http://localhost:8501`，在左侧边栏添加监控路线即可。

### 5. 运行测试

```bash
# 全部单元测试（跳过需要真实浏览器的 e2e 测试）
pytest tests/ -q --ignore=tests/test_e2e.py -k "not Ctrip"
```

---

## 配置说明

复制 `.env.example` 为 `.env` 后按需填写：

```ini
# ── 数据库 ─────────────────────────────────────────────────────────
DATABASE_URL=sqlite:///flightscanner.db

# ── 爬虫设置 ───────────────────────────────────────────────────────
SCRAPER_TYPE=qunar,ctrip      # 启用的爬虫，逗号分隔（qunar / ctrip）
SCRAPER_HEADLESS=true         # false = 显示浏览器窗口（调试用）
SCRAPER_TIMEOUT=30000         # 页面等待超时（毫秒）
SCRAPER_RETRY_COUNT=3

# ── AI 趋势分析（可选）─────────────────────────────────────────────
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# ── 通知渠道（至少配置一个）────────────────────────────────────────

# Email（SMTP）
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your-app-password  # Gmail 需使用"应用专用密码"

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-xxx
TELEGRAM_CHAT_ID=your-chat-id    # 通过 @userinfobot 获取

# 飞书群机器人
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
FEISHU_WEBHOOK_SECRET=           # 可选，飞书安全设置中开启时填写

# 企业微信群机器人
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

### Cookie 文件

Cookie 优先从文件读取，也可通过环境变量传入（JSON 数组格式）：

| 文件 | 平台 | 刷新方式 |
|------|------|---------|
| `qunar_cookies.json` | 去哪儿 | `python scripts/qunar_login.py` |
| `ctrip_cookies.json` | 携程 | 从浏览器 DevTools → Network → 复制 Cookie 请求头 |
