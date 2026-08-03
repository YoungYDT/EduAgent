import asyncio, sys
from backend.mcp.client import call_mcp_tool

async def main():
    base = "http://localhost:8001"          # 独立模式：直接指向端口，无路径前缀
    results = await call_mcp_tool(
        server_url=base,
        tool_name="search_knowledge_base",
        arguments={"query": "跨境电商项目具体内容讲的是什么", "tenant_id": "tenant_default"},
    )
    print("*"*80)
    print(f"命中 {len(results)} 条")
    print(results)
    print(type(results))

asyncio.run(main())