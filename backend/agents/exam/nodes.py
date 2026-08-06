# backend/agents/exam/nodes.py

import asyncio
import json
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from langchain_core.messages import HumanMessage, SystemMessage

from langgraph.types import interrupt

from backend.agents.exam.state import (
    ExamState,
    SubjectiveReviewResult,
    WeakPointsReport,
)
from backend.agents.exam.prompts import (
    SYSTEM_PROMPT,
    SUBJECTIVE_REVIEW_PROMPT,
    SUBJECTIVE_THINK_PROMPT,
    CODE_QUALITY_REVIEW_PROMPT,
    WEAK_POINTS_ANALYSIS_PROMPT,
)
from backend.core.llm_factory import get_llm, get_structured_llm
from backend.core.logger import get_logger
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def _get_message_content(msg) -> str:
    """统一获取消息文本内容(兼容 text 属性和 content 属性)"""
    if hasattr(msg, "text") and not callable(getattr(msg, "text", None)):
        return msg.text
    if isinstance(msg.content, str):
        return msg.content
    return str(msg.content)


def _chinese_to_int(s: str) -> int:
    """中文数字转整数，转换失败时直接 int()，仍失败时返回1"""
    cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    try:
        return int(s)
    except ValueError:
        return cn_map.get(s, 1)


# ──────────────────────────────────────────────────────────────
# 节点1：parse_word — 解析学员作答 Word 文件
# ──────────────────────────────────────────────────────────────

def _sync_parse_word(word_path: str) -> list:
    """
    同步解析 Word 文件(在线程池中运行，避免阻塞事件循环)。

    返回 list[dict]，每个 dict 包含：
        question_no:    题号(int)
        header_text:    原始题目行文本
        student_answer: 学员作答文本(代码题为代码字符串)
        is_code:        True 表示代码题(仅代码题有此字段)
    """
    from docx import Document
    import re

    doc = Document(word_path)
    parsed_questions = []
    current_question = None
    current_answer_lines = []
    in_code_block = False
    code_buffer = []

    for para in doc.paragraphs:
        para_text = para.text.strip()

        # 空行：代码块内保留，其他忽略
        if not para_text:
            if in_code_block:
                code_buffer.append("")
            continue

        # 判断是否是题目开头行(支持 "第X题" / "Q.X" / "题目X")
        is_question_header = re.match(
            r"^(第?\s*[一二三四五六七八九十\d]+\s*[题、。.]|Q\.?\s*\d+|题目\s*\d+)",
            para_text,
            re.IGNORECASE,
        )

        if is_question_header:
            # 保存上一题(如果有)
            if current_question is not None:
                if not current_question.get("is_code"):
                    answer_text = "\n".join(code_buffer) if in_code_block else "\n".join(current_answer_lines)
                    current_question["student_answer"] = answer_text.strip()

                parsed_questions.append(current_question)

            # 提取题号(优先数字，其次中文数字)
            match = re.search(r"[一二三四五六七八九十\d]+", para_text)
            q_no = _chinese_to_int(match.group()) if match else len(parsed_questions) + 1

            current_question = {"question_no": q_no, "header_text": para_text, "student_answer": ""}
            current_answer_lines = []
            code_buffer = []
            in_code_block = False

        elif para_text.startswith("```"):
            # 代码块开关：遇到第二个 ``` 时闭合，保存代码内容
            in_code_block = not in_code_block
            if not in_code_block and current_question:
                current_question["student_answer"] = "\n".join(code_buffer).strip()
                current_question["is_code"] = True

        elif in_code_block:
            # 代码块内部：保留原始缩进(不 strip)
            code_buffer.append(para.text)

        elif current_question is not None:
            # 跳过纯模板提示行(不含实际答案)
            skip_prefixes = ["作答区", "请在此处"]
            if any(para_text.startswith(p) for p in skip_prefixes):
                pass
            else:
                # 提取 "答：X" 格式的答案内容(只取冒号后部分)
                answer_prefixes = ["答：", "答:", "Answer:"]
                extracted = None
                for prefix in answer_prefixes:
                    if para_text.startswith(prefix):
                        rest = para_text[len(prefix):].strip()
                        if rest:
                            extracted = rest
                        break  # 无论是否有内容都不再把整行加入
                if extracted is not None:
                    current_answer_lines.append(extracted)
                elif not any(para_text.startswith(p) for p in answer_prefixes):
                    current_answer_lines.append(para_text)

    # 保存最后一题
    if current_question is not None:
        if not current_question.get("is_code"):
            answer_text = "\n".join(code_buffer) if in_code_block \
                else "\n".join(current_answer_lines)
            current_question["student_answer"] = answer_text.strip()
        parsed_questions.append(current_question)

    return parsed_questions


async def parse_word_node(state: ExamState) -> dict:
    """
    解析学员提交的 Word 试卷文件，提取各题作答内容。

    python-docx 内部有文件 I/O(打开 .docx zip)和 XML 解析(ElementTree)，
    两者都是同步阻塞操作，不能直接在 async 函数里调用。
    用 run_in_executor(None, ...) 放入默认线程池，asyncio 事件循环继续处理
    其他协程，线程完成后 await 恢复。
    """
    word_path = state["word_file_path"]

    try:
        loop = asyncio.get_running_loop()
        parsed_questions = await loop.run_in_executor(None, _sync_parse_word, word_path)

        logger.info(
            "parse_word.done",
            file=word_path,
            questions_found=len(parsed_questions),
        )

        return {"parsed_questions": parsed_questions}

    except Exception as e:
        logger.error("parse_word.failed", error=str(e), file=word_path)
        # 优雅降级：文件损坏或格式不符时，返回空列表。
        # 后续 load_questions_meta_node 从 DB 补全题目信息，
        # student_answer 全部为空字符串，教师人工补批。
        return {"parsed_questions": []}


# backend/agents/exam/nodes.py(接 6.3)

# ──────────────────────────────────────────────────────────────
# 节点2：load_questions_meta — 加载试卷题目元数据
# ──────────────────────────────────────────────────────────────

async def load_questions_meta_node(state: ExamState) -> dict:
    """
    从数据库加载试卷的完整题目元数据(含标准答案、得分点、知识点标签)，
    与解析出的学员答案合并，覆盖写入 parsed_questions。
    """
    exam_id = state["exam_id"]
    parsed = state["parsed_questions"]  # parse_word_node 的输出

    async with AsyncSessionLocal() as session:
        # ── ① 加载题目列表 ──────────────────────────────────────
        result = await session.execute(
            text("""
                 SELECT id,
                        question_no,
                        question_type,
                        content,
                        correct_answer,
                        score,
                        knowledge_tag
                 FROM questions
                 WHERE exam_id = :exam_id
                 ORDER BY question_no
                 """),
            {"exam_id": exam_id},
        )
        questions = result.mappings().all()
        # for i in questions:
        #     print(i)

        # ── ② 加载得分点(仅简答题有)──────────────────────────
        question_ids = [str(q["id"]) for q in questions]
        scoring_points_rows = []
        if question_ids:
            # 动态构造 IN 子句(避免 asyncpg 的 ANY(:qids::uuid[]) 类型不兼容问题)
            param_names = [f":qid_{i}" for i in range(len(question_ids))]
            # print(param_names)
            qid_params = {f"qid_{i}": qid for i, qid in enumerate(question_ids)}
            # print(qid_params)
            sp_result = await session.execute(
                text(f"""
                    SELECT id, question_id, point_desc, point_score
                    FROM scoring_points
                    WHERE question_id IN ({", ".join(param_names)}) AND is_active = TRUE
                    ORDER BY question_id, id
                """),
                qid_params,
            )
            scoring_points_rows = sp_result.mappings().all()

            # for sp in scoring_points_rows:
            #     print(sp)

    # ── ③ 按 question_id 聚合得分点 ─────────────────────────────
    sp_by_question: dict[str, list] = {}
    for sp in scoring_points_rows:
        qid = str(sp["question_id"])
        sp_by_question.setdefault(qid, []).append({
            "id": str(sp["id"]),
            "desc": sp["point_desc"],
            "score": sp["point_score"],
        })

    # ── ④ 以 DB 题目为主，合并解析结果 ─────────────────────────
    parsed_by_no = {p["question_no"]: p for p in parsed}
    merged_questions = []

    for q in questions:
        q_no = q["question_no"]
        merged_questions.append({
            "question_id": str(q["id"]),
            "question_no": q_no,
            "question_type": q["question_type"],
            "content": q["content"],
            "student_answer": parsed_by_no.get(q_no, {}).get("student_answer", ""),
            "correct_answer": q["correct_answer"] or "",
            "scoring_points": sp_by_question.get(str(q["id"]), []),
            "full_score": q["score"],
            "knowledge_tag": q["knowledge_tag"] or "",
        })

    logger.info(
        "load_questions_meta.done",
        exam_id=exam_id,
        total_questions=len(merged_questions),
    )

    return {"parsed_questions": merged_questions}


if __name__ == '__main__':
    # file_path= "/Users/apple/Desktop/pythonProject/Agent/samples/student_answer.docx"
    # res = _sync_parse_word(file_path)
    # for i in res:
    #     print(i)

    # import asyncio
    # file_path = "/Users/apple/Desktop/pythonProject/Agent/samples/student_answer.docx"
    # async def main():
    #     # 解析word文档
    #     state = {"word_file_path": file_path}
    #     result = await parse_word_node(state)
    #     for res in result["parsed_questions"]:
    #         print(res)
    #
    # asyncio.run(main())

    import asyncio
    async def main():
        file_path = "/Users/apple/Desktop/pythonProject/Agent/samples/student_answer.docx"
        state = {"exam_id": "e0000001-0000-0000-0000-000000000001", "word_file_path": file_path}
        result1 = await parse_word_node(state)
        state.update(result1)

        result2 = await load_questions_meta_node(state)
        for res in result2["parsed_questions"]:
            print(res)
            print(" ")


    asyncio.run(main())
