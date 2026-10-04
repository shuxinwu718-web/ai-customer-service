import json
import os
import time
from collections import defaultdict, deque
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from dashscope import Generation

from system_prompt import build_system_prompt
from tools import TOOLS, execute_tool, set_auth_token
from intent import detect_intent, prefetch_data

# 加载环境变量
load_dotenv()

app = FastAPI(title="AI 客服系统")

# 允许跨域（让前端项目能调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 请求/响应模型 ==========
class HistoryItem(BaseModel):
    role: str  # "user" 或 "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[HistoryItem]] = None
    token: str = ""  # 登录用户 JWT（用于查询我的订单/物流/退款）


class ChatResponse(BaseModel):
    reply: str
    success: bool = True
    error: Optional[str] = None


# ========== 频率限制（IP 维度，内存实现） ==========
RATE_LIMIT_PER_MINUTE = 10  # 每分钟最多 10 条
_rate_records: "defaultdict[str, deque]" = defaultdict(deque)


def check_rate_limit(client_ip: str) -> bool:
    """每分钟最多 RATE_LIMIT_PER_MINUTE 条，超限返回 False"""
    now = time.time()
    records = _rate_records[client_ip]
    # 清理 60 秒之前的记录
    while records and records[0] <= now - 60:
        records.popleft()
    if len(records) >= RATE_LIMIT_PER_MINUTE:
        return False
    records.append(now)
    return True


# ========== AI 调用函数（Agent 循环） ==========
MAX_TOOL_ROUNDS = 3  # 单次提问最多工具调用轮数


def chat_agent(message: str, history: Optional[List[HistoryItem]] = None, token: str = "") -> str:
    """调用通义千问（系统提示词 + 多轮上下文 + 意图预取 + Function Calling 工具）"""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    model = os.getenv("MODEL_NAME", "qwen-turbo")

    if not api_key:
        return "错误：未配置 API Key"

    # 本次请求身份（订单/物流/退款工具使用）
    set_auth_token(token or "")

    # 进 LLM 前规则判定意图并预取真实数据（模型自选工具不可靠，命中后强制基于真实数据回答）
    system_content = build_system_prompt()
    injected = prefetch_data(detect_intent(message), message)
    if injected:
        system_content += "\n\n" + injected

    # 组装 messages：[system, ...历史对话(最多最近10轮), 当前问题]
    messages = [{"role": "system", "content": system_content}]
    if history:
        for item in history[-10:]:
            if item.role in ("user", "assistant") and item.content.strip():
                messages.append({"role": item.role, "content": item.content})
    messages.append({"role": "user", "content": message})

    # Function Calling 循环
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = Generation.call(
                model=model,
                messages=messages,
                tools=TOOLS,
                result_format="message",  # 必须：工具调用信息在 choices[0].message.tool_calls
                api_key=api_key,
            )
        except Exception as e:
            return f"发生错误: {str(e)}"

        if response.status_code != 200:
            return f"AI 调用失败: {response.message}"

        output = response.output  # DictMixin（dict 子类）
        choices = output.get("choices") or []
        first_message = choices[0].get("message") or {} if choices else {}
        tool_calls = first_message.get("tool_calls") or []
        if tool_calls:
            # 先回传 assistant(tool_calls) 消息，再追加各工具结果（DashScope 多轮工具调用要求）
            messages.append(first_message)
            for tc in tool_calls:
                fn = tc.get("function") or {}
                try:
                    arguments = json.loads(fn.get("arguments")) if fn.get("arguments") else {}
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                result = execute_tool(fn.get("name"), arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": result,
                })
            continue

        # 无工具调用 → 最终回复
        text = output.get("text")
        if not text:
            text = first_message.get("content")
        if text:
            return text
        return "抱歉，暂时无法理解你的问题，请换个方式再试一次。"

    return "抱歉，暂时无法完成你的请求，请换个方式再试一次。"


# ========== API 接口 ==========
@app.get("/")
async def root():
    return {"service": "AI 客服", "status": "running"}


@app.post("/ai/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, req: Request):
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    # IP 限流兜底
    client_ip = req.client.host if req.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="提问太频繁，请稍后再试")

    # 输入限长：超长自动截断
    message = request.message.strip()[:200]
    reply = chat_agent(message, request.history, request.token)
    return ChatResponse(reply=reply)


@app.get("/ai/health")
async def health():
    return {"status": "healthy"}


# ========== 启动服务 ==========
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
