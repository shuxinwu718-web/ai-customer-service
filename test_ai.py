import os
from dotenv import load_dotenv
import dashscope
from dashscope import Generation

# 加载 .env 文件中的环境变量
load_dotenv()

# 获取 API Key
api_key = os.getenv('DASHSCOPE_API_KEY')
model = os.getenv('MODEL_NAME', 'qwen-turbo')

print(f"使用模型: {model}")
print(f"API Key: {api_key[:20]}... (已隐藏)")

# 调用 AI
response = Generation.call(
    model=model,
    prompt="你好，请用一句话介绍一下你自己",
    api_key=api_key  # 显式传入
)

print("\n=== AI 回复 ===")
print(response)