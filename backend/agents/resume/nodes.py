# backend/agents/resume/nodes.py（阶段版：前 4 个节点）

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.resume.state import ResumeState, ResumeStructured
from backend.agents.resume.prompts import SYSTEM_PROMPT, EXTRACT_STRUCTURED_PROMPT
from backend.core.llm_factory import get_structured_llm
from backend.core.logger import get_logger

logger = get_logger(__name__)
# 注：后续节点（4.5 起）会再 import DimensionScore / IssueList / 更多提示词 / get_llm / AsyncSessionLocal


# ── 节点①：upload_to_minio —— 本地模式空跑 ─────────────────────
async def upload_to_minio_node(state: ResumeState) -> dict:
    """文件已在本地临时路径，无需上传对象存储，直接跳过。"""
    logger.info("upload_to_minio.skip", review_id=state["review_id"], reason="local_mode")
    return {}     # 返回空字典：不更新任何 State 字段，流程照常往下走


# ── 节点②：download_pdf —— 本地模式空跑 ───────────────────────
async def download_pdf_node(state: ResumeState) -> dict:
    """文件已在 pdf_local_path，无需从对象存储下载，直接跳过。"""
    logger.info("download_pdf.skip", review_id=state["review_id"], reason="local_file_exists")
    return {}


# ── 节点③：extract_text —— PDF 文本提取 ───────────────────────
def _sync_extract_text(pdf_path: str) -> dict:
    """同步 PDF 文本提取（在线程池中运行）。特别处理双栏简历布局。"""
    import fitz                                       # PyMuPDF，导入名是 fitz

    doc = fitz.open(pdf_path)                         # 打开 PDF
    page_count = len(doc)                             # 页数
    all_text_parts = []

    for page in doc:                                  # 逐页处理
        # get_text("blocks")：每块 = (x0, y0, x1, y1, text, block_no, block_type)
        blocks = page.get_text("blocks")
        text_blocks = [b for b in blocks if b[6] == 0]   # block_type==0 → 文字块（排除图片块）
        if not text_blocks:
            continue

        # 判断双栏：按横坐标 x0 把块分到左半 / 右半
        page_width = page.rect.width
        midpoint   = page_width / 2
        left_blocks  = [b for b in text_blocks if b[0] < midpoint - 20]   # 偏左（留 20px 容差）
        right_blocks = [b for b in text_blocks if b[0] >= midpoint - 20]  # 偏右
        is_two_column = (                              # 左右都够多 + 右侧占比>30% → 双栏
            len(left_blocks) >= 2 and len(right_blocks) >= 2
            and len(right_blocks) / max(len(text_blocks), 1) > 0.3
        )

        if is_two_column:                             # 双栏：先读完左栏（按 y 上下），再读右栏
            left_sorted  = sorted(left_blocks,  key=lambda b: b[1])
            right_sorted = sorted(right_blocks, key=lambda b: b[1])
            page_text = (
                "\n".join(b[4].strip() for b in left_sorted  if b[4].strip())
                + "\n"
                + "\n".join(b[4].strip() for b in right_sorted if b[4].strip())
            )
        else:                                         # 单栏：按 y 从上到下读
            sorted_blocks = sorted(text_blocks, key=lambda b: b[1])
            page_text = "\n".join(b[4].strip() for b in sorted_blocks if b[4].strip())

        all_text_parts.append(page_text)

    doc.close()
    # 多页之间插入分页标记，便于后续区分
    raw_text = "\n\n---PAGE BREAK---\n\n".join(all_text_parts)
    return {"raw_text": raw_text, "page_count": page_count}


async def extract_text_node(state: ResumeState) -> dict:
    """异步节点：用线程池跑同步的 PDF 解析，避免阻塞事件循环。"""
    pdf_path = state["pdf_local_path"]
    try:
        loop = asyncio.get_running_loop()
        # run_in_executor：把同步阻塞的解析丢到线程池（回顾 2.1.4）
        result = await loop.run_in_executor(None, _sync_extract_text, pdf_path)
        raw_text   = result["raw_text"]
        page_count = result["page_count"]

        # 兜底：文字过少多半是扫描件/图片 PDF（不引入 OCR 重依赖，仅告警）
        if len(raw_text.strip()) < 200:
            logger.warning("extract_text.text_too_short", text_length=len(raw_text.strip()))

        logger.info("extract_text.done", page_count=page_count, text_length=len(raw_text))
        return {"raw_text": raw_text, "page_count": page_count}
    except Exception as e:
        logger.error("extract_text.failed", error=str(e))
        raise


# ── 节点④：extract_structured —— LLM 结构化提取 ───────────────
async def extract_structured_node(state: ResumeState) -> dict:
    """用 LLM Function Calling 把原始文本提取成结构化简历（ResumeStructured）。"""
    raw_text = state["raw_text"]
    # 超长截断：截前 4000 字，防止超出 context 并省 token
    text_for_llm = raw_text[:4000] if len(raw_text) > 4000 else raw_text
    prompt = EXTRACT_STRUCTURED_PROMPT.format(resume_text=text_for_llm)

    # 拿一个绑定了 ResumeStructured 结构的模型（3.4 工厂 + 2.3 结构化输出）
    structured_llm = get_structured_llm("resume", ResumeStructured)

    structured_dict = None
    for attempt in range(2):                          # 最多尝试 2 次（结构化输出偶发返回 None）
        try:
            result = await structured_llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            if result is None:                        # 模型没调用工具 → 返回 None
                raise ValueError("structured output returned None")
            structured_dict = result.model_dump()     # 转字典存进 State
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("extract_structured.retry", error=str(e))
                await asyncio.sleep(1)
            else:
                logger.warning("extract_structured.failed", error=str(e))

    if structured_dict is None:                       # 两次都失败 → 降级为空结构
        structured_dict = ResumeStructured(name="未能提取").model_dump()

    logger.info("extract_structured.done",
                name=structured_dict.get("name", ""),
                projects_count=len(structured_dict.get("projects", [])))
    return {"structured": structured_dict}


# ── 模块自测：实测 PDF 文本提取（离线，不需大模型）──────────────
if __name__ == "__main__":
    # import fitz
    #
    # # 造一个简历样例 PDF
    # _doc = fitz.open()
    # _page = _doc.new_page()
    # _page.insert_text((50, 60),
    #     "张三  后端开发工程师\n邮箱：zhangsan@example.com\n求职意向：Java 后端开发\n\n"
    #     "技能\n熟悉 Java、Spring Boot、MySQL、Redis\n\n"
    #     "项目经历\n电商系统  后端开发  2023.06 - 2023.12\n"
    #     "负责订单与库存模块，使用 Spring Boot + Redis，QPS 提升 30%",
    #     fontsize=11, fontname="china-s")
    # _doc.save("/tmp/sample_resume.pdf")
    # _doc.close()

    # # 跑真实的 extract_text_node
    # _result = asyncio.run(extract_text_node({"pdf_local_path": "/tmp/sample_resume.pdf"}))
    # print("page_count:", _result["page_count"])
    # print("raw_text 长度:", len(_result["raw_text"]))
    # print("含 Spring Boot:", "Spring Boot" in _result["raw_text"])
    # print("含 QPS 提升 30%:", "QPS 提升 30%" in _result["raw_text"])


    profile_path = "/Users/apple/Desktop/pythonProject/Agent/samples/简历_第7版.pdf"
    res = _sync_extract_text(profile_path)
    print(res)
