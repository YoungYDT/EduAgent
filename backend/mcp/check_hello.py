import asyncio

from backend.mcp.client import call_mcp_tool, list_mcp_tools

async def main():
    base = "http://localhost:8009"
    tools = await list_mcp_tools(base)
    print(f"tools: {tools}")
    print(tools[0]["name"])
    print(f"tools: {len(tools)}")
    result = await call_mcp_tool(base, "add", {"a": 3, "b": 5})
    print("add(3, 5) =", result)      # 预期：8

asyncio.run(main())