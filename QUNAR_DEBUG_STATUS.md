# 去哪儿网二维码捕获修复 - 最终状态

## ✅ 问题已解决！

### 核心修复

基于实际F12检查结果，发现并使用了关键的登录按钮元素：
```html
<a id="__headerInfo_login__" href="https://user.qunar.com/passport/login.jsp?ret=..." rel="nofollow">登录</a>
```

## 修复详情

### 1. 改进的登录状态检测 ✅

**文件**: `src/flightscanner/scrapers/qunar_scraper.py:305-375`

**优先级检查顺序**:

#### 优先级1: 检查登录按钮（最可靠）
```python
login_button = await page.query_selector("#__headerInfo_login__")
if login_button and await login_button.is_visible():
    logger.info("Detected login button - user not logged in")
    return True
```
- **关键发现**: 如果登录按钮可见 → 用户未登录
- **关键发现**: 如果登录按钮不可见 → 用户已登录

#### 优先级2: 检查登录弹窗
```python
login_qr_popup = await page.query_selector(".login_QR_imgs")
if login_qr_popup and await login_qr_popup.is_visible():
    return True
```

#### 优先级3: 检查各类登录模态框
```python
login_modal_selectors = [
    ".login-modal[style*='display: block']",
    ".login_container",
    "[class*='login'][class*='popup']",
    # ...
]
```

#### 优先级4: 检查URL重定向（新增）
```python
if "user.qunar.com/passport/login" in current_url:
    return True
```

#### 优先级5: 检查二维码图片
```python
qr_img = await page.query_selector("img[src*='qcode/show']")
if qr_img and await qr_img.is_visible():
    return True
```

### 2. 主动触发登录流程 ✅

**文件**: `src/flightscanner/scrapers/qunar_scraper.py:377-467`

**Step 0: 点击登录按钮触发页面重定向**
```python
# 检查登录弹窗是否已可见
login_popup = await page.query_selector(".login_QR_imgs")
popup_visible = login_popup and await login_popup.is_visible()

if not popup_visible:
    # 点击登录按钮
    login_button = await page.query_selector("#__headerInfo_login__")
    if login_button:
        await login_button.click()
        logger.info("✓ Clicked login button (#__headerInfo_login__)")
        # 等待页面重定向和二维码加载
        await asyncio.sleep(5)
```

**关键发现**:
- 点击登录按钮会触发**页面重定向**（不是模态弹窗）
- 重定向到: `https://user.qunar.com/passport/login.jsp?ret=...`
- 二维码直接在登录页面中显示

### 3. 优化的选择器优先级 ✅

**基于实际测试结果重新排序**:

```python
qunar_specific_selectors = [
    "img[src*='qcode/show']",              # 最可靠（直接QR图片）
    "img[src*='user.qunar.com/qcode']",    # QR URL模式
    ".login_QR_imgs img",                   # 模态弹窗场景
    ".login_QR_imgs",                       # 容器元素
]
```

**实际测试结果**:
- ✓ `img[src*='qcode/show']` 成功捕获（登录页面）
- URL: `https://user.qunar.com/qcode/show?token=80E89EDA...`
- 尺寸: 200×201像素
- 大小: ~1.4KB

## 测试验证

### 自动测试结果 ✅

运行 `python scripts/test_qr_fix.py`:

```
✓ 检测到登录按钮 (#__headerInfo_login__)
✓ 点击登录按钮成功
✓ 页面重定向到 user.qunar.com/passport/login.jsp
✓ 找到QR码图片 img[src*='qcode/show']
✓ 二维码已保存: qunar_login_qr.png
✓ 文件大小: 1425 bytes (200×201 PNG)
```

### 调试脚本结果 ✅

运行 `python scripts/debug_page_elements.py`:

```
4. 检查登录按钮...
   ✓ 找到登录按钮 (#__headerInfo_login__)
   登录按钮可见: True
   → 用户未登录，需要登录
   ✓ 已点击登录按钮

6. 检查二维码图片...
   找到 1 个包含'qcode'的图片
   图片 1:
   - src: https://user.qunar.com/qcode/show?token=...
   - 可见: True
   ✓ 已保存截图: qr_img_antidetect_1.png
```

## 工作流程

### 完整的登录检测和二维码捕获流程

1. **访问航班搜索页面**
   ```
   https://flight.qunar.com/site/oneway_list.htm?fromCity=上海&toCity=成都&...
   ```

2. **检测登录状态**
   - 查找登录按钮 `#__headerInfo_login__`
   - 如果可见 → 用户未登录

3. **触发登录流程**
   - 点击登录按钮
   - 页面重定向到登录页

4. **等待页面加载**
   - 等待5秒让页面重定向和QR码加载

5. **捕获二维码**
   - 使用选择器 `img[src*='qcode/show']`
   - 截图保存到 `qunar_login_qr.png`

6. **处理登录**
   - 无头模式: 打开二维码图片，等待扫码（最多120秒）
   - 有头模式: 用户在浏览器窗口中手动扫码

7. **继续采集**
   - 登录成功后自动保存cookies
   - 继续采集航班价格数据

## 修改的文件

### 主要文件
- **`src/flightscanner/scrapers/qunar_scraper.py`**
  - `_is_login_required()` (line 305-375): 改进的登录检测
  - `_capture_login_qr_code()` (line 377-467): 主动触发登录

### 测试文件
- **`scripts/debug_page_elements.py`**
  - 添加登录按钮检测和点击逻辑
  - 改进的调试输出

- **`scripts/test_qr_fix.py`**
  - 完整的QR码捕获测试流程

## 关键技术要点

### 1. 登录按钮作为可靠指标
- ID: `#__headerInfo_login__`
- 可见 = 未登录，不可见 = 已登录
- 是最可靠的登录状态指示器

### 2. 页面重定向行为
- 点击登录按钮触发完整页面重定向（非AJAX）
- 重定向到独立的登录页面
- 需要等待足够时间让重定向完成

### 3. 反检测措施持续有效
```python
# 浏览器启动参数
args=["--disable-blink-features=AutomationControlled"]

# JavaScript注入
Object.defineProperty(navigator, 'webdriver', { get: () => false });
window.chrome = { runtime: {} };
```

### 4. 选择器优先级
- URL匹配选择器最可靠: `img[src*='qcode/show']`
- DOM结构选择器作为备用: `.login_QR_imgs img`

## 已知限制

### 1. 登录超时
- 无头模式下需要在120秒内完成扫码
- 超时后抛出 `LoginRequiredError`

### 2. Cookies有效期
- 登录成功后cookies会保存
- 但cookies有有效期，过期后需要重新登录

### 3. 反爬虫措施
- 去哪儿网可能会更新反爬虫策略
- 需要定期验证登录流程是否正常

## 后续优化建议

### 1. Cookies管理
使用 `scripts/extract_qunar_cookies.py` 手动提取cookies:
```bash
python scripts/extract_qunar_cookies.py
```
然后在 `.env` 中设置 `QUNAR_COOKIES`，避免每次都需要扫码。

### 2. 登录状态持久化
- 实现cookies自动保存和加载
- 定期检查cookies有效性
- 失效时自动触发重新登录

### 3. 多账号支持
- 支持配置多个去哪儿账号
- 轮换使用避免频率限制

## 总结

✅ **登录检测**：使用 `#__headerInfo_login__` 按钮作为可靠指标
✅ **触发登录**：主动点击按钮触发页面重定向
✅ **二维码捕获**：使用 `img[src*='qcode/show']` 成功捕获
✅ **自动化流程**：无头模式下自动打开QR码并等待扫码
✅ **Cookies保存**：登录成功后自动保存cookies供后续使用

**状态**: 功能完全正常 ✅
**最后测试**: 2026-02-28 17:14
**测试结果**: 二维码成功捕获并保存
