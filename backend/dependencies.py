# backend/dependencies.py
# FastAPI 依赖注入：数据库连接（鉴权部分见 3.6）

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from backend.config import get_settings

settings = get_settings()

# ── PostgreSQL 异步连接池 ──────────────────────────────────────
engine = create_async_engine(
    settings.database_url,        # 来自 config.py（3.3），最终来自 .env.local（3.1）
    pool_size=10,                 # 连接池基础大小
    max_overflow=20,              # 高峰时最多再额外开 20 个连接
    echo=False,                   # 改 True 会打印所有 SQL，调试时可临时打开
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：获取异步数据库会话，自动提交 / 回滚 / 关闭"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
