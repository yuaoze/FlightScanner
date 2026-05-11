# FlightScanner 部署指南 — 2C4G Linux 服务器

> 适用：阿里云 ECS / 腾讯云 CVM / 轻量应用服务器，2 vCPU + 4GB 内存。
> 系统：Ubuntu 22.04 LTS（其它 Debian/RHEL 派生版只需替换 `apt` 命令）。

完整流程从空白机器到生产可用约 **60-90 分钟**（不含域名备案）。

---

## 0. 部署前检查清单

| 项 | 必需 | 说明 |
|----|------|------|
| 服务器 SSH 访问 | ✅ | 密钥登录（关闭密码登录更安全） |
| 安全组 | ✅ | 入方向开 22 / 80 / 443，**关闭 8000** |
| 域名 | 推荐 | 大陆 ECS 80/443 必须备案；境外区或仅 Cloudflare Tunnel 可免备案 |
| 时区 | ✅ | 必须 `Asia/Shanghai`，否则 CST 显示会错 |
| Git 仓库访问 | ✅ | 仓库地址或打包好的 tarball |

资源规划（2C4G）：

```
4 GB 内存分配
├─ 系统 + sshd + journald     ~500 MB
├─ nginx                       ~50 MB
├─ uvicorn + FastAPI 常驻      ~250 MB
├─ Chromium 单实例（采集时）    400-700 MB
├─ Chromium 双平台并发峰值      ~1.4 GB
└─ 安全余量                     ~1 GB
```

**关键约束**：4GB 不够同时跑「定时路线采集 + 周末雷达批扫」，必须靠 swap 和并发限制兜底。

---

## 1. 系统初始化

```bash
# SSH 登录后立刻：
sudo apt update && sudo apt upgrade -y

# 时区
sudo timedatectl set-timezone Asia/Shanghai

# 防火墙 —— 必须用显式端口号
# （不要用 'Nginx Full'，那是 nginx 装包后才注册的 app profile，
# 此时 nginx 还没装，会被 ufw 静默忽略 → 80/443 还是不通）
sudo apt install -y ufw
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# 验证 — 应看到 22/tcp、80/tcp、443/tcp 三条 ALLOW
sudo ufw status
```

> **⚠️ 别忘了云服务商安全组**：ufw 是服务器内的防火墙，云控制台**安全组是另一道独立防火墙**。
> 必须同时在云控制台（阿里云 / 百度云 / 腾讯云）的「实例 → 安全组 → 入站规则」里放行 80/443，
> 否则即使 ufw 开了也访问不到。安全组规则改动**立即生效**，不用重启实例。

### 加 4GB swap（强烈建议）

2C4G 内存余量只有 ~1GB，weekend radar 等高峰场景依赖 swap 保命：

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 让内核更倾向 swap，少 OOM kill
echo 'vm.swappiness=20' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

验证：

```bash
free -h    # 看到 Swap 行 4Gi 即可
```

---

## 2. 系统依赖

```bash
# 基础工具
sudo apt install -y git curl build-essential nginx \
  python3.10 python3.10-venv python3-pip \
  sqlite3 libsqlite3-dev

# Node.js 20（前端构建用）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs

# Playwright 系统依赖（Chromium 跑起来需要的一堆 lib）
# 这一步必装 sudo
sudo apt install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
  fonts-noto-cjk    # 中文字体 — 不装会导致页面渲染没字，影响 DOM 解析
```

---

## 3. 创建专用用户 + 拉代码

不要用 root 跑应用：

```bash
# 创建用户
sudo useradd -m -s /bin/bash flightscanner
sudo passwd -l flightscanner   # 锁定密码登录，仅允许 sudo su 切换

# 切到该用户
sudo -iu flightscanner

# 拉代码
git clone https://your.git.host/FlightScanner.git
cd FlightScanner
```

---

## 4. Python 后端

```bash
# 仍以 flightscanner 用户身份
cd ~/FlightScanner

python3.10 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -e .

# 装 Playwright bundled chromium
playwright install chromium
```

> 如果 `playwright install` 报库缺失，回到 root 用户跑 `sudo $(which python3.10) -m playwright install-deps chromium` 一键补全。

---

## 5. 数据库目录

```bash
# root 创建数据目录
sudo mkdir -p /var/lib/flightscanner/backups
sudo chown -R flightscanner:flightscanner /var/lib/flightscanner
```

---

## 6. 配置 .env

```bash
# 仍以 flightscanner 用户身份
cd ~/FlightScanner
cp .env.example .env
chmod 600 .env       # 关键 — 含 API key 必须严格权限
nano .env
```

**最小可用配置**（按需补充其它字段）：

```ini
# 数据库放数据目录
DATABASE_URL=sqlite:////var/lib/flightscanner/flightscanner.db

# 必须 headless（服务器无图形）
SCRAPER_HEADLESS=true
SCRAPER_TYPE=qunar,ctrip
SCRAPER_TIMEOUT=30000
MAX_RESULTS_PER_PLATFORM=15      # 4G 内存建议从 15 起，跑稳后再调

# AI（可选；填了启用 DeepSeek 趋势分析）
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 至少配置一个通知渠道（推荐 Telegram，最稳）
TELEGRAM_BOT_TOKEN=123456:ABC-xxx
TELEGRAM_CHAT_ID=your-chat-id

# 通知冷却（默认值适用，按需调整）
NOTIFY_COOLDOWN_TARGET_HIT=4
NOTIFY_BELOW_AVG_THRESHOLD=10
```

---

## 7. 前端构建

```bash
cd ~/FlightScanner/frontend
npm ci          # 用 lock 文件，避免 npm install 的不确定性
npm run build   # 产出 frontend/dist/
```

构建产物 ~880KB（gzip 后 ~265KB），完全静态，由 nginx serve。

---

## 8. systemd 守护进程

```bash
sudo nano /etc/systemd/system/flightscanner-api.service
```

```ini
[Unit]
Description=FlightScanner API + Scheduler
After=network.target

[Service]
Type=simple
User=flightscanner
Group=flightscanner
WorkingDirectory=/home/flightscanner/FlightScanner
Environment="PATH=/home/flightscanner/FlightScanner/venv/bin"
EnvironmentFile=/home/flightscanner/FlightScanner/.env
ExecStart=/home/flightscanner/FlightScanner/venv/bin/python -m uvicorn \
    flightscanner.api.main:app \
    --host 127.0.0.1 \
    --port 8000

Restart=on-failure
RestartSec=10

# 日志
StandardOutput=journal
StandardError=journal

# 资源限制（4G 内存重点关注）
MemoryMax=2.5G          # 硬上限，超过 systemd kill
MemoryHigh=2G           # soft limit，throttle GC
TasksMax=300

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/flightscanner /home/flightscanner/FlightScanner

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flightscanner-api

# 查日志（实时）
sudo journalctl -u flightscanner-api -f

# 验证服务起来了
curl -s http://127.0.0.1:8000/api/stats | head -c 200
```

---

## 9. nginx 反向代理

```bash
sudo nano /etc/nginx/sites-available/flightscanner
```

```nginx
server {
    listen 80;
    server_name your-domain.com;       # 没域名暂时填 _ 或 ECS 公网 IP

    # 前端静态资源
    root /home/flightscanner/FlightScanner/frontend/dist;
    index index.html;

    # gzip
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    # 缓存静态资源
    location ~* \.(js|css|png|jpg|svg|woff2)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # SPA 路由 fallback — 所有非 /api 路径走 index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 反代到 uvicorn
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 扫码登录轮询是慢请求
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
    }

    # 限制单请求体大小
    client_max_body_size 10M;
}
```

```bash
# nginx 用户需要能读 frontend/dist
sudo chmod 755 /home/flightscanner

sudo ln -s /etc/nginx/sites-available/flightscanner /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default     # 移除默认欢迎页
sudo nginx -t
sudo systemctl reload nginx
```

打开浏览器访问 `http://your-server-ip` 应该能看到 FlightScanner 界面（无 HTTPS）。

---

## 10. HTTPS（强烈建议）

仅当域名已解析到此服务器 IP（A 记录）后再做：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
# 按提示填邮箱、同意 ToS、选择强制 HTTPS（推荐 2 = redirect）
```

证书自动续期 cron 由 certbot 自动配置，验证：

```bash
sudo certbot renew --dry-run
```

---

## 11. 上传 Cookie（首次部署必做）

服务跑起来后访问 `https://your-domain.com/settings`：

1. **「Cookie 管理」** 卡片 → 点「**扫码刷新 去哪儿**」
2. 服务端启动 headless Chromium → 抓取登录二维码 → 通过 base64 返回前端
3. 用手机扫描浏览器上显示的二维码 → 几秒后服务端拿到 Cookie 落库
4. **携程同样操作一次**

或本地获取 Cookie 后通过「手动上传」粘贴：

```bash
# 本地机器：
python scripts/qunar_login.py
cat qunar_cookies.json
# 复制内容 → 设置页粘贴 → 保存
```

---

## 12. 添加首条监控验证

1. 浏览器访问 `https://your-domain.com`
2. 左侧导航 「＋ 添加监控」
3. 填出发城市/到达城市/出行日期/目标价 → 提交
4. 5-10 分钟后回到「监控总览」，能看到卡片带 sparkline + AI Badge → ✅ 部署成功

---

## 13. 定时备份（推荐）

```bash
# 仍以 flightscanner 用户身份
nano ~/backup_db.sh
```

```bash
#!/bin/bash
set -e
DEST=/var/lib/flightscanner/backups
DB=/var/lib/flightscanner/flightscanner.db

# 用 SQLite .backup 命令保证一致性（比 cp 文件安全）
sqlite3 "$DB" ".backup $DEST/$(date +%Y%m%d_%H%M).db"

# 仅保留最近 14 天
find "$DEST" -name "*.db" -mtime +14 -delete

echo "[$(date)] backup ok, $(ls -1 $DEST | wc -l) files in $DEST"
```

```bash
chmod +x ~/backup_db.sh

# 加 cron — 每天 03:30 备份
crontab -e
# 加入这行：
30 3 * * * /home/flightscanner/backup_db.sh >> /home/flightscanner/backup.log 2>&1
```

---

## 14. 监控告警（强烈建议）

2C4G 跑这套服务有 OOM 风险，**必须监控** 。

### 内存告警

```bash
# /home/flightscanner/mem_watch.sh
#!/bin/bash
USED_PCT=$(free | awk '/^Mem:/ {printf "%.0f", $3/$2 * 100}')
SWAP_USED=$(free -m | awk '/^Swap:/ {print $3}')

if [ "$USED_PCT" -gt 90 ] || [ "$SWAP_USED" -gt 2048 ]; then
    curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage" \
        -d "chat_id=$TG_CHAT_ID" \
        -d "text=⚠️ FS 服务器内存吃紧: ${USED_PCT}% RAM, ${SWAP_USED}MB swap"
fi
```

```bash
chmod +x ~/mem_watch.sh

# cron 每 5 分钟
*/5 * * * * /home/flightscanner/mem_watch.sh
```

### OOM kill 检测

```bash
# /home/flightscanner/oom_check.sh
#!/bin/bash
killed=$(sudo journalctl -k --since "5 min ago" | grep -ci "Out of memory")
if [ "$killed" -gt 0 ]; then
    curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage" \
        -d "chat_id=$TG_CHAT_ID" \
        -d "text=🔥 FS 服务发生 OOM kill ${killed} 次，已自动重启"
    sudo systemctl restart flightscanner-api
fi
```

cron 每 5 分钟跑。

---

## 15. 后续更新部署

```bash
# /home/flightscanner/deploy.sh
#!/bin/bash
set -e
cd /home/flightscanner/FlightScanner

git pull

source venv/bin/activate
pip install -e . --quiet

cd frontend
npm ci --silent
npm run build

cd ..
sudo systemctl restart flightscanner-api

echo "✓ 部署完成 $(date)"
```

```bash
chmod +x ~/deploy.sh
```

每次代码更新只需：

```bash
ssh flightscanner@your-server
~/deploy.sh
```

---

## 16. 故障排查速查

| 现象 | 排查 |
|------|------|
| **浏览器访问公网 IP 超时** | ① `sudo ufw status` 应有 80/tcp + 443/tcp ALLOW（**没有就 `sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw reload`**）；② 云控制台安全组入方向开 80/443；③ 大陆 ECS 域名未备案时 80/443 可能被运营商拦，临时改 8080 |
| `https://your-domain.com` 502 Bad Gateway | `sudo systemctl status flightscanner-api`，看 journalctl |
| API 响应正常但前端白屏 | nginx 配置 `try_files` 是否正确；`frontend/dist/` 是否存在 |
| 采集 0 条 / 403 | Cookie 失效 → 设置页扫码刷新 |
| 内存爆 / 服务重启循环 | swap 是否启用；`MAX_RESULTS_PER_PLATFORM` 减小到 8-10 |
| 时区显示错（UTC 而非 CST） | `timedatectl` 检查；服务端代码已用 `time_utils.fmt_cst` |
| 数据库锁死 (database is locked) | 检查是否有遗留 streamlit 进程；`fuser /var/lib/flightscanner/flightscanner.db` |
| 前端调用 `/api/*` 404 | nginx `location /api/` proxy_pass 末尾的 `/` 不能漏 |
| Chromium 启动失败 | `sudo apt install` 缺的库；`fonts-noto-cjk` 必装 |
| systemd ValidationError | `.env` 里 `KEY=value # 注释` 内联注释会被吞进值，改成注释独占一行 |

### 常用诊断命令

```bash
# 服务状态
sudo systemctl status flightscanner-api nginx

# 实时日志
sudo journalctl -u flightscanner-api -f
sudo tail -f /var/log/nginx/error.log

# 内存/CPU
htop
free -h

# 端口监听
sudo ss -tlnp | grep -E '80|443|8000'

# API 端点
curl -s http://127.0.0.1:8000/api/stats
curl -s http://127.0.0.1:8000/api/cookies/status
```

---

## 17. 资源使用预期（参考）

正常负载（5-10 路线，6h 间隔，无周末雷达）：

```
状态        RAM 占用    CPU
─────────────────────────────
空闲        ~600 MB     <1%
单平台采集  ~1.2 GB     5-15%
双平台采集  ~1.8 GB     10-30%
峰值        ~2.5 GB     50-80%（短暂）
```

**要避免的情况**：
- 周末雷达批扫（30 城市 × 8 周末）+ 同时定时路线触发 → 容易超 3GB → swap 大量进入 → 响应变慢
- 应对：要么把 weekend_radar 关闭（参考 [`feature_log/v2.0.0.md`](../feature_log/v2.0.0.md) 调度器代码），要么把所有路线 scrape_interval 设为 12h+ 错峰

---

## 18. ⚠️ 中国大陆 ECS 备案提醒

如果服务器在大陆区（杭州/上海/北京等）且对外用域名访问 80/443：

- **必须先 ICP 备案**，否则运营商在 80/443 端口拦截，访问报 connection reset
- 备案需要域名 + 阿里云账号实名 + 个人/企业资料
- 周期 7-20 工作日

**临时绕过方案**：
- 用 SSH tunnel：`ssh -L 8080:127.0.0.1:80 user@ecs` → 本地访问 `http://localhost:8080`
- 或用 8080 等非常用端口（不会被拦），nginx 改 `listen 8080`
- 或换境外区（新加坡、东京等），但反爬风险升高

---

## 完成

```
🛬 部署完成！
   API:        https://your-domain.com/api
   前端:        https://your-domain.com/
   API 文档:    https://your-domain.com/api/docs（FastAPI 自动生成）
   日志:        sudo journalctl -u flightscanner-api -f
   重启:        sudo systemctl restart flightscanner-api
   更新:        ~/deploy.sh
```

后续运维参考 [`feature_log/v2.0.0.md`](../feature_log/v2.0.0.md) 了解 v2.0 的架构变化。
