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

FlightScanner 是一套面向个人用户的**机票价格智能监控系统**。它通过 Playwright 浏览器自动化持续抓取去哪儿、携程等平台的实时票价，将价格历史持久化存储，并借助 AI 大模型（DeepSeek）分析价格走势，在达到"最佳买点"时通过 Email、Telegram 等渠道推送提醒。

**解决的核心问题：**
- 机票价格波动频繁，手动比价耗时且易错过低价窗口
- 缺乏基于历史规律的"是否现在买"决策依据
- 多平台信息分散，无统一监控入口

---

## 核心功能

### ✅ 已实现（Phase 1 MVP）

| 功能 | 说明 |
|------|------|
| 国内单程机票监控 | 支持去哪儿网（Qunar），Cookie 自动刷新 |
| 定时价格采集 | APScheduler，可按路线设置独立采集间隔（1~24小时）|
| 价格历史存储 | SQLite，三表结构（flights / routes / price_history）|
| Streamlit 可视化仪表板 | 可展开路线卡片，内嵌 Altair 折线图 |
| Email 价格提醒 | SMTP，当最新价格 ≤ 目标价时触发 |
| Cookie 扫码自动刷新 | `python scripts/qunar_login.py` 弹出浏览器完成登录 |
| 规则型趋势分析 | 基于均值/方差判断涨跌，输出 `PriceTrend` 对象 |

### 🚧 规划中（Phase 2~4）

| 功能 | 阶段 |
|------|------|
| 携程（Ctrip）scraper 完善 | Phase 2 |
| 往返程监控 | Phase 2 |
| 灵活日期 ±N 天监控 | Phase 2 |
| Telegram Bot 推送 | Phase 2 |
| AI 趋势分析（DeepSeek）| Phase 3 |
| 节假日/淡旺季感知 | Phase 3 |
| "建议购买 / 建议观望"AI简报 | Phase 3 |
| 国际航班支持 | Phase 3 |
| 多用户支持 + 认证 | Phase 4 |
| FastAPI REST 后端 | Phase 4 |
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
| **数据库（开发）** | SQLite + aiosqlite | — |
| **数据库（生产）** | PostgreSQL | ≥ 15 |
| **浏览器自动化** | Playwright | ≥ 1.40 |
| **HTTP 客户端** | httpx | ≥ 0.27 |
| **AI 接口** | DeepSeek API（OpenAI 兼容）| — |
| **定时任务** | APScheduler | ≥ 3.10 |
| **数据可视化** | Altair + pandas | ≥ 5.0 / ≥ 2.0 |
| **CLI** | Click | ≥ 8.1 |
| **配置管理** | pydantic-settings | ≥ 2.1 |
| **测试框架** | pytest + pytest-asyncio | ≥ 8.0 |

### 目录结构

```
FlightScanner/
├── src/flightscanner/          # 核心 Python 包
│   ├── interfaces.py           # 所有核心抽象接口（ABC）
│   ├── scrapers/               # 各平台爬虫实现
│   │   ├── qunar_scraper.py    # 去哪儿（Playwright + Cookie）
│   │   └── ctrip_scraper.py   # 携程（开发中）
│   ├── analyzers/              # 价格分析器
│   │   └── rule_based_analyzer.py
│   ├── core/services/          # 业务服务层
│   │   └── route_service.py
│   ├── models/database.py      # SQLAlchemy ORM 模型
│   ├── repositories/           # 数据访问层
│   ├── notifiers/              # 通知发送器
│   │   └── email_notifier.py
│   ├── scheduler/              # 定时采集调度
│   │   └── price_monitor.py
│   └── utils/config.py         # pydantic-settings 配置
├── ui/                         # Streamlit 前端
│   ├── app.py
│   └── components/
│       ├── overview.py         # 路线卡片（可展开）
│       ├── charts.py           # Altair 图表
│       └── sidebar.py          # 添加路线表单
├── scripts/
│   └── qunar_login.py          # 扫码刷新 Cookie
├── tests/
├── qunar_cookies.json          # 登录 Cookie（本地，勿提交）
├── CLAUDE.md                   # AI 开发上下文配置
└── ROADMAP.md                  # 迭代计划
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
| target_price | NUMERIC(10,2) | 目标心理价位 |
| scrape_interval | INTEGER | 采集间隔（小时，默认 6）|
| is_active | INTEGER | 1=监控中 / 0=已暂停 |
| created_at / updated_at | DATETIME | 时间戳 |

#### `flights` — 航班基础信息

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| flight_no | VARCHAR(20) | 航班号（CA1234）|
| airline | VARCHAR(100) | 航空公司 |
| departure_city / arrival_city | VARCHAR(50) | 城市 |
| departure_time / arrival_time | VARCHAR(10) | HH:MM 时刻 |
| departure_date | DATE | 航班日期 |
| direction | VARCHAR(20) | departure / return |

唯一约束：`(flight_no, departure_date, departure_city, arrival_city, direction)`

#### `price_history` — 价格快照（核心时序数据）

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| flight_id | INTEGER FK | → flights.id |
| route_id | INTEGER FK | → routes.id |
| price | NUMERIC(10,2) | 抓取价格 |
| currency | VARCHAR(10) | 默认 CNY |
| seat_class | VARCHAR(50) | 经济舱/商务舱等 |
| available_seats | INTEGER | 剩余座位（可空）|
| source | VARCHAR(50) | qunar / ctrip 等 |
| scraped_at | DATETIME | 抓取时间戳（UTC）|

---

## 快速开始

```bash
# 1. 安装依赖
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
playwright install chromium

# 2. 配置环境变量
cp .env.example .env   # 填写 API Key 和 Email 配置

# 3. 首次登录去哪儿（获取 Cookie）
python scripts/qunar_login.py

# 4. 启动仪表板
streamlit run ui/app.py

# 5. 运行测试
pytest tests/test_qunar_scraper.py -q
```

---

## 配置说明

`.env` 关键配置项：

```ini
DATABASE_URL=sqlite:///flightscanner.db

# AI 接口（DeepSeek，OpenAI 兼容）
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# Email 通知
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USERNAME=your@gmail.com
EMAIL_PASSWORD=app-password
EMAIL_RECIPIENT=notify@example.com

# 采集器
SCRAPER_HEADLESS=true
SCRAPER_TIMEOUT=60000
```
