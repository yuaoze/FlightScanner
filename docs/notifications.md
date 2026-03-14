# 通知渠道接入指南

FlightScanner 支持以下四种通知渠道，可通过 `.env` 文件配置。所有渠道均可同时启用，价格提醒会并行发送到所有已配置的渠道。

---

## 触发条件

价格提醒在满足以下任一条件时触发（优先级由高到低）：

| 条件 | 说明 | 卡片颜色 |
|------|------|----------|
| **target_hit** | 当前价 ≤ 目标价 | 绿色 |
| **near_30d_low** | 当前价 ≤ 30天最低价 × 1.05（接近历史低价） | 橙色 |
| **below_avg** | 当前价 < 30天均价 × (1 - N%)，且历史数据 ≥ 7 条 | 蓝色 |

### 防骚扰冷却

- 同一路线 **24 小时内不重复通知**（可通过 `NOTIFY_COOLDOWN_HOURS` 调整）
- **例外**：若价格在冷却期内再次下降 **≥ 5%**，立即打破冷却重新通知

---

## 飞书机器人

发送 Interactive Card 富交互卡片，支持价格对比、买点建议、触发原因展示。

### 接入步骤

1. 打开飞书群 → 右上角 **设置** → **群机器人** → **添加机器人** → 选择 **自定义机器人**
2. 填写机器人名称（如 "FlightScanner"），复制生成的 **Webhook URL**
3. （可选）开启 **安全设置 → 签名校验**，复制签名密钥
4. 在 `.env` 中填入：

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
FEISHU_WEBHOOK_SECRET=your_secret_key   # 可选，开启签名校验时填写
```

---

## 企业微信机器人

发送 Markdown 格式消息，包含价格统计和买点建议。

### 接入步骤

1. 打开企业微信群 → 右键群名 → **添加机器人**
2. 填写机器人名称，复制生成的 **Webhook URL**
3. 在 `.env` 中填入：

```env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx
```

---

## Telegram 机器人

发送 Markdown 格式消息，包含航班信息、价格统计和买点建议。

### 接入步骤

1. 在 Telegram 中搜索 `@BotFather`，发送 `/newbot`，按提示填写机器人名称，获取 **Bot Token**
2. 获取 Chat ID：
   - 向机器人发送任意消息后，访问：
     `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - 在返回的 JSON 中找到 `"chat": {"id": 123456789}` 字段
3. 在 `.env` 中填入：

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=123456789
```

---

## 邮件（SMTP）

发送 HTML 邮件，包含完整航班信息表格和买点分析模块。

### 接入步骤

1. 在 `.env` 中填入 SMTP 服务器配置：

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_app_password
```

2. **Gmail 用户注意**：需要使用「应用专用密码」而非账号密码：
   - Google 账户 → 安全 → 两步验证 → 应用专用密码 → 生成

3. 其他常见 SMTP 服务商配置：

| 服务商 | HOST | PORT |
|--------|------|------|
| Gmail | smtp.gmail.com | 587 |
| 163邮箱 | smtp.163.com | 465 |
| QQ邮箱 | smtp.qq.com | 587 |
| Outlook | smtp.office365.com | 587 |

---

## 全局通知配置

在 `.env` 中可调整以下全局参数：

```env
# 同一路线重复通知最短间隔（小时），默认 24
NOTIFY_COOLDOWN_HOURS=24

# 低于30天均价 N% 时触发通知，默认 10.0（即低于均价10%）
NOTIFY_BELOW_AVG_THRESHOLD=10.0
```

---

## 验证通知

在添加路线后，可通过 Streamlit UI 触发立即采集来验证通知是否正常工作。
如需快速测试，可临时将 `NOTIFY_BELOW_AVG_THRESHOLD` 调低至 `1.0`，
确认收到通知后再改回默认值。
