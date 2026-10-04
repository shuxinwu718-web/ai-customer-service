# intent.py - 在调用 LLM 前用规则判定用户意图，并预取真实数据注入提示
# 背景：大模型临时决定是否调用工具不可靠（容易拒答或编造数据），
# 因此对高频意图先按规则命中并预取真实数据注入 system 提示，强制模型基于真实数据回答；
# Function Calling 仍保留作为追问（如某商品详情/某订单物流轨迹）的兜底。

import json
import re

from tools import (
    get_hot_products,
    get_my_orders,
    get_seckill_sessions,
    search_products,
    _truncate,
)

# ========== 意图判定（优先级从高到低，先命中先预取） ==========

# "如何/怎么/流程" 类提问属于流程说明，走知识库/LLM 回答，不预取数据
HOW_QUESTION_RE = r"如何|怎么|怎样|流程|步骤|多久|多长时间|需要(什么|哪些)"

SEC_KILL_RE = r"秒杀|限时抢购|抢购|优惠活动|促销|特价"
HOT_RE = r"热卖|爆款|畅销|口碑|大家都在买|大家(在|都)买|销量.{0,4}(高|榜)|卖得.{0,4}好|人气"
RECOMMEND_RE = r"推荐|性价比|帮我选|帮我买|有什么(商品|好物|东西|推荐)|有哪些(商品|好物|东西)|想买|买什么|预算|元\s*(以内|以下|内|之内|不超过)"
REFUND_RE = r"退款"
TRACKING_RE = r"物流|快递|运单|发货.{0,6}(了吗|没有|状态|进度|到哪)|货.{0,4}(到哪|发了吗)"
ORDERS_RE = r"订单|我买了|我买过|查.{0,2}(我的|一下).{0,2}买"


def detect_intent(message: str) -> str:
    """返回意图标识：seckill / hot / recommend / refund / tracking / orders / none"""
    text = (message or "").strip()
    if re.search(HOW_QUESTION_RE, text):
        return "none"
    if re.search(SEC_KILL_RE, text):
        return "seckill"
    if re.search(HOT_RE, text):
        return "hot"
    if re.search(RECOMMEND_RE, text):
        return "recommend"
    if re.search(REFUND_RE, text):
        return "refund"
    if re.search(TRACKING_RE, text):
        return "tracking"
    if re.search(ORDERS_RE, text):
        return "orders"
    return "none"


# ========== 参数提取 ==========

def _extract_budget(message: str):
    """提取预算上限（元）"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*元\s*(以内|以下|内|之内|预算|不超过|少于|低于)", message)
    if m:
        return float(m.group(1))
    m = re.search(r"预算\s*(\d+(?:\.\d+)?)", message)
    return float(m.group(1)) if m else None


# 常见商品类目白名单（防止把"推荐/商品"这类意图词当成搜索关键词）
_CATEGORY_WORDS = [
    "手机", "耳机", "蓝牙耳机", "键盘", "鼠标", "电脑", "笔记本", "显示器", "平板",
    "电视", "冰箱", "空调", "洗衣机", "音响", "音箱", "相机", "打印机", "路由器",
    "充电器", "充电宝", "数据线", "水杯", "保温杯", "零食", "咖啡", "茶叶", "牛奶",
    "面膜", "护肤品", "口红", "香水", "篮球", "足球", "羽毛球", "乒乓球", "跳绳",
    "哑铃", "瑜伽垫", "跑鞋", "运动鞋", "球鞋", "衣服", "卫衣", "T恤", "衬衫",
    "外套", "裤子", "裙子", "背包", "行李箱", "手表", "项链", "戒指",
]


def _extract_keyword(message: str):
    for w in _CATEGORY_WORDS:
        if w in message:
            return w
    return None


# ========== 预取真实数据 ==========

def prefetch_data(intent: str, message: str) -> "str | None":
    """命中意图后预取真实数据，返回注入 system 的文本；失败或无需预取返回 None"""
    try:
        if intent == "seckill":
            raw = get_seckill_sessions()
            if not json.loads(raw).get("items"):
                return "【当前商城没有进行中的秒杀/优惠活动，请如实告知用户】"
            return _inject("当前秒杀/优惠活动", raw)

        if intent == "hot":
            return _inject("热卖榜", get_hot_products(limit=5))

        if intent == "recommend":
            budget = _extract_budget(message)
            keyword = _extract_keyword(message)
            raw = search_products(keyword=keyword, max_price=budget, sort_by="sales", size=5)
            if not json.loads(raw).get("items"):
                return "【当前没有符合该条件的在售商品，请如实告知用户】"
            return _inject("符合条件的商品", raw)

        if intent in ("orders", "tracking", "refund"):
            # 订单/物流/退款类问题：预取最近订单（含物流单号、退款单号）；
            # 具体某单轨迹/退款进度仍可由模型调用 get_tracking/get_refund_progress 进一步查询
            raw = get_my_orders(limit=5)
            if not json.loads(raw).get("items"):
                return "【该用户暂无订单，请如实告知】"
            return _inject("该用户最近订单", raw)
    except Exception:
        # 预取失败不阻断对话，退化为普通回答
        return None
    return None


def _inject(title: str, raw: str) -> str:
    """组装注入文本（截断防数据内嵌指令）"""
    return f"【{title}真实数据（请直接基于以下数据回答，不要编造，不要重复调用已预取的工具）】\n{_truncate(raw)}"
