# AI 客服服务（Python）

> 基于 FastAPI 构建的智能客服 Agent 服务，为 E-Shop 电商系统提供 AI 问答能力。

| 项目 | 仓库地址 |
|------|----------|
| 🖥️ **前端** | [Eshop_front](https://github.com/shuxinwu718-web/Eshop_front) |
| ☕ **后端（Java）** | [Eshop](https://github.com/shuxinwu718-web/Eshop) |
| 🐍 **AI 客服（本项目）** | [ai-customer-service](https://github.com/shuxinwu718-web/ai-customer-service) |

## 功能概述

- 基于大语言模型的智能问答
- 支持电商场景常见问题解答（商品咨询、订单查询、退换货等）
- 提供 SSE 流式响应，支持打字机效果
- 可对接 E-Shop 后端获取订单、商品等实时数据

## 技术栈

| 类别 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| 语言 | Python 3.10+ |
| AI 框架 | 可对接 OpenAI API / 其他 LLM |
| 异步支持 | Uvicorn + asyncio |

## 项目结构

```
ai-customer-service/
├── main.py              # FastAPI 入口
├── faq.py               # FAQ 知识库
├── system_prompt.py     # 系统提示词配置
├── tools.py             # Agent 工具函数
├── test_ai.py           # 测试脚本
├── requirements.txt     # Python 依赖
├── .env.example         # 环境变量模板
├── .env                 # 环境变量（含 API Key，不上传）
└── venv/                # 虚拟环境（不上传）
```

## 快速开始

### 环境要求

- Python 3.10 或更高版本

### 安装与启动

```bash
# 1. 克隆项目
git clone https://github.com/shuxinwu718-web/ai-customer-service.git
cd ai-customer-service

# 2. 创建并激活虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入你的 API Key 等配置

# 5. 启动服务
python main.py
# 或使用 uvicorn
uvicorn main:app --reload --port 5000
```

### 环境变量说明

| 变量 | 说明 | 示例 |
|------|------|------|
| `OPENAI_API_KEY` | LLM API 密钥 | `sk-xxx...` |
| `MODEL_NAME` | 使用的模型名称 | `gpt-4o-mini` |
| `API_BASE_URL` | API 接口地址（可选） | `https://api.openai.com/v1` |

## API 接口

### 对话接口

```
POST /chat
```

请求体：

```json
{
  "message": "用户提问内容",
  "session_id": "会话ID（可选）"
}
```

响应（SSE 流式）：

```
data: {"content": "回答片段", "finish": false}
...
data: {"content": "", "finish": true}
```

### 健康检查

```
GET /health
```

返回：

```json
{"status": "ok"}
```

## 与 E-Shop 前端联调

前端项目 `.env.development` 中配置 AI 服务地址：

```bash
VITE_APP_AI_API_URL = http://localhost:5000
```

启动 AI 服务后，前端 `/ai` 请求会自动代理到该服务。

## 开发说明

### 扩展 FAQ 知识库

编辑 `faq.py`，添加新的问答对：

```python
FAQ_DATA = {
    "常见问题": [
        {"q": "问题描述", "a": "答案内容"},
    ]
}
```

### 自定义系统提示词

编辑 `system_prompt.py`，修改 `SYSTEM_PROMPT` 变量。

### 添加工具函数

在 `tools.py` 中注册新的工具函数，供 Agent 调用。

## License

MIT
