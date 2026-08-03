# backend/agents/qa/nodes.py

import asyncio
import uuid

from sqlalchemy import text
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from backend.agents.qa.state import QAState
from backend.agents.qa.prompts import (
    HYDE_PROMPT,
    MULTI_QUERY_REWRITE_PROMPT,
    RAG_ANSWER_PROMPT,
    DIRECT_ANSWER_PROMPT,
    GENERAL_ANSWER_PROMPT,
    RAG_STRATEGY_PROMPT,
    SYSTEM_PROMPT,
)
from backend.core.llm_factory import get_llm
from backend.core.memory import (
    trim_messages_to_window,
    should_trigger_summary,
    compress_to_summary,
    build_thread_id,
)
from backend.config import get_settings
from backend.core.logger import get_logger
from backend.core.query_classifier import get_query_classifier

logger = get_logger(__name__)

# ── 检索相关常量 ───────────────────────────────────────────────
MAX_BROAD_QUERIES        = 3   # BROAD 分支最多并行的子 Query 数
RECALL_TOP_K_PRECISE     = 8   # PRECISE：直接检索召回数
RECALL_TOP_K_VAGUE       = 10  # VAGUE：HyDE 语义扩充后多召回些
RECALL_TOP_K_BROAD_PER   = 4   # BROAD：每个子 Query 的召回数
RERANK_TOP_K             = 3   # 精排后保留的最终 chunk 数


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def _get_message_content(msg) -> str:
    """统一获取消息文本(兼容 .text 属性和 .content 属性)"""
    if hasattr(msg, "text") and not callable(getattr(msg, "text", None)):
        return msg.text
    if isinstance(msg.content, str):
        return msg.content
    return str(msg.content)


def _format_history_for_prompt(messages: list) -> str:
    """把最近几轮对话格式化为 Prompt 可用的文本"""
    lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            lines.append(f"学员：{_get_message_content(msg)}")
        elif isinstance(msg, AIMessage):
            # AI 回答截断，避免 Prompt 过长
            lines.append(f"AI：{_get_message_content(msg)[:200]}...")
    return "\n".join(lines) if lines else "(无历史对话)"


_WEEKDAYS_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

def _current_datetime_str() -> str:
    """返回格式化的当前时间字符串，供注入 Prompt 使用"""
    from datetime import datetime
    now = datetime.now()
    weekday = _WEEKDAYS_CN[now.weekday()]
    return now.strftime(f"%Y年%m月%d日 {weekday} %H:%M")


def _build_system_content(summary: str | None = None) -> str:
    """
    构建注入了当前时间的 SystemMessage 内容，所有生成节点共用。
    summary 非空时追加历史学习摘要，帮助 LLM 保持多轮上下文连贯。
    """
    content = SYSTEM_PROMPT + f"\n\n【当前时间】{_current_datetime_str()}"
    if summary:
        content += f"\n\n【学员历史学习摘要】\n{summary}"
    return content



# ──────────────────────────────────────────────────────────────
# 联网请求自动识别
# ──────────────────────────────────────────────────────────────

_WEB_SEARCH_HINTS = (
    "联网", "联网查询", "联网搜索", "上网查", "上网搜", "网上搜",
    "搜索一下", "可以搜索", "帮我搜", "百度一下", "谷歌一下",
)

def _extract_query_and_web_flag(raw: str) -> tuple[str, bool]:
    """
    检测用户消息是否含联网请求指令。
    返回 (清洗后的问题, 是否自动启用联网)。

    联网指令部分被去除，防止被 LLM 当作问题内容处理。
    例："Spring AOP 是什么，如果不知道可以联网搜索"
      → ("Spring AOP 是什么", True)
    """
    import re
    needs_web = any(h in raw for h in _WEB_SEARCH_HINTS)
    if not needs_web:
        return raw, False
    clean = re.sub(
        r'[，,。.？?！!\s]*(?:如果不知道|不知道的话|不清楚|你可以|可以)?'
        r'(?:联网|上网|网上)?(?:查询|搜索|搜一下|查一查|百度|谷歌).*$',
        '', raw,
    ).strip()
    return (clean or raw), True
