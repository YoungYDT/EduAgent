import uvicorn
from fastapi import FastAPI
from backend.api.v1 import auth,resume
from contextlib import asynccontextmanager
from backend.mcp.knowledge_base_server import mcp as kb_mcp
from backend.mcp.web_search_server import mcp as ws_mcp

@asynccontextmanager
async def lifespan(app: FastAPI):
    # app.mount() 不会自动传播子应用 lifespan，必须在这里手动启动
    # _session_manager.run() 初始化 anyio task group，MCP 请求依赖它
    async with kb_mcp._session_manager.run():
        async with ws_mcp._session_manager.run():
            yield



app = FastAPI(lifespan=lifespan)

app.include_router(auth.router, prefix="/auth", tags=["auth"])

app.include_router(resume.router, prefix="/resume", tags=["resume"])

app.mount("/mcp/kb",     kb_mcp.streamable_http_app())
app.mount("/mcp/web-search", ws_mcp.streamable_http_app())


if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8000)