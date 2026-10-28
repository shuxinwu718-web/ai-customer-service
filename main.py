import json
import os
import time
from collections import defaultdict, deque
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI

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


# ========== AI 调用（DeepSeek，OpenAI 兼容接口） ==========
MAX_TOOL_ROUNDS = 3  # 单次提问最多工具调用轮数


def build_client() -> Optional[OpenAI]:
    """DeepSeek 走 OpenAI 兼容协议，默认地址 https://api.deepseek.com"""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=60,
    )


def model_name() -> str:
    return os.getenv("MODEL_NAME", "deepseek-chat")


def friendly_error(error: Exception) -> str:
    """把供应商错误转成用户能看懂的提示，避免把密钥等细节抛给前端"""
    text = str(error)
    lowered = text.lower()
    if "401" in text or "invalid_api_key" in lowered or "authentication" in lowered:
        return "AI 密钥无效，请检查 DEEPSEEK_API_KEY 配置"
    if "429" in text or "rate limit" in lowered:
        return "请求过于频繁，请稍后再试"
    if "timeout" in lowered or "timed out" in lowered:
        return "AI 服务响应超时，请稍后再试"
    return text


def tool_calls_payload(message) -> list:
    """OpenAI 协议要求回传 assistant 消息里的 tool_calls 结构"""
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in (message.tool_calls or [])
    ]


def run_tool_calls(message) -> list:
    """执行模型请求的全部工具，返回可直接追加进 messages 的 tool 结果"""
    results = []
    for tc in message.tool_calls or []:
        try:
            arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        results.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": execute_tool(tc.function.name, arguments),
            }
        )
    return results


def build_messages(message: str, history: Optional[List[HistoryItem]], token: str) -> list:
    """系统提示词 + 意图预取数据 + 最近 10 轮历史 + 当前问题"""
    set_auth_token(token or "")

    system_content = build_system_prompt()
    # 进 LLM 前规则判定意图并预取真实数据（模型自选工具不可靠，命中后强制基于真实数据回答）
    injected = prefetch_data(detect_intent(message), message)
    if injected:
        system_content += "\n\n" + injected

    messages = [{"role": "system", "content": system_content}]
    if history:
        for item in history[-10:]:
            if item.role in ("user", "assistant") and item.content.strip():
                messages.append({"role": item.role, "content": item.content})
    messages.append({"role": "user", "content": message})
    return messages


def chat_agent(message: str, history: Optional[List[HistoryItem]] = None, token: str = "") -> str:
    """非流式：系统提示词 + 多轮上下文 + 意图预取 + Function Calling"""
    client = build_client()
    if not client:
        return "错误：未配置 API Key"

    messages = build_messages(message, history, token)

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=model_name(),
                messages=messages,
                tools=TOOLS,
            )
        except Exception as e:
            return f"AI 调用失败: {friendly_error(e)}"

        reply = response.choices[0].message if response.choices else None
        if reply is None:
            return "抱歉，暂时无法理解你的问题，请换个方式再试一次。"

        if reply.tool_calls:
            # 先回传 assistant(tool_calls)，再追加工具结果
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.content or "",
                    "tool_calls": tool_calls_payload(reply),
                }
            )
            messages.extend(run_tool_calls(reply))
            continue

        text = (reply.content or "").strip()
        return text or "抱歉，暂时无法理解你的问题，请换个方式再试一次。"

    return "抱歉，暂时无法完成你的请求，请换个方式再试一次。"


def chat_agent_stream(message: str, history: Optional[List[HistoryItem]] = None, token: str = ""):
    """流式版：工具轮非流式判断，最终回答轮以增量流式输出（SSE）"""
    client = build_client()
    if not client:
        yield "错误：未配置 API Key"
        return

    messages = build_messages(message, history, token)

    # 1) 工具轮（非流式）：判断是否有 tool_calls，有则执行工具并回传结果
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=model_name(),
                messages=messages,
                tools=TOOLS,
            )
        except Exception as e:
            yield f"AI 调用失败: {friendly_error(e)}"
            return

        reply = response.choices[0].message if response.choices else None
        if reply is None or not reply.tool_calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": reply.content or "",
                "tool_calls": tool_calls_payload(reply),
            }
        )
        messages.extend(run_tool_calls(reply))
    else:
        yield "抱歉，暂时无法完成你的请求，请换个方式再试一次。"
        return

    # 2) 最终回答轮：流式输出。此处不再带 tools，避免流式轮又要请求工具
    try:
        stream = client.chat.completions.create(
            model=model_name(),
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
    except Exception as e:
        yield f"AI 调用失败: {friendly_error(e)}"


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


@app.post("/ai/chat/stream")
async def chat_stream(request: ChatRequest, req: Request):
    """SSE 流式对话接口：data: {"delta": "..."} 增量块 + 结尾 data: {"done": true}"""
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    client_ip = req.client.host if req.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="提问太频繁，请稍后再试")

    message = request.message.strip()[:200]

    def event_gen():
        for piece in chat_agent_stream(message, request.history, request.token):
            payload = json.dumps({"delta": piece}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/ai/health")
async def health():
    return {"status": "healthy"}


# ========== 启动服务 ==========
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
