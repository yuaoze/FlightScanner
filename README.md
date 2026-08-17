<p align="center">
  <img src="logo.png" alt="FlightScanner Logo" width="520" />
</p>

# FlightScanner — AI 驱动的机票价格监控与预测系统

> 定时/实时监控国内外机票价格，结合 AI 分析历史波动，在更合适的买点自动推送提醒。

**当前版本：v2.1.0**（详见 [feature_log/v2.1.0.md](feature_log/v2.1.0.md)）

---

## 项目简介

FlightScanner 是一套面向个人用户的机票价格智能监控系统。它通过 Playwright 抓取多平台票价，使用 SQLite 持久化价格历史，并结合规则分析与 AI 大模型（DeepSeek）输出趋势判断，在价格达到目标区间时通过 Email、Telegram、飞书、企业微信等渠道发送提醒。

适用场景：

- 长期跟踪某条航线或某个固定航班的价格变化
- 借助历史数据判断“现在买”还是“继续等”
- 从周末雷达中快速发现更划算的短途出行机会

---

## 核心功能

- **路线监控 + 精准航班监控**：支持按航线监控，也支持按航班号固定监控去程/回程与舱位
- **多平台采集与定时调度**：基于 Playwright + APScheduler，支持去哪儿、携程、同程旅行；同程覆盖国内、港澳台和国际单程/往返，按路线独立间隔自动采集
- **价格历史与 AI 决策**：保存历史票价，结合规则分析与 DeepSeek 输出趋势判断与建议操作
- **现代化 Web 控制台**：React + FastAPI 前后端分离，支持监控总览、路线详情、提醒记录、设置中心、周末雷达
- **多渠道通知**：支持 Email、Telegram、飞书、企业微信等提醒方式

---

## 技术架构

```text
React + Vite 前端
        │
        ▼
FastAPI API / 调度入口
        │
        ├── PriceMonitorScheduler（定时采集 / 通知 / AI 预测）
        ├── Playwright Scrapers（Qunar / Ctrip / Tongcheng）
        ├── Analyzers（规则分析 / DeepSeek）
        └── Notifiers（Email / Telegram / 飞书 / 企业微信）
        │
        ▼
SQLite + SQLAlchemy
```

核心技术栈：

- **前端**：React、TypeScript、Vite、TailwindCSS、Recharts、React Query
- **后端**：FastAPI、SQLAlchemy、APScheduler、Pydantic Settings
- **采集**：Playwright、httpx
- **分析与通知**：DeepSeek（OpenAI 兼容）、SMTP、Telegram Bot、Webhook

---

## 快速开始

### 1. 安装依赖

```bash
git clone <repo-url> && cd FlightScanner

python3 -m venv venv
source venv/bin/activate

pip install -e ".[dev]"
playwright install chromium
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少建议配置：

- `DATABASE_URL`（默认 sqlite 即可）
- 至少一个通知渠道
- `DEEPSEEK_API_KEY`（启用 AI 分析时填写）

### 3. 获取 Cookie

```bash
python scripts/qunar_login.py
python scripts/ctrip_login.py
python scripts/tongcheng_login.py
```

也可以先启动后端后，在 `/settings` 页通过 Cookie 管理卡片扫码刷新。
同程旅行通过其官方微信 OAuth 登录页刷新 Cookie，请使用微信“扫一扫”
（不是同程旅行 App）；同程默认也可匿名采集。

### 4. 启动后端 API

```bash
python -m uvicorn flightscanner.api.main:app --host 127.0.0.1 --port 8000 --reload
```

启动后会同时：

- 提供 `http://127.0.0.1:8000/api/*`
- 启动后台 `PriceMonitorScheduler`
- 自动调度所有启用中的监控路线
- 提供在线文档 `http://127.0.0.1:8000/docs`

### 5. 启动前端

```bash
cd frontend
npm install
npm run dev
```

默认访问：`http://localhost:5173`

### 6. 运行测试

```bash
FLIGHTSCANNER_DISABLE_SCHEDULER=1 pytest tests/ -q --ignore=tests/test_e2e.py -k "not Ctrip"
```

如需单独验证去哪儿爬虫：

```bash
pytest tests/test_qunar_scraper.py -q
```

---

## 配置说明

### `.env` 关键配置

```ini
DATABASE_URL=sqlite:///flightscanner.db

SCRAPER_TYPE=qunar,ctrip,tongcheng
SCRAPER_HEADLESS=true
SCRAPER_TIMEOUT=30000
SCRAPER_RETRY_COUNT=3

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

FEISHU_WEBHOOK_URL=
FEISHU_WEBHOOK_SECRET=

WECOM_WEBHOOK_URL=
```

说明：

- `python-dotenv` 不会自动忽略值后面的内联 `# 注释`，注释建议单独占一行
- 通知渠道无需全部填写，但至少配置一个才有告警意义
- `SCRAPER_TYPE` 支持 `qunar`、`ctrip`、`tongcheng` 或逗号组合

### Cookie 文件

- `qunar_cookies.json`：去哪儿登录 Cookie
- `ctrip_cookies.json`：携程登录 Cookie
- `tongcheng_cookies.json`：同程旅行 Cookie（可选；默认可匿名采集，风控时建议通过官方微信 OAuth 扫码刷新）

推荐通过扫码脚本生成；同程脚本展示的是官方微信登录二维码，请使用微信“扫一扫”：

```bash
python scripts/qunar_login.py
python scripts/ctrip_login.py
python scripts/tongcheng_login.py
```

Cookie 过期后重新刷新即可。

### 同程国际机票口径

- 国内航线走同程国内机票页；港澳台和海外航线自动切换到官方国际机票页。
- 国际列表只记录成人经济舱的公开含税价，不把未税票面价、会员价或券后价混入趋势。
- 国际往返会读取官方 RT 产品详情中的真实去程、回程和整套含税价，不使用两个独立单程最低价相加。
- “立即采集”会显示各平台的结果数量、告警和错误；同程风控、登录失效或接口变化不会再伪装成正常的 0 条结果。
