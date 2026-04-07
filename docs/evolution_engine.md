# AI 进化引擎（Evolution Engine）

## 概述

Evolution Engine 是一套 **4 齿轮闭环自进化预测系统**，让 AI 机票价格简报在持续使用过程中不断积累经验、识别失误模式并修正自身行为。

核心思路来自《原则》"机器论"：记录决策 → 观察结果 → 诊断失误 → 反馈改进。

```
每次采集          每天 UTC 03:00           生成 AI 简报时
    │                    │                       │
  [G1 执行器]   →   [G2 监控器]   →   [G3 诊断器]   →   [G4 进化器]
  记录预测快照      回测计算 Pain       LLM 根因分析     注入历史上下文
                       Index                             + 信誉 badge
```

---

## 四个齿轮详解

### G1 执行器（Executor）

**时机**：每次价格采集完成后，满足触发条件时自动写入。

**触发条件**（满足任一）：
- 已累积 ≥ 5 个不同采集批次
- 最新批次价格与历史中位数偏差 ≥ 8%（价格异动）

**冷却规则**：同一路线 **12 小时内只记录一次**，避免同质预测积压。

**记录内容**（`ai_prediction_log` 表）：

| 字段 | 含义 |
|------|------|
| `price_at_prediction` | 预测时当前最低价（基准价） |
| `recommended_action` | `Buy` 或 `Wait` |
| `trend` | 上涨 / 下跌 / 震荡 / 稳定 |
| `confidence` | AI 置信度（0.0 ~ 1.0） |
| `reason` | 一句话建议原因 |
| `llm_source` | `deepseek` 或 `rule_based` |
| `days_until_flight` | 预测时距出发天数 |
| `outcome_status` | 初始为 `pending` |

**实现**：`evolution_engine.log_prediction()`

---

### G2 监控器（Monitor）

**时机**：每天 UTC 03:00 定时运行（APScheduler CronTrigger）。

**回测触发条件**（满足任一）：

| 条件 | 覆盖范围 | 价格观测窗口 |
|------|----------|------------|
| 路线已出发（`target_date < today`） | 该路线所有 `pending` 记录 | `predicted_at` → `target_date 23:59 UTC` |
| 预测写入满 7 天 | 仅该路线**最新一条** `pending` 记录 | `predicted_at` → `predicted_at + 7d` |

> 7 天条件只取最新记录，目的是防止历史旧记录随时间累积后反复批量触发。

**跳过条件**：观测窗口内采集批次 < 2（数据不足，无法评估走势）→ `outcome_status = "skipped"`。

**Pain Index 计算原则**：

```
建议 Buy，价格后续下跌超过 5%：
  pain = (base_price - actual_min_price) × 0.7
  （机会成本折扣：买贵了，但不是 100% 的损失）

建议 Wait，价格后续上涨超过 5%：
  pain = (actual_final_price - base_price) × 1.0
  （错过买点，全额计算损失）

价格变动 ≤ 5%（SIGNIFICANCE_THRESHOLD）：
  outcome = neutral，pain = 0
```

**最终状态**：

| `outcome_status` | 含义 |
|-----------------|------|
| `win` | 预测方向正确，pain ≤ 0 |
| `loss` | 预测失误，pain > 0 |
| `neutral` | 价格变动不显著（≤ 5%） |
| `skipped` | 数据不足，无法评估 |

**附加字段**：`catchable_low_exists`（0/1）——在观测窗口内是否存在可捕捉的低价机会（需连续 ≥ 2 批次低于基准价 5% 才算）。

**实现**：`evolution_engine.run_backtesting()`

---

### G3 诊断器（Diagnostician）

**时机**：G2 完成后，对高痛失误记录自动触发（需配置 `DEEPSEEK_API_KEY`）。

**触发条件**：`outcome_status = "loss"` 且 `pain_index > 200 CNY` 且 `rca_run_at IS NULL`（尚未分析过）。

**实现方式**：发起第二次 LLM 调用（独立于简报生成），要求模型输出结构化 JSON：

```json
{
  "error_category": "以下之一",
  "rca_analysis": "中文分析，失误原因 + 改进建议（100字以内）"
}
```

**错误分类（error_category）**：

| 分类 | 含义 |
|------|------|
| `Holiday_Surge_Ignored` | 节假日涨价未被识别 |
| `Airline_Flash_Sale_Missed` | 航司闪促未被捕捉 |
| `Too_Close_To_Departure` | 临近出发，规律失效 |
| `Trend_Reversal_Undetected` | 趋势反转未被察觉 |
| `Low_Historical_Data` | 历史数据不足，预测依据薄弱 |
| `External_Event_Impact` | 外部事件影响（政策/自然灾害等） |
| `Other` | 其他原因 |

分析结果写入 `error_category`、`rca_analysis`、`rca_run_at` 字段，供 G4 使用。

**实现**：`evolution_engine.run_rca()`

---

### G4 进化器（Evolver）

**时机**：每次生成 AI 价格简报时实时调用。

**两项功能**：

#### 1. 历史失误上下文注入

从数据库读取该路线的历史预测记录，拼接成摘要字符串，**追加到 AI 的 system prompt 末尾**：

```
【历史预测记录】胜率 60%（10 次）
近期失误：
  - 建议Buy，实际痛苦指数 ¥240，分类：Holiday_Surge_Ignored
  - 建议Wait，实际痛苦指数 ¥180，分类：Trend_Reversal_Undetected
请在本次分析中参考以上历史失误，避免重复类似错误。
```

触发条件：已评估记录 ≥ 3 条（`MIN_EVALUATED_FOR_CREDIBILITY`）。

#### 2. 信誉 Badge 展示

在 UI 简报卡片下方展示 AI 胜率徽章：

| 胜率 | Badge 颜色 |
|------|-----------|
| ≥ 70% | 绿色 `#10b981` |
| 50% ~ 70% | 黄色 `#f59e0b` |
| < 50% | 红色 `#ef4444` |

> 胜率计算：`(win + neutral × 0.5) / evaluated_count`（neutral 算半胜，因为价格未显著变动并非真正失误）

#### 3. 熔断机制（Circuit Breaker）

若最近连续 **3 次**（`CIRCUIT_BREAKER_CONSECUTIVE`）预测的 `pain_index ≥ 300 CNY`（`FATAL_LOSS_THRESHOLD`），触发熔断：
- UI 暂停自动生成 AI 简报，显示警告
- AI prompt 中追加熔断警告，提高分析置信度门槛
- 用户可点击"强制生成"按钮手动覆盖

**实现**：`evolution_engine.get_route_credibility()` + `evolution_engine.build_evolved_context()`

---

## 数据流图

```
采集完成
    ↓
_maybe_log_prediction()        ← 满足触发条件 + 12h 冷却
    ↓
G1: log_prediction()           ← 写入 ai_prediction_log（pending）
    ↓
（每天 UTC 03:00）
    ↓
G2: run_backtesting()          ← 回测所有可评估记录
    ↓ 写入 outcome_status / pain_index
G3: run_rca()                  ← pain > 200 CNY 触发 LLM 根因分析
    ↓ 写入 error_category / rca_analysis
（下次 AI 简报生成）
    ↓
G4: build_evolved_context()    ← 读取历史失误，拼接 system prompt 后缀
    ↓
generate_brief_with_fallback() ← 携带进化上下文的 AI 调用
    ↓
render_ai_brief()              ← 渲染简报 + 信誉 badge / 熔断警告
```

---

## 关键常量

| 常量 | 值 | 含义 |
|------|-----|------|
| `HIGH_PAIN_THRESHOLD` | 200 CNY | 触发 G3 RCA 的最低痛苦指数 |
| `FATAL_LOSS_THRESHOLD` | 300 CNY | 计入熔断计数的极严重失误阈值 |
| `SIGNIFICANCE_THRESHOLD` | 5% | 价格变动显著性门槛（低于此值视为 neutral） |
| `CIRCUIT_BREAKER_CONSECUTIVE` | 3 次 | 连续极严重失误多少次触发熔断 |
| `MIN_EVALUATED_FOR_CREDIBILITY` | 3 条 | 显示信誉 badge 所需最少已评估预测数 |
| `CATCHABLE_LOW_MIN_BATCHES` | 2 批 | 判断"可捕捉低价"所需最少连续低价批次 |

---

## 涉及文件

| 文件 | 作用 |
|------|------|
| `src/flightscanner/analyzers/evolution_engine.py` | 4 齿轮完整实现 |
| `src/flightscanner/models/database.py` | `AIPredictionLog` ORM 模型 |
| `src/flightscanner/scheduler/price_monitor.py` | G1 采集后 hook；G2/G3 daily cron |
| `src/flightscanner/analyzers/deepseek_analyzer.py` | `evolution_context` 参数注入 |
| `ui/components/ai_brief.py` | G4 信誉 badge、熔断 UI、上下文注入 |
| `tests/test_evolution_engine.py` | 4 齿轮单元测试 |
