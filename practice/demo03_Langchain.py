import asyncio
import os
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

load_dotenv()  # 从 .env 文件加载环境变量

llm = init_chat_model(
    model="deepseek-chat",  # 模型名称
    model_provider="openai",  # 走 OpenAI 兼容协议（关键）
    api_key=os.getenv("DEEPSEEK_API_KEY"),  # 从环境变量读取 API Key
    base_url="https://api.deepseek.com/v1",  # DeepSeek 接口地址（关键）
    temperature=0,  # 输出的随机性，0 = 最稳定
)

# ==============================================================================

# async def main():
#     messages = [
#         SystemMessage(content="你是一位专业的 Python 讲师，用一句话回答。"),
#         HumanMessage(content="什么是装饰器。")
#     ]
#     res = await llm.ainvoke(messages)
#     print(res.content)
#
# asyncio.run(main())
# ==============================================================================


# class PersonInfo(BaseModel):
#     name: str = Field(description="名字")
#     age: int = Field(description="年龄")
#     city: str = Field(description="城市")
#
#
# strudture_llm = llm.with_structured_output(PersonInfo, method="function_calling")
#
#
# async def main():
#     messahes = [
#         SystemMessage(content="你负责从文本中抽取人物信息。"),
#         HumanMessage(content="我叫小明，今年 25 岁，住在上海。")
#     ]
#
#     res: PersonInfo = await strudture_llm.ainvoke(messahes)
#     print(type(res))
#     print(res.name)
#     print(res.age)
#     print(res.city)
#     print(res.model_dump())
#
# asyncio.run(main())


# ==============================================================================


async def main():
    messages=[HumanMessage(content="请用三句话介绍一下 python")]
    async for chunk in llm.astream(messages):
        print(chunk.content, end='', flush=True)

asyncio.run(main())


# ==============================================================================