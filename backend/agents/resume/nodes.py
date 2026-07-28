# backend/agents/resume/nodes.py（阶段版：前 4 个节点）

import asyncio
from backend.core.llm_factory import get_structured_llm, get_llm
from langchain_core.messages import HumanMessage, SystemMessage
from backend.agents.resume.state import ResumeState, ResumeStructured, DimensionScore, IssueList
from backend.agents.resume.prompts import SYSTEM_PROMPT, EXTRACT_STRUCTURED_PROMPT, DIMENSION_REVIEW_PROMPTS, \
    DIAGNOSE_THINK_PROMPT, DIAGNOSE_ISSUES_PROMPT
from backend.core.logger import get_logger

logger = get_logger(__name__)


# 注：后续节点（4.5 起）会再 import DimensionScore / IssueList / 更多提示词 / get_llm / AsyncSessionLocal


# ── 节点①：upload_to_minio —— 本地模式空跑 ─────────────────────
async def upload_to_minio_node(state: ResumeState) -> dict:
    """文件已在本地临时路径，无需上传对象存储，直接跳过。"""
    logger.info("upload_to_minio.skip", review_id=state["review_id"], reason="local_mode")
    return {}  # 返回空字典：不更新任何 State 字段，流程照常往下走


# ── 节点②：download_pdf —— 本地模式空跑 ───────────────────────
async def download_pdf_node(state: ResumeState) -> dict:
    """文件已在 pdf_local_path，无需从对象存储下载，直接跳过。"""
    logger.info("download_pdf.skip", review_id=state["review_id"], reason="local_file_exists")
    return {}


# ── 节点③：extract_text —— PDF 文本提取 ───────────────────────
def _sync_extract_text(pdf_path: str) -> dict:
    """同步 PDF 文本提取（在线程池中运行）。特别处理双栏简历布局。"""
    import fitz  # PyMuPDF，导入名是 fitz

    doc = fitz.open(pdf_path)  # 打开 PDF
    page_count = len(doc)  # 页数
    all_text_parts = []

    for page in doc:  # 逐页处理
        # get_text("blocks")：每块 = (x0, y0, x1, y1, text, block_no, block_type)
        blocks = page.get_text("blocks")
        text_blocks = [b for b in blocks if b[6] == 0]  # block_type==0 → 文字块（排除图片块）
        if not text_blocks:
            continue

        # 判断双栏：按横坐标 x0 把块分到左半 / 右半
        page_width = page.rect.width
        midpoint = page_width / 2
        left_blocks = [b for b in text_blocks if b[0] < midpoint - 20]  # 偏左（留 20px 容差）
        right_blocks = [b for b in text_blocks if b[0] >= midpoint - 20]  # 偏右
        is_two_column = (  # 左右都够多 + 右侧占比>30% → 双栏
                len(left_blocks) >= 2 and len(right_blocks) >= 2
                and len(right_blocks) / max(len(text_blocks), 1) > 0.3
        )

        if is_two_column:  # 双栏：先读完左栏（按 y 上下），再读右栏
            left_sorted = sorted(left_blocks, key=lambda b: b[1])
            right_sorted = sorted(right_blocks, key=lambda b: b[1])
            page_text = (
                    "\n".join(b[4].strip() for b in left_sorted if b[4].strip())
                    + "\n"
                    + "\n".join(b[4].strip() for b in right_sorted if b[4].strip())
            )
        else:  # 单栏：按 y 从上到下读
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
        raw_text = result["raw_text"]
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
    for attempt in range(2):  # 最多尝试 2 次（结构化输出偶发返回 None）
        try:
            result = await structured_llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            if result is None:  # 模型没调用工具 → 返回 None
                raise ValueError("structured output returned None")
            structured_dict = result.model_dump()  # 转字典存进 State
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("extract_structured.retry", error=str(e))
                await asyncio.sleep(1)
            else:
                logger.warning("extract_structured.failed", error=str(e))

    if structured_dict is None:  # 两次都失败 → 降级为空结构
        structured_dict = ResumeStructured(name="未能提取").model_dump()

    logger.info("extract_structured.done",
                name=structured_dict.get("name", ""),
                projects_count=len(structured_dict.get("projects", [])))
    return {"structured": structured_dict}


SIX_DIMENSIONS = [
    {"key": "project_depth", "name": "项目深度", "weight": 0.30,
     "focus": "项目描述是否有量化数据、技术选型理由、个人贡献、难点解决"},
    {"key": "tech_match", "name": "技术匹配度", "weight": 0.25,
     "focus": "技术栈是否与目标岗位匹配，技能描述是否有层次（熟练/了解/掌握）"},
    {"key": "expression", "name": "表达规范性", "weight": 0.15,
     "focus": "动词开头、STAR 结构、无错别字、无主语省略歧义"},
    {"key": "structure", "name": "简历结构", "weight": 0.15,
     "focus": "模块完整性、排版逻辑、信息密度、重要内容是否放前面"},
    {"key": "quantification", "name": "量化程度", "weight": 0.10,
     "focus": "性能指标、用户量、优化幅度等量化数据的使用情况"},
    {"key": "authenticity", "name": "真实可信度", "weight": 0.05,
     "focus": "表述是否夸大、技术深度描述是否与经验年限匹配、时间线是否合理"},
]


def _build_structured_summary(structured: dict) -> str:
    lines = []
    if structured.get("name"):
        lines.append(f"姓名：{structured['name']}")
    if structured.get("target_position"):
        lines.append(f"求职意向：{structured['target_position']}")
    if structured.get("education"):
        edu = structured["education"][0]
        lines.append(f"最高学历：{edu.get('school', '')} {edu.get('major', '')} {edu.get('degree', '')}")
    if structured.get("skills_list"):
        lines.append(f"技术栈：{', '.join(structured['skills_list'][:10])}")
    if structured.get("projects"):
        proj_names = [p.get("name", "") for p in structured["projects"]]
        lines.append(f"项目数量：{len(structured['projects'])} 个（{', '.join(proj_names[:3])}）")
    if structured.get("work_experience"):
        lines.append(f"工作经历：{len(structured['work_experience'])} 段")
    return "\n".join(lines) if lines else "（结构化提取失败，请基于原文评审）"


def _empty_dimension_score(dim: dict) -> dict:
    return {
        "key": dim["key"],
        "dimension": dim["name"],
        "score": 50,
        "weight": dim["weight"],
        "issues": ["该维度评审失败，建议人工复核"],
        "suggestions": [],
    }

# ──────────────────────────────────────────────────────────────
# 节点⑤：run_six_dimensions —— 六维度并行评审
# ──────────────────────────────────────────────────────────────
async def run_six_dimensions_node(state: ResumeState) -> dict:
    raw_text = state["raw_text"]
    structured = state.get("structured") or {}
    structured_summary = _build_structured_summary(structured)  # 精简摘要，省 token

    async def review_one_dimension(dim: dict) -> dict:
        """评审单个维度，返回一条 DimensionScore dict。内含 2 次重试。"""
        prompt = DIMENSION_REVIEW_PROMPTS[dim["key"]].format(
            resume_text=raw_text[:3000],
            structured_summary=structured_summary,
            focus=dim["focus"],
        )

        for attempt in range(2):  # 最多试 2 次
            try:
                structured_llm = get_structured_llm("resume", DimensionScore)
                result: DimensionScore = await structured_llm.ainvoke([
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ])

                d = result.model_dump()
                d["dimension"] = dim["name"]  # 代码层填：中文维度名
                d["weight"] = dim["weight"]  # 代码层填：权重
                d["key"] = dim["key"]
                return d
            except Exception as e:
                if attempt == 0:
                    await asyncio.sleep(1)  # 第一次失败：等 1 秒重试
                else:
                    return _empty_dimension_score(dim)  # 第二次仍失败：降级为 50 分

    # ── 六维度并行 ──
    tasks = [review_one_dimension(dim) for dim in SIX_DIMENSIONS]
    dimension_scores = await asyncio.gather(*tasks)  # 同时跑 6 个，等全部完成

    # ── 加权综合分 = Σ(各维度得分 × 权重) ──
    weighted_score = sum(d["score"] * d["weight"] for d in dimension_scores)

    return {
        "dimension_scores": list(dimension_scores),
        "weighted_score": round(weighted_score, 2),
    }


# ──────────────────────────────────────────────────────────────
# 节点⑥：diagnose_issues —— 逐条问题诊断
# ──────────────────────────────────────────────────────────────
async def diagnose_issues_node(state: ResumeState) -> dict:
    """汇总六维度问题，Think 前置推理后生成带优先级的诊断清单，并排序。"""
    dimension_scores = state.get("dimension_scores", [])
    raw_text = state["raw_text"]
    structured = state.get("structured") or {}

    # ① 汇总各维度已发现的问题文本
    all_raw_issues = []
    for dim in dimension_scores:
        for issue_text in dim.get("issues", []):
            all_raw_issues.append(f"[{dim['dimension']}] {issue_text}")
    raw_issues_text = "\n".join(f"- {i}" for i in all_raw_issues) or "（暂无）"


    # ② Think 前置推理（自由文本宏观分析；失败不影响主流程）
    reasoning_trace = ""
    try:
        dimension_scores_summary = "\n".join(
            f"- {d['dimension']}：{d['score']}分 — 问题：{', '.join(d.get('issues', [])[:2])}"
            for d in dimension_scores
        )
        think_prompt = DIAGNOSE_THINK_PROMPT.format(
            dimension_scores_summary=dimension_scores_summary,
            raw_issues=raw_issues_text,
        )
        think_llm = get_llm("resume", temperature=0)  # 普通模型（非结构化）
        think_resp = await think_llm.ainvoke([HumanMessage(content=think_prompt)])

        # 兼容不同版本返回：优先 .text（且不是方法），否则用 .content
        reasoning_trace = (
            think_resp.text if hasattr(think_resp, "text") and not callable(think_resp.text)
            else str(think_resp.content)
        ).strip()

        logger.debug("diagnose_think.done", dimensions=len(dimension_scores))
    except Exception as e:
        logger.warning("diagnose_think.failed", error=str(e))

    # 把 Think 结果作为附加上下文拼到诊断提示后面
    think_context = f"\n\n【诊断前宏观分析】\n{reasoning_trace}" if reasoning_trace else ""


    # ③ 结构化生成问题清单（IssueList）
    prompt = DIAGNOSE_ISSUES_PROMPT.format(
        resume_text=raw_text[:3000],
        structured_summary=_build_structured_summary(structured),
        raw_issues=raw_issues_text,
    ) + think_context

    try:
        structured_llm = get_structured_llm("resume", IssueList)
        result: IssueList = await structured_llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        issues = [item.model_dump() for item in result.items]  # 每条 IssueItem 转 dict
    except Exception as e:
        logger.warning("diagnose_issues.failed", error=str(e))
        # 降级：直接用各维度问题，统一标 medium
        issues = [
            {"priority": "medium", "dimension": dim["dimension"], "description": issue,
             "location": "简历全文", "suggestion": "请参考评审建议修改"}
            for dim in dimension_scores
            for issue in dim.get("issues", [])
        ]

    # ④ 按优先级排序：high(0) → medium(1) → low(2)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 2))

    logger.info("diagnose_issues.done",
                total=len(issues),
                high=sum(1 for i in issues if i.get("priority") == "high"))
    return {"issues": issues}


# ── 模块自测：实测 PDF 文本提取（离线，不需大模型）──────────────
if __name__ == "__main__":
    profile_path = "/Users/apple/Desktop/pythonProject/Agent/samples/简历_第7版.pdf"

    state = {"pdf_local_path": profile_path}
    async def main():
        result1 = await extract_text_node(state)
        # print(f"{result1}")
        state.update(result1)

        result2 = await extract_structured_node(state)
        # print(result2)
        state.update(result2)

        result3 = await run_six_dimensions_node(state)
        state.update(result3)

        result4 = await diagnose_issues_node(state)

    asyncio.run(main())