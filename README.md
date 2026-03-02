# FlightScanner

✈️ 航班价格监控与分析系统 - 基于LLM的智能机票价格追踪工具

## 项目简介

FlightScanner 是一个模块化、易于扩展的个人航班价格监控系统。它能够：

- 🕷️ **自动采集**：定时爬取携程等平台的航班价格数据
- 📊 **历史追踪**：存储价格历史记录，分析价格走势
- 🤖 **智能分析**：利用 LLM 分析价格趋势，预测最佳购票时机
- 🔔 **价格提醒**：当价格低于阈值时自动发送通知

## 技术栈

| 类别 | 技术选型 | 说明 |
|------|---------|------|
| **采集框架** | Playwright | 支持动态网页，反爬能力强，异步高效 |
| **数据库** | SQLite + SQLAlchemy | 轻量级存储，ORM支持，时序数据友好 |
| **Web UI** | Streamlit + Altair | 快速构建数据应用，交互式可视化 |
| **任务调度** | APScheduler | 定时任务调度，后台自动化监控 |
| **测试框架** | pytest + pytest-asyncio | TDD驱动开发，完整的测试覆盖 |
| **大模型** | DeepSeek API | 智能价格分析，趋势预测，性价比高 |
| **配置管理** | Pydantic Settings | 类型安全的环境变量管理 |
| **异步框架** | asyncio + httpx | 高并发，异步HTTP请求 |

## 项目结构

```
FlightScanner/
├── src/flightscanner/
│   ├── __init__.py              # 包入口
│   ├── interfaces.py            # 抽象基类定义
│   ├── models/                  # 数据模型
│   │   ├── __init__.py
│   │   └── database.py          # SQLAlchemy模型 (Flight, PriceHistory, Route)
│   ├── core/                    # 业务逻辑层 (NEW in v1.0)
│   │   ├── __init__.py
│   │   └── services/
│   │       ├── __init__.py
│   │       └── route_service.py # 航线管理服务
│   ├── scrapers/                # 爬虫模块
│   │   ├── __init__.py
│   │   └── ctrip_scraper.py     # 携程爬虫实现
│   ├── repositories/            # 数据访问层
│   │   ├── __init__.py
│   │   └── sqlalchemy_repo.py   # SQLAlchemy仓库实现
│   ├── analyzers/               # 价格分析模块
│   │   ├── __init__.py
│   │   └── rule_based_analyzer.py
│   ├── notifiers/               # 通知模块
│   │   ├── __init__.py
│   │   └── email_notifier.py    # 邮件通知实现
│   ├── scheduler/               # 调度模块 (NEW in v1.0)
│   │   ├── __init__.py
│   │   └── price_monitor.py     # 后台价格监控
│   ├── utils/                   # 工具模块
│   │   ├── __init__.py
│   │   └── config.py            # 配置管理
│   └── cli.py                   # 命令行接口
├── ui/                          # Web UI模块 (NEW in v1.0)
│   ├── __init__.py
│   ├── app.py                   # Streamlit主应用
│   ├── components/
│   │   ├── __init__.py
│   │   ├── sidebar.py           # 侧边栏组件
│   │   ├── overview.py          # 概览组件
│   │   └── charts.py            # 图表组件
│   └── utils/
│       ├── __init__.py
│       └── db.py                # 数据库会话管理
├── tests/                       # 测试文件
│   ├── __init__.py
│   ├── conftest.py              # pytest配置
│   └── test_integration.py      # 集成测试
├── scripts/                     # 验证脚本
│   ├── verify_db.py             # 数据库验证
│   ├── verify_scraper.py        # 爬虫验证
│   ├── verify_llm.py            # LLM验证
│   └── verify_notify.py         # 通知验证
├── config/                      # 配置文件目录
├── main.py                      # 调度器入口 (NEW in v1.0)
├── .env.example                 # 环境变量模板
├── pyproject.toml               # 项目配置
└── README.md                    # 项目文档
```

## 快速开始

### 0. 数据库迁移（如果从 v1.0.0 升级）

如果你已经安装了 v1.0.0 并遇到 `no such column: routes.scrape_interval` 错误，请运行迁移脚本:

```bash
python scripts/migrate_add_scrape_interval.py
```

详细迁移指南请查看 [docs/MIGRATION.md](docs/MIGRATION.md)

### 1. 环境准备

```bash
# 克隆项目
git clone https://github.com/yourusername/FlightScanner.git
cd FlightScanner

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# 安装依赖
pip install -e ".[dev]"

# 安装 Playwright 浏览器
playwright install chromium
```

### 2. 配置环境变量

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 文件，填入你的配置
vim .env
```

主要配置项：

```bash
# DeepSeek API (必需) - 兼容 OpenAI API 格式
DEEPSEEK_API_KEY=sk-your-deepseek-api-key-here
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 数据库 (默认使用SQLite)
DATABASE_URL=sqlite:///flightscanner.db

# 邮件通知 (可选)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASSWORD=your-password

# Telegram通知 (可选)
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id

# 爬虫配置
SCRAPER_HEADLESS=true
SCRAPER_TIMEOUT=30000
```

## 使用方式

FlightScanner v1.0 提供两种使用方式：

### 方式一：Web UI 模式（推荐）

启动 Streamlit Web 界面，通过浏览器管理航线监控：

```bash
# 启动 Web 界面
streamlit run ui/app.py
```

浏览器访问 `http://localhost:8501`，你可以：

1. **添加航线监控**
   - 在左侧边栏填写出发城市、到达城市
   - 选择目标出行日期
   - 设置目标价格阈值
   - 点击"Add Route"添加监控

2. **查看监控列表**
   - 查看所有已添加的航线
   - 查看最新价格、目标价格
   - 暂停/激活监控
   - 删除航线

3. **价格趋势分析**
   - 选择航线查看价格走势图
   - 查看价格统计数据（平均价、最低价、最高价）
   - 查看历史价格记录

4. **后台自动监控**
   ```bash
   # 在另一个终端启动后台调度器
   python main.py

   # 可选参数
   python main.py --no-headless          # 调试模式（显示浏览器）
   python main.py --enable-notifications # 启用邮件通知
   ```

调度器会每 6 小时自动抓取活跃航线的价格，并在价格低于目标时发送通知。

### 方式二：命令行模式

使用 CLI 命令进行单次查询：

```bash
# 查询航班价格
flightscanner search "北京" "上海" 2024-03-15

# 查看历史价格
flightscanner history "北京" "上海" --days 30

# 查看帮助
flightscanner --help
```

### 完整工作流示例

```bash
# 终端 1: 启动 Web UI
streamlit run ui/app.py

# 终端 2: 启动后台监控（可选）
python main.py --enable-notifications

# 浏览器操作
# 1. 打开 http://localhost:8501
# 2. 添加航线：北京 → 上海，2024-03-15，目标价格 ¥800
# 3. 查看价格趋势图
# 4. 等待后台自动抓取（每6小时）或手动触发
```

### 3. 验证安装

按照开发路线图逐阶段验证：

#### 阶段一：数据库验证

```bash
python scripts/verify_db.py
```

**验证标准**：
- ✅ 数据库表创建成功
- ✅ 插入测试航班数据
- ✅ 插入测试价格数据
- ✅ 查询并验证数据
- ✅ 删除测试数据

#### 阶段二：爬虫原型验证

```bash
python scripts/verify_scraper.py
```

**验证标准**：
- ✅ Playwright 初始化成功
- ✅ 浏览器启动成功
- ✅ 成功访问目标网站
- ✅ 截图保存为 `debug_screenshot.png`
- ✅ 打印页面标题

#### 阶段三：数据链路验证

```bash
pytest tests/test_integration.py -v
```

**验证标准**：
- ✅ Mock数据生成正确
- ✅ 数据成功存入数据库
- ✅ 数据库查询功能正常
- ✅ Flight-Price 关联关系正确

#### 阶段四：LLM与通知验证

```bash
# LLM验证
python scripts/verify_llm.py

# 通知验证
python scripts/verify_notify.py
```

**验证标准**：
- ✅ DeepSeek API 连接成功
- ✅ 收到 LLM 分析结果
- ✅ 邮件/Telegram 通知发送成功

## 核心接口设计

### FlightScraper - 数据采集抽象

```python
from abc import ABC, abstractmethod
from flightscanner.interfaces import SearchParams, FlightPrice

class FlightScraper(ABC):
    @abstractmethod
    async def search_flights(self, params: SearchParams) -> list[FlightPrice]:
        """搜索航班价格"""
        pass

    @abstractmethod
    async def close(self) -> None:
        """清理资源"""
        pass
```

### DataRepository - 数据持久化抽象

```python
class DataRepository(ABC):
    @abstractmethod
    def save_price(self, flight_price: FlightPrice) -> int:
        """保存价格快照"""
        pass

    @abstractmethod
    def get_history(self, departure_city: str, arrival_city: str, days: int) -> list[FlightPrice]:
        """获取历史价格"""
        pass
```

### PriceAnalyzer - 价格分析抽象

```python
class PriceAnalyzer(ABC):
    @abstractmethod
    def predict_trend(self, historical_prices: list[FlightPrice], target_date: date) -> PriceTrend:
        """分析价格趋势"""
        pass

    @abstractmethod
    def should_alert(self, current_price: Decimal, trend: PriceTrend, threshold: Decimal) -> bool:
        """判断是否发送提醒"""
        pass
```

### Notifier - 通知抽象

```python
class Notifier(ABC):
    @abstractmethod
    async def send_alert(self, flight_price: FlightPrice, trend: PriceTrend, message: str) -> bool:
        """发送价格提醒"""
        pass
```

## 数据模型

### Flight - 航班基础信息

```python
class Flight(Base):
    __tablename__ = "flights"

    id = Column(Integer, primary_key=True)
    flight_no = Column(String(20), nullable=False)          # 航班号
    airline = Column(String(100), nullable=False)           # 航空公司
    departure_city = Column(String(50), nullable=False)     # 出发城市
    arrival_city = Column(String(50), nullable=False)       # 到达城市
    departure_time = Column(String(10), nullable=False)     # 出发时间
    arrival_time = Column(String(10), nullable=False)       # 到达时间
    departure_date = Column(Date, nullable=False)           # 航班日期
    direction = Column(String(20), nullable=False)          # 去程/返程
    created_at = Column(DateTime, default=datetime.utcnow)
```

### PriceHistory - 价格快照

```python
class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    flight_id = Column(Integer, ForeignKey("flights.id"))   # 关联航班
    price = Column(Numeric(10, 2), nullable=False)          # 价格
    currency = Column(String(10), default="CNY")            # 货币
    seat_class = Column(String(50), nullable=False)         # 舱位等级
    available_seats = Column(Integer, nullable=True)        # 可用座位数
    source = Column(String(50), nullable=False)             # 数据来源
    scraped_at = Column(DateTime, default=datetime.utcnow)  # 采集时间
```

## 开发路线图

### ✅ 阶段一：基础设施搭建

**任务**：
- [x] 创建项目目录结构
- [x] 配置 pyproject.toml
- [x] 实现数据库模型 (Flight, PriceHistory)
- [x] 编写配置管理模块

**验证脚本**：`scripts/verify_db.py`

### ✅ 阶段二：采集器原型

**任务**：
- [x] 实现 Playwright 基础采集类
- [x] 配置反爬策略
- [x] 截图保存功能

**验证脚本**：`scripts/verify_scraper.py`

### ✅ 阶段三：数据链路打通

**任务**：
- [x] 实现数据解析逻辑
- [x] 实现数据库存储
- [x] 编写集成测试

**验证脚本**：`tests/test_integration.py`

### ✅ 阶段四：LLM与通知集成

**任务**：
- [x] 接入 DeepSeek API (兼容 OpenAI API 格式)
- [x] 实现价格趋势分析
- [x] 实现邮件/Telegram 通知

**验证脚本**：`scripts/verify_llm.py`, `scripts/verify_notify.py`

### ✅ 阶段五：Web UI 与自动监控（已完成 v1.0）

**任务**：
- [x] 数据库模型升级 - 新增 Route 航线监控表
- [x] 服务层开发 - RouteService 业务逻辑
- [x] Web UI 开发 - Streamlit 仪表板
  - [x] 航线添加表单
  - [x] 监控列表展示
  - [x] 价格趋势图表
  - [x] 航线管理操作
- [x] 后台调度器 - APScheduler 定时任务
- [x] 价格提醒集成 - 自动发送通知

**新增功能**：
- ✅ 数据库存储航线监控配置（替代 YAML 文件）
- ✅ Web 界面管理航线监控
- ✅ 后台自动定时抓取价格（每6小时）
- ✅ 价格趋势可视化（Altair 图表）
- ✅ 航线状态管理（激活/暂停）

**启动方式**：
```bash
# Web UI
streamlit run ui/app.py

# 后台调度器
python main.py --enable-notifications
```

### 📋 阶段六：优化与部署

**任务**：
- [ ] 性能优化
- [ ] Docker 容器化
- [ ] CI/CD 配置
- [ ] 监控与日志

## 代码规范

项目遵循以下代码规范：

- **PEP 8**：Python 代码风格指南
- **类型注解**：使用 Python 类型提示
- **文档字符串**：Google 风格的 docstring
- **测试驱动**：TDD 开发模式

### 运行代码检查

```bash
# 代码格式化
black src/ tests/ scripts/
isort src/ tests/ scripts/

# 代码检查
ruff check src/ tests/ scripts/

# 类型检查
mypy src/
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行测试并生成覆盖率报告
pytest --cov=flightscanner --cov-report=html

# 运行特定测试
pytest tests/test_integration.py -v
```

## 错误处理

项目预留了完善的错误处理机制：

```python
# 自定义异常类
class ScraperError(Exception):
    """爬虫基础异常"""

class NetworkTimeoutError(ScraperError):
    """网络超时异常"""

class ParseError(ScraperError):
    """解析错误异常"""

class AntiCrawlerDetectedError(ScraperError):
    """反爬检测异常"""
```

所有模块都使用 `tenacity` 库实现了重试机制：

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def fetch_with_retry():
    # 带重试的请求
    pass
```

## 扩展性设计

项目采用依赖注入和抽象基类设计，易于扩展：

### 添加新的数据源

```python
from flightscanner.interfaces import FlightScraper

 class QunarScraper(FlightScraper):
    async def search_flights(self, params):
        # 实现去哪儿网爬虫
        pass
```

### 添加新的通知渠道

```python
from flightscanner.interfaces import Notifier

class WeChatNotifier(Notifier):
    async def send_alert(self, flight_price, trend, message):
        # 实现微信通知
        pass
```

## 注意事项

1. **反爬策略**：请遵守目标网站的 robots.txt，合理设置爬取频率
2. **API 费用**：DeepSeek API 调用会产生费用，但相比 OpenAI 性价比更高
3. **数据隐私**：.env 文件包含敏感信息，请勿提交到版本控制
4. **法律合规**：仅供个人学习使用，请勿用于商业用途

## 许可证

MIT License

## 贡献指南

欢迎提交 Issue 和 Pull Request！

在提交 PR 之前，请确保：
1. 代码通过所有测试
2. 代码符合 PEP 8 规范
3. 添加了必要的文档和注释

## 联系方式

如有问题或建议，请提交 Issue 或联系作者。

---

**Happy Coding! ✈️**
