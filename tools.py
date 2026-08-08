# tools.py - AI 客服 Function Calling 工具
# 通过 HTTP 代理调用 E-Shop 后端公开接口（后端零改动）

import json

import requests

# E-Shop 后端地址
BACKEND_BASE = "http://localhost:8080"
# 单次后端请求超时（秒）
TIMEOUT = 5

# ========== 工具定义（Function Calling Schema） ==========

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "搜索本商城在售商品。当用户询问推荐商品、查询某个商品的价格/库存/在售状态时调用。"
                           "支持按关键词、价格区间筛选，按销量/价格排序。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "商品名称关键词，如'跑鞋'、'手机'、'蓝牙耳机'",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "最低价格（元）",
                    },
                    "max_price": {
                        "type": "number",
                        "description": "最高价格（元）",
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["relevant", "sales", "price_asc", "price_desc"],
                        "description": "排序方式：相关度/销量/价格升序/价格降序，默认相关度",
                    },
                    "size": {
                        "type": "integer",
                        "description": "返回商品数量，默认 5，最多 10",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_seckill_sessions",
            "description": "查询当前商城正在进行的秒杀活动场次（含剩余库存）。"
                           "当用户询问'有什么秒杀/优惠活动'、'秒杀活动'时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hot_products",
            "description": "获取本商城的热卖/畅销商品榜单（按销量+评分排序）。"
                           "当用户询问'最近大家在买什么'、'有什么爆款/热卖'、'口碑好的商品'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回商品数量，默认 5，最多 10",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_detail",
            "description": "查询某款商品的详细信息：价格、库存、销量、商家、SKU规格（颜色/尺码等）、商品图片。"
                           "当用户询问'某商品有什么颜色/尺码/配置、还有货吗、长什么样'时，先调用 search_products 定位商品 id，"
                           "再调用本工具获取详情。若用户已提供商品 id 也可直接调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "integer",
                        "description": "商品 id（来自 search_products 返回结果）",
                    },
                },
                "required": ["product_id"],
            },
        },
    },
]


# ========== HTTP 工具函数 ==========

def _http_get(path: str, params: dict) -> dict:
    """请求后端，返回 Result.data；失败抛异常"""
    resp = requests.get(BACKEND_BASE + path, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 200:
        raise RuntimeError(body.get("msg") or f"接口返回异常(code={body.get('code')})")
    return body.get("data")


# ========== 工具实现 ==========

def search_products(keyword=None, min_price=None, max_price=None, sort_by="relevant", size=5):
    """商品搜索/推荐：ES 搜索接口"""
    params = {
        "page": 0,
        "size": min(int(size or 5), 10),
        "sortBy": sort_by or "relevant",
    }
    if keyword:
        params["keyword"] = keyword
    if min_price is not None:
        params["minPrice"] = min_price
    if max_price is not None:
        params["maxPrice"] = max_price

    data = _http_get("/api/product/es/search", params) or {}
    raw_list = data.get("list") or []
    items = []
    for entry in raw_list[:10]:
        p = (entry or {}).get("product") or {}
        items.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "price": float(p.get("price")) if p.get("price") is not None else None,
            "stock": p.get("stock"),
            "sales": p.get("sales"),
            "category": p.get("categoryName"),
            "coverImage": p.get("coverImage"),
        })
    return json.dumps({"total": data.get("total", len(items)), "items": items}, ensure_ascii=False)


def get_seckill_sessions():
    """秒杀场次查询"""
    data = _http_get("/api/seckill/sessions", {})
    if not data:
        return json.dumps({"items": []}, ensure_ascii=False)

    status_text = {0: "未开始", 1: "进行中"}
    items = []
    for s in data:
        items.append({
            "id": s.get("id"),
            "sessionName": s.get("sessionName"),
            "couponName": s.get("couponName"),
            "status": status_text.get(s.get("status"), "未知"),
            "startTime": str(s.get("startTime"))[:19] if s.get("startTime") else None,
            "endTime": str(s.get("endTime"))[:19] if s.get("endTime") else None,
            "seckillStock": s.get("seckillStock"),
            "remainStock": s.get("remainStock"),
            "limitPerUser": s.get("limitPerUser"),
        })
    return json.dumps({"items": items}, ensure_ascii=False)


def get_hot_products(limit=5):
    """热卖榜查询：按销量+评分排序"""
    data = _http_get("/api/product/hot", {"limit": min(int(limit or 5), 10)}) or []
    items = []
    for p in data[:10]:
        items.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "price": float(p.get("price")) if p.get("price") is not None else None,
            "sales": p.get("sales"),
            "avgRating": p.get("avgRating"),
            "coverImage": p.get("coverImage"),
        })
    return json.dumps({"items": items}, ensure_ascii=False)


def get_product_detail(product_id):
    """商品详情查询：基本信息 + SKU 规格 + 商品图片"""
    pid = int(product_id)
    data = _http_get(f"/api/product/{pid}", {})
    if not data:
        return json.dumps({"error": "商品不存在"}, ensure_ascii=False)

    # 合并商品图片列表
    try:
        images = _http_get(f"/api/product/{pid}/images", {}) or []
    except Exception:
        images = []

    # 压缩 SKU：specs 是 JSON 字符串（如 {"颜色":"黑色","尺码":"41"}）
    skus = []
    for s in (data.get("skus") or [])[:10]:
        try:
            specs_obj = json.loads(s.get("specs") or "{}")
        except (json.JSONDecodeError, TypeError):
            specs_obj = {}
        skus.append({
            "specs": specs_obj,
            "price": float(s.get("price")) if s.get("price") is not None else None,
            "stock": s.get("stock"),
            "sales": s.get("sales"),
        })

    item = {
        "id": data.get("id"),
        "name": data.get("name"),
        "price": float(data.get("price")) if data.get("price") is not None else None,
        "stock": data.get("stock"),
        "sales": data.get("sales"),
        "categoryId": data.get("categoryId"),
        "merchantName": data.get("merchantName"),
        "description": (data.get("description") or "")[:100],
        "coverImage": data.get("coverImage"),
        "skus": skus,
        "images": [img.get("imageUrl") for img in images if img.get("imageUrl")][:5],
    }
    return json.dumps(item, ensure_ascii=False)


# 工具名 → 执行函数
TOOL_EXECUTOR = {
    "search_products": search_products,
    "get_seckill_sessions": get_seckill_sessions,
    "get_hot_products": get_hot_products,
    "get_product_detail": get_product_detail,
}


def execute_tool(name: str, arguments: dict) -> str:
    """执行工具，返回给 AI 的紧凑 JSON 字符串（失败返回错误信息）"""
    func = TOOL_EXECUTOR.get(name)
    if not func:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        return func(**(arguments or {}))
    except Exception as e:
        return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)
