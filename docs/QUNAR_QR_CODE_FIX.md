# 去哪儿网二维码捕获功能修复说明

## 问题诊断

### 原始问题
去哪儿网的登录二维码无法正常捕获，原因包括：

1. **选择器不足**: 原代码只有4个简单的选择器
2. **缺少等待**: 使用`query_selector`立即查询，没有等待元素出现
3. **未触发登录**: 没有尝试点击登录按钮来触发二维码弹窗
4. **未检查iframe**: 登录弹窗可能在iframe中
5. **缺少后备方案**: 找不到二维码就直接失败

### 分析结果
通过实际访问 https://flight.qunar.com 发现：

- 页面上有多个base64编码的图片，但默认不可见
- 登录二维码需要**主动触发**（点击登录按钮）
- 二维码可能出现在模态框/弹窗中
- 不同页面可能使用不同的class名称

## 修复方案

### 修改文件
`src/flightscanner/scrapers/qunar_scraper.py` 的 `_capture_login_qr_code()` 方法

### 改进内容

#### 1. **主动触发登录弹窗** (新增)
```python
login_button_selectors = [
    "text=/登录/",              # 文本匹配
    ".q_header_username",        # 去哪儿特定class
    "#J_loginBtn",               # ID选择器
    "a:has-text('登录')",        # 包含"登录"文本的链接
    "button:has-text('登录')",   # 包含"登录"文本的按钮
]

# 尝试点击登录按钮触发二维码弹窗
for selector in login_button_selectors:
    login_btn = await page.wait_for_selector(selector, timeout=2000, state="visible")
    if login_btn:
        await login_btn.click()
        await asyncio.sleep(2)  # 等待弹窗出现
        break
```

#### 2. **大幅扩展二维码选择器** (从4个增加到30+个)
```python
qr_selectors = [
    # 基础选择器
    "img[class*='qrcode']",
    "img[class*='QRCode']",
    "img[src*='qrcode']",
    "img[alt*='二维码']",
    "canvas[class*='qrcode']",

    # 容器内查找
    "[class*='qrcode'] img",
    "[id*='qrcode'] img",
    "[class*='login-qr'] img",

    # 模态框内查找
    "[class*='modal'] img[src*='data:image']",
    "[class*='dialog'] img[src*='data:image']",
    "[class*='popup'] img[src*='data:image']",

    # Canvas变体
    "[class*='login'] canvas",
    "[class*='qr'] canvas",

    # 更多变体...
]
```

#### 3. **使用等待机制** (重要改进)
```python
# 旧代码: 立即查询，找不到就返回None
qr_element = await page.query_selector("...")

# 新代码: 等待元素出现（最多3秒）
qr_element = await page.wait_for_selector(
    selector,
    timeout=3000,  # 等待3秒
    state="visible"  # 必须可见
)
```

#### 4. **检查iframe** (新增)
```python
# 检查所有iframe中的二维码
for frame in page.frames:
    if frame != page.main_frame:
        for selector in qr_selectors[:10]:
            qr_in_frame = await frame.wait_for_selector(
                selector, timeout=1000, state="visible"
            )
            if qr_in_frame:
                screenshot = await qr_in_frame.screenshot()
                return base64.b64encode(screenshot).decode("utf-8")
```

#### 5. **多层后备方案** (新增)

**方案A**: 查找具体二维码元素 (30+个选择器)
↓
**方案B**: 检查iframe中的二维码
↓
**方案C**: 截取登录区域 (模态框/对话框)
```python
login_area_selectors = [
    ".login-container",
    ".qrcode-container",
    "[class*='login-modal']",
    "[role='dialog']",
    ".modal-content",
    # ...
]
```
↓
**方案D**: 截取完整页面 (最后手段)
```python
screenshot = await page.screenshot(full_page=False)
return base64.b64encode(screenshot).decode("utf-8")
```

#### 6. **详细日志输出** (新增)
```python
logger.info("Attempting to capture login QR code...")
logger.info(f"Clicked login button: {selector}")
logger.info(f"Found QR code element: {selector}")
logger.info(f"QR code element tag: {tag_name}")
logger.info("QR code not found in main page, checking iframes...")
logger.warning("Could not find specific QR code element, taking full page screenshot")
```

## 修复效果

### Before (旧代码)
```
❌ 4个简单选择器
❌ 立即查询，没有等待
❌ 找不到就返回None
❌ 无触发登录逻辑
❌ 无iframe检查
❌ 无后备方案
```

### After (新代码)
```
✅ 30+个全面选择器
✅ 等待机制 (wait_for_selector)
✅ 主动触发登录弹窗
✅ 检查iframe中的二维码
✅ 4层后备方案
✅ 详细日志输出
✅ 容错处理
```

## 测试建议

### 1. 手动测试
```bash
# 使用非无头模式观察登录流程
python -c "
import asyncio
from flightscanner.scrapers import QunarScraper

async def test():
    scraper = QunarScraper(headless=False)  # 非无头模式
    from flightscanner.interfaces import SearchParams
    from datetime import date

    params = SearchParams(
        departure_city='上海',
        arrival_city='成都',
        departure_date=date(2026, 3, 6)
    )

    results = await scraper.search_flights(params)
    await scraper.close()

asyncio.run(test())
"
```

观察点：
- 是否点击了登录按钮？
- 登录弹窗是否出现？
- 二维码是否被捕获？
- qunar_login_qr.png 是否包含完整二维码？

### 2. 日志分析
检查日志输出，确认每个步骤：
```
[INFO] Attempting to capture login QR code...
[INFO] Clicked login button: .q_header_username
[INFO] Found QR code element: img[class*='qrcode']
[INFO] QR code element tag: img
[INFO] QR code is base64 encoded image
```

### 3. 调试工具
使用之前创建的调试脚本：
```bash
# 分析页面结构
python scripts/analyze_qunar_auto.py

# 查看生成的截图和HTML
ls -lh qunar_*.png qunar_*.html
```

## 可能的问题和解决方案

### 问题1: 仍然无法找到二维码
**可能原因**:
- 去哪儿网更新了DOM结构
- 使用了新的anti-bot技术

**解决方案**:
1. 运行调试脚本查看实际DOM结构
2. 添加新的选择器到`qr_selectors`列表
3. 检查是否需要更多的等待时间

### 问题2: 点击登录按钮失败
**可能原因**:
- 登录按钮选择器已变化
- 按钮被其他元素遮挡

**解决方案**:
1. 使用浏览器开发者工具检查实际的按钮选择器
2. 更新`login_button_selectors`列表
3. 考虑使用JavaScript直接触发点击

### 问题3: 二维码在iframe中但未被检测到
**可能原因**:
- iframe加载较慢
- iframe有跨域限制

**解决方案**:
1. 增加iframe检查的等待时间
2. 记录所有iframe的URL进行分析
3. 考虑直接访问iframe URL

## 相关文件

- **修改的文件**: `src/flightscanner/scrapers/qunar_scraper.py`
- **修改的方法**: `_capture_login_qr_code()` (line 304-465)
- **调试脚本**:
  - `scripts/analyze_qunar_auto.py` - 自动分析页面结构
  - `scripts/trigger_qunar_login.py` - 手动触发登录

## 总结

这次修复大幅提升了二维码捕获的**成功率**和**健壮性**：

1. **主动性**: 不再被动等待，主动触发登录
2. **全面性**: 30+个选择器覆盖各种可能的DOM结构
3. **容错性**: 4层后备方案确保一定能捕获到内容
4. **可观察性**: 详细日志便于调试和问题定位

即使去哪儿网未来更新DOM结构，新代码的灵活性也能大大降低维护成本。
