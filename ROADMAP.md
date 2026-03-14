# FlightScanner 迭代路线图

---

## Phase 1 — MVP 基础监控（已完成）

**核心目标：** 验证"采集 → 存储 → 通知"基础链路可跑通。

### 已交付功能

- [x] 去哪儿网（Qunar）Playwright 爬虫，支持有头/无头模式
- [x] Cookie 注入机制 + 扫码登录刷新（`scripts/qunar_login.py`）
- [x] Cookie 失效自动弹窗提示重新登录
- [x] APScheduler 定时采集，每条路线独立设置间隔（1~24h）
- [x] SQLite 三表结构（flights / routes / price_history）
- [x] Email（SMTP）价格提醒
- [x] 规则型趋势分析（均值/方差判涨跌，输出 `PriceTrend`）
- [x] Streamlit 仪表板：概览卡片 + 可展开路线卡片（内嵌 Altair 图）
- [x] 31 个单元测试覆盖爬虫核心逻辑

### 主要技术难点与解决方案

| 难点 | 解决方案 |
|------|----------|
| Qunar Bella 指纹反爬 | 注入完整登录 Cookie，绕过 Bella 令牌验证 |
| 移动端两阶段响应（`data: ""`）| `page.route()` 拦截 + 30s 轮询等待非空响应 |
| Cookie 格式兼容 | 同时支持 JSON 数组和原始 `Cookie:` 字符串两种格式 |
| `on_response` 无法读取 body | 改用 `page.route()` + `route.fetch()` 拦截响应体 |

---

## Phase 2 — 核心功能完善（下一阶段）

**核心目标：** 多源数据、多程类型、多通知渠道，进入日常可用状态。

### 功能清单

#### 2.1 多平台采集
- [x] **携程（Ctrip）爬虫完善**：IATA 城市码映射、双策略采集（API 拦截 + DOM 降级）、反爬 JS 注入，全部测试通过
- [x] **多源聚合**：同一路线同时查询 Qunar + Ctrip，相同 `(flight_no, seat_class)` 保留最低价，按价格升序写入 DB
- [x] **爬虫工厂/注册表**：`ScraperRegistry`，按平台名动态构建爬虫实例，支持自定义扩展

#### 2.2 往返程监控
- [x] `Route` 表新增 `return_date`（可空）、`trip_type`（`oneway` / `roundtrip`）字段
- [x] `SearchParams.return_date` 已预留，完成爬虫侧往返查询逻辑
- [x] 仪表板展示去程/回程分列价格

#### 2.3 灵活日期监控
- [ ] `Route` 表新增 `date_flex_days`（默认 0，最大 7）
- [ ] 采集时对 `[target_date - flex, target_date + flex]` 每天独立查询
- [ ] 日历热图展示各日期最低价（Altair heatmap）

#### 2.4 多渠道通知
- [x] **Telegram Bot**：`TelegramNotifier`，通过 Telegram Bot API 发送 Markdown 格式提醒
- [x] **WeCom 企业微信 Webhook**：`WeComNotifier`，群机器人一行 POST 推送 Markdown
- [x] **飞书 Webhook**：`FeiShuNotifier`，Post 富文本格式 + 可选 HMAC-SHA256 签名
- [x] **通知器工厂**：`build_notifiers()` 统一管理四渠道（Email / Telegram / WeCom / 飞书）
- [ ] 仪表板：通知渠道配置面板（填写 Bot Token / Webhook URL）

#### 2.5 过滤条件
- [ ] `Route` 表新增 `filter_json` TEXT 列（存储 JSON），包含：
  - `airlines`：白名单航司列表
  - `direct_only`：是否仅直飞
  - `max_duration_hours`：最长飞行时长
- [ ] 采集后在 `RouteService` 侧过滤，再入库

### 技术难点预判

| 难点 | 方案 |
|------|------|
| 携程反爬比去哪儿更严 | 考虑引入 camoufox 或 stealth 插件，或改用官方 API |
| 灵活日期产生大量查询 | 每日期独立 job，加入随机延迟（3~8s）避免封号 |
| Telegram 国内访问 | 代理配置（`httpx.Client(proxies=...)`）或使用 WeCom 替代 |

---

## Phase 3 — AI 与高级特性

**核心目标：** 接入 AI 大模型，从"监控工具"升级为"决策助手"。

### 功能清单

#### 3.1 AI 趋势分析（DeepSeek）
- [ ] **`DeepSeekAnalyzer`**：实现 `PriceAnalyzer` ABC
  - 输入：过去 30 天价格序列 + 节假日日历 + 出行日期距今天数
  - 输出：`PriceTrend`（direction / confidence / recommendation / predicted_lowest / best_booking_time）
- [ ] **Prompt 工程**：结构化 Few-shot 提示，要求输出 JSON 格式（便于解析）
- [ ] **降级策略**：DeepSeek 调用失败时自动回退到 `RuleBasedAnalyzer`

#### 3.2 AI 简报生成
- [ ] 每周定时（周一早 8 点）生成路线简报：价格波动原因 + 未来一周趋势预测
- [ ] 简报通过已配置的通知渠道推送（邮件正文 / Telegram Markdown）
- [ ] 仪表板"AI 分析"标签页，展示最新简报和置信度图表

#### 3.3 节假日/淡旺季感知
- [ ] 集成中国法定节假日 API（或维护本地 JSON 文件）
- [ ] 在 AI Prompt 中注入：`距离出发日还有 N 天，期间经过 [春节长假/清明]，历史涨幅约 30%`
- [ ] 仪表板在节假日日期旁显示 🔴 警示标记

#### 3.4 国际航班支持
- [x] `Route.is_international` 字段（自动推断 + 手动覆盖）
- [x] 统一 IATA 城市映射（`city_codes.py`，覆盖国内 + 主要国际城市）
- [x] `Flight` 表记录机场名称和 IATA 代码（`departure/arrival_airport_code`）
- [ ] 国际航班爬虫（携程国际版 / 去哪儿国际）或接入 Amadeus / Skyscanner API
- [ ] 货币转换（fixer.io API），存储原始货币和 CNY 折算价

#### 3.5 "最佳买点"推荐
- [ ] 结合 AI 分析置信度 + 当前价格与历史均价偏差，计算"买入信号强度"（0~100 分）
- [ ] 仪表板路线卡片显示信号强度进度条
- [ ] 信号 ≥ 80 时触发"强烈建议购买"通知

### 技术难点预判

| 难点 | 方案 |
|------|------|
| AI 输出不稳定（非 JSON）| 使用 `response_format={"type":"json_object"}` + 重试 |
| 价格数据稀疏（新路线无历史）| 给 AI 提供"同类路线"历史价格作为参照 |
| Token 成本控制 | 仅在有 ≥ 7 条历史记录时调用 AI，其余用规则分析 |

---

## Phase 4 — 生产化与多用户

**核心目标：** 从"个人工具"扩展为可部署的多用户 SaaS 雏形。

### 功能清单

#### 4.1 FastAPI REST 后端
- [ ] 将业务逻辑从 Streamlit 剥离到 FastAPI 路由
- [ ] RESTful API：`GET /routes`, `POST /routes`, `DELETE /routes/{id}`, `GET /routes/{id}/prices`
- [ ] WebSocket 端点：实时推送采集进度
- [ ] Swagger / ReDoc 文档自动生成

#### 4.2 多用户支持
- [ ] `users` 表：id / email / hashed_password / notification_config（JSON）
- [ ] JWT 认证（`python-jose`），Refresh Token 机制
- [ ] 路线隔离：`routes.user_id` FK，查询时自动过滤当前用户
- [ ] 用户自助注册 + 邮件验证

#### 4.3 数据库升级
- [ ] SQLite → PostgreSQL（Alembic 迁移脚本）
- [ ] 添加 TimescaleDB 扩展（`price_history` 改为 Hypertable，提升时序查询性能）
- [ ] Redis 缓存热点路线最新价格（TTL 5 分钟）

#### 4.4 前端升级（可选）
- [ ] 评估是否替换 Streamlit 为 Next.js + TailwindCSS
- [ ] 移动端响应式设计
- [ ] PWA 支持（离线缓存、桌面安装）

#### 4.5 部署与运维
- [ ] `Dockerfile` + `docker-compose.yml`（app + postgres + redis）
- [ ] GitHub Actions CI：lint → test → build → push image
- [ ] 健康检查端点 `GET /health`
- [ ] Sentry 错误监控集成

---

## 里程碑时间线（参考）

```
Phase 1  [已完成]  ██████████████████████
Phase 2  [进行中]  ████████░░░░░░░░░░░░░░  目标：完成核心功能采集 + 通知多渠道
Phase 3  [规划中]  ░░░░░░░░░░░░░░░░░░░░░░  目标：DeepSeek AI 分析上线
Phase 4  [未开始]  ░░░░░░░░░░░░░░░░░░░░░░  目标：生产部署 + 多用户
```

---

## 技术债务记录

| 问题 | 优先级 | 关联阶段 |
|------|--------|---------|
| SQLite 不支持并发写入（多路线同时采集时有锁竞争）| 高 | Phase 2/4 |
| Streamlit 每次交互重新加载全部路线数据（无缓存）| 中 | Phase 3 |
| 爬虫错误仅记日志，未持久化失败记录到 DB | 低 | Phase 3 |
