"""周末低价雷达目的地池、签证元数据与视觉元素。

包含国内/国际目的地列表、反高铁排除圈、签证状态、
Emoji 映射、CSS 渐变色背景和目的地风景图 URL。
"""

from typing import Dict, List, Set


# ── 高铁4小时排他圈（从上海出发）──────────────────────────────────────────────
# 这些城市乘高铁4小时内可达，飞机性价比低，排除出推荐池
HSR_EXCLUSION_FROM_SHANGHAI: Set[str] = {
    "杭州", "南京", "苏州", "合肥", "宁波", "无锡", "常州",
    "嘉兴", "绍兴", "金华", "衢州", "湖州", "台州", "扬州",
    "镇江", "泰州", "南通", "徐州", "连云港", "芜湖",
}

# ── 国内目的地池 ───────────────────────────────────────────────────────────────
DOMESTIC_DESTINATIONS: List[str] = [
    "三亚", "成都", "重庆", "昆明", "大理", "丽江", "西安", "桂林",
    "贵阳", "厦门", "青岛", "大连", "沈阳", "哈尔滨", "长沙",
    "武汉", "广州", "深圳", "南宁", "张家界", "西双版纳",
]

# ── 国际/港澳台目的地池（飞行≤4小时）────────────────────────────────────────
INTERNATIONAL_DESTINATIONS: List[str] = [
    "东京", "大阪", "首尔", "济州", "香港", "澳门",
    "曼谷", "新加坡", "吉隆坡", "普吉岛",
]

# ── 过滤后的目的地完整池 ───────────────────────────────────────────────────────
ALL_DESTINATIONS: List[str] = [
    d for d in DOMESTIC_DESTINATIONS if d not in HSR_EXCLUSION_FROM_SHANGHAI
] + INTERNATIONAL_DESTINATIONS

# ── 签证元数据 ─────────────────────────────────────────────────────────────────
VISA_INFO: Dict[str, Dict[str, str]] = {
    "济州":    {"status": "免签",   "label": "🆓 免签说走就走"},
    "香港":    {"status": "免签",   "label": "🆓 港澳通行证直接走"},
    "澳门":    {"status": "免签",   "label": "🆓 港澳通行证直接走"},
    "东京":    {"status": "需签证", "label": "📋 需提前办好日本签证"},
    "大阪":    {"status": "需签证", "label": "📋 需提前办好日本签证"},
    "首尔":    {"status": "需申请", "label": "ℹ️ 需韩签或K-ETA在线申请"},
    "曼谷":    {"status": "落地签", "label": "✅ 落地签，护照直接走"},
    "新加坡":  {"status": "免签",   "label": "🆓 免签30天"},
    "吉隆坡":  {"status": "免签",   "label": "🆓 免签30天"},
    "普吉岛":  {"status": "落地签", "label": "✅ 落地签可办"},
}

# ── 目的地 Emoji 映射 ──────────────────────────────────────────────────────────
DESTINATION_EMOJI: Dict[str, str] = {
    "三亚": "🏖️",   "成都": "🐼",   "重庆": "🌶️",  "昆明": "🌸",
    "大理": "🏔️",   "丽江": "🏯",   "西安": "🏛️",   "桂林": "⛰️",
    "贵阳": "🌿",   "厦门": "🌊",   "青岛": "⛵",   "大连": "🦀",
    "沈阳": "🏙️",   "哈尔滨": "❄️", "长沙": "🍜",   "武汉": "🌉",
    "广州": "🏙️",   "深圳": "🌆",   "南宁": "🌴",   "张家界": "🗻",
    "西双版纳": "🦋",
    "东京": "⛩️",   "大阪": "🦐",   "首尔": "🌃",   "济州": "🍊",
    "香港": "🏙️",   "澳门": "🎰",   "曼谷": "🛕",
    "新加坡": "🦁", "吉隆坡": "🗼", "普吉岛": "🏝️",
    "_default": "✈️",
}

# ── 城市渐变色背景（CSS gradient），用于卡片顶部色块替代图片 ──────────────────
DESTINATION_GRADIENT: Dict[str, str] = {
    "三亚":    "linear-gradient(135deg, #06b6d4, #0891b2)",
    "成都":    "linear-gradient(135deg, #10b981, #059669)",
    "重庆":    "linear-gradient(135deg, #f97316, #ea580c)",
    "昆明":    "linear-gradient(135deg, #a78bfa, #7c3aed)",
    "大理":    "linear-gradient(135deg, #38bdf8, #0284c7)",
    "丽江":    "linear-gradient(135deg, #fb7185, #e11d48)",
    "西安":    "linear-gradient(135deg, #d97706, #b45309)",
    "桂林":    "linear-gradient(135deg, #34d399, #059669)",
    "贵阳":    "linear-gradient(135deg, #4ade80, #16a34a)",
    "厦门":    "linear-gradient(135deg, #22d3ee, #0e7490)",
    "青岛":    "linear-gradient(135deg, #60a5fa, #2563eb)",
    "大连":    "linear-gradient(135deg, #f472b6, #db2777)",
    "沈阳":    "linear-gradient(135deg, #94a3b8, #475569)",
    "哈尔滨":  "linear-gradient(135deg, #e2e8f0, #94a3b8)",
    "长沙":    "linear-gradient(135deg, #fcd34d, #d97706)",
    "武汉":    "linear-gradient(135deg, #c084fc, #9333ea)",
    "广州":    "linear-gradient(135deg, #f97316, #dc2626)",
    "深圳":    "linear-gradient(135deg, #818cf8, #4f46e5)",
    "南宁":    "linear-gradient(135deg, #86efac, #16a34a)",
    "张家界":  "linear-gradient(135deg, #6ee7b7, #059669)",
    "西双版纳":"linear-gradient(135deg, #a3e635, #65a30d)",
    "东京":    "linear-gradient(135deg, #f43f5e, #e11d48)",
    "大阪":    "linear-gradient(135deg, #fb923c, #ea580c)",
    "首尔":    "linear-gradient(135deg, #8b5cf6, #7c3aed)",
    "济州":    "linear-gradient(135deg, #10b981, #059669)",
    "香港":    "linear-gradient(135deg, #f97316, #ea580c)",
    "澳门":    "linear-gradient(135deg, #facc15, #ca8a04)",
    "曼谷":    "linear-gradient(135deg, #eab308, #ca8a04)",
    "新加坡":  "linear-gradient(135deg, #ef4444, #b91c1c)",
    "吉隆坡":  "linear-gradient(135deg, #14b8a6, #0f766e)",
    "普吉岛":  "linear-gradient(135deg, #f472b6, #be185d)",
    "_default": "linear-gradient(135deg, #64748b, #475569)",
}

# ── 目的地风景图 URL（picsum 占位图，seed 固定确保刷新不跳变） ─────────────────
# 可替换为 Unsplash CDN 的真实城市图（格式同 picsum）
DESTINATION_IMAGE: Dict[str, str] = {
    "三亚":     "https://images.unsplash.com/photo-1692963507663-9edee478f46c?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxTYW55YSUyMGJlYWNoJTIwQ2hpbmF8ZW58MHwwfHx8MTc3NjIxODIyM3ww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "成都":     "https://images.unsplash.com/photo-1614357395841-0a3e233e517d?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxDaGVuZ2R1JTIwY2l0eSUyMENoaW5hfGVufDB8MHx8fDE3NzYyMTgyMjR8MA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "重庆":     "https://images.unsplash.com/photo-1740575864268-c9f3b13d1aa3?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxDaG9uZ3FpbmclMjBza3lsaW5lJTIwbmlnaHR8ZW58MHwwfHx8MTc3NjIxODIyNXww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "昆明":     "https://images.unsplash.com/photo-1727360945395-eab8b402415a?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxLdW5taW5nJTIwWXVubmFuJTIwQ2hpbmF8ZW58MHwwfHx8MTc3NjIxODIyN3ww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "大理":     "https://images.unsplash.com/photo-1678620071844-8377e26f1944?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxEYWxpJTIwWXVubmFuJTIwYW5jaWVudCUyMHRvd258ZW58MHwwfHx8MTc3NjIxODIyOHww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "丽江":     "https://images.unsplash.com/photo-1704077393213-ec08ac53e3fe?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxMaWppYW5nJTIwb2xkJTIwdG93biUyMFl1bm5hbnxlbnwwfDB8fHwxNzc2MjE4MjI5fDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "西安":     "https://images.unsplash.com/photo-1725933014999-e70ae6e57375?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxYaWFuJTIwY2l0eSUyMHdhbGwlMjBDaGluYXxlbnwwfDB8fHwxNzc2MjE4MjMxfDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "桂林":     "https://images.unsplash.com/photo-1773318901379-aac92fdf5611?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxHdWlsaW4lMjBrYXJzdCUyMG1vdW50YWluc3xlbnwwfDB8fHwxNzc2MjE4MjMyfDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "贵阳":     "https://images.unsplash.com/photo-1722666079751-d3df5f34442f?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxHdWl5YW5nJTIwY2l0eSUyMENoaW5hfGVufDB8MHx8fDE3NzYyMTgyMzN8MA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "厦门":     "https://images.unsplash.com/photo-1720058842063-51098c2405ef?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxYaWFtZW4lMjBjaXR5JTIwY29hc3QlMjBDaGluYXxlbnwwfDB8fHwxNzc2MjE4MjM1fDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "青岛":     "https://images.unsplash.com/photo-1750602761546-cbae6fd40704?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxRaW5nZGFvJTIwYmVhY2glMjBjaXR5JTIwQ2hpbmF8ZW58MHwwfHx8MTc3NjIxODIzNnww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "大连":     "https://picsum.photos/seed/dalian/400/220",
    "沈阳":     "https://images.unsplash.com/photo-1582133148993-3d601fdab251?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxTaGVueWFuZyUyMGNpdHklMjBDaGluYXxlbnwwfDB8fHwxNzc2MjE4MjM5fDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "哈尔滨":    "https://images.unsplash.com/photo-1552418033-d68b553926d2?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxIYXJiaW4lMjBpY2UlMjBmZXN0aXZhbCUyMENoaW5hfGVufDB8MHx8fDE3NzYyMTgyNDF8MA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "长沙":     "https://images.unsplash.com/photo-1743525237777-d8905a900b1e?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxDaGFuZ3NoYSUyMGNpdHklMjBDaGluYXxlbnwwfDB8fHwxNzc2MjE4MjQyfDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "武汉":     "https://images.unsplash.com/photo-1682082050259-a228a438f3dc?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxXdWhhbiUyMFllbGxvdyUyMENyYW5lJTIwVG93ZXJ8ZW58MHwwfHx8MTc3NjIxODI0NHww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "广州":     "https://images.unsplash.com/photo-1583996829982-823143cc975a?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxHdWFuZ3pob3UlMjBza3lsaW5lJTIwQ2hpbmF8ZW58MHwwfHx8MTc3NjIxODI0NXww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "深圳":     "https://images.unsplash.com/photo-1759970729294-99c7eebeaf54?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxTaGVuemhlbiUyMHNreWxpbmUlMjBuaWdodHxlbnwwfDB8fHwxNzc2MjE4MjQ2fDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "南宁":     "https://images.unsplash.com/photo-1732723336627-d035377534e1?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxOYW5uaW5nJTIwY2l0eSUyMENoaW5hJTIwZ3JlZW58ZW58MHwwfHx8MTc3NjIxODI0OHww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "张家界":    "https://images.unsplash.com/photo-1632377082649-d7c597893d82?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxaaGFuZ2ppYWppZSUyMG1vdW50YWlucyUyMHBpbGxhcnxlbnwwfDB8fHwxNzc2MjE4MjUwfDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "西双版纳":   "https://picsum.photos/seed/xishuangbanna/400/220",
    "东京":     "https://images.unsplash.com/photo-1630736579629-8e5479022796?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxUb2t5byUyMHNreWxpbmUlMjBKYXBhbnxlbnwwfDB8fHwxNzc2MjE4MjUyfDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "大阪":     "https://images.unsplash.com/photo-1704003671790-ab28034a1b24?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxPc2FrYSUyMGNhc3RsZSUyMEphcGFufGVufDB8MHx8fDE3NzYyMTgyNTR8MA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "首尔":     "https://images.unsplash.com/photo-1506816561089-5cc37b3aa9b0?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxTZW91bCUyMGNpdHlzY2FwZSUyMFNvdXRoJTIwS29yZWF8ZW58MHwwfHx8MTc3NjIxODI1NXww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "济州":     "https://images.unsplash.com/photo-1680002529460-b6b5acf0aa37?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxKZWp1JTIwSXNsYW5kJTIwS29yZWF8ZW58MHwwfHx8MTc3NjIxODI1N3ww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "香港":     "https://images.unsplash.com/photo-1533029030467-904d7bbd602b?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxIb25nJTIwS29uZyUyMHNreWxpbmUlMjBoYXJib3J8ZW58MHwwfHx8MTc3NjIxODI1OHww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "澳门":     "https://images.unsplash.com/photo-1641262309325-8b8cf816e099?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxNYWNhdSUyMGNpdHklMjBsaWdodHMlMjBuaWdodHxlbnwwfDB8fHwxNzc2MjE4MjYwfDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "曼谷":     "https://images.unsplash.com/photo-1691488822390-0fd80c389953?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxCYW5na29rJTIwdGVtcGxlJTIwVGhhaWxhbmR8ZW58MHwwfHx8MTc3NjIxODI2MXww&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "新加坡":    "https://images.unsplash.com/photo-1599917858303-0c3c47ccece3?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxTaW5nYXBvcmUlMjBNYXJpbmElMjBCYXklMjBuaWdodHxlbnwwfDB8fHwxNzc2MjE4MjYzfDA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "吉隆坡":    "https://images.unsplash.com/photo-1533118673680-d7eaa85beb24?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxLdWFsYSUyMEx1bXB1ciUyMFBldHJvbmFzJTIwVG93ZXJzfGVufDB8MHx8fDE3NzYyMTgyNjV8MA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "普吉岛":    "https://images.unsplash.com/photo-1704549931312-432d26dd53c3?ixid=M3w5MjUyMzl8MHwxfHNlYXJjaHwxfHxQaHVrZXQlMjBiZWFjaCUyMFRoYWlsYW5kfGVufDB8MHx8fDE3NzYyMTgyNjZ8MA&ixlib=rb-4.1.0&w=800&h=500&fit=crop&q=80",
    "_default": "https://picsum.photos/seed/travel-sky/400/220",
}
