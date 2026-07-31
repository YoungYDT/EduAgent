# scripts/build_knowledge_base.py(阶段版：仅文档加载部分)
# 5.3 补充分块，5.4 补充嵌入，5.5 完整版

import uuid
from pathlib import Path
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
    MarkdownTextSplitter,
)
from backend.core.knowledge_base import BGEMEmbedder, DocumentChunk, generate_chunk_id



def load_pdf(file_path: str) -> list[Document]:
    """
    加载 PDF 文档，每页返回一个 Document。

    只提取文字层内容；图片/扫描件页面 page_content 为空字符串，
    不报错(在 5.3 分块时会过滤掉空页)。

    Args:
        file_path: PDF 文件的本地路径

    Returns:
        list[Document]，每个 Document 对应一页
        metadata 包含 source(文件路径)和 page(页码，从 0 开始)
    """
    loader = PyPDFLoader(file_path)
    pages = loader.load()
    print(f"  [PDF] 加载完成：{len(pages)} 页 ← {Path(file_path).name}")
    return pages


def load_markdown(file_path: str) -> list[Document]:
    """
    加载 Markdown 文档，整个文件作为一个 Document 返回。

    不在这里做标题切分——那是 5.3 分块步骤的工作。
    这里只负责把文件内容读进内存。

    Args:
        file_path: Markdown 文件的本地路径(.md 或 .markdown)

    Returns:
        list[Document]，只有一个元素，page_content 为文件全文
        metadata 包含 source(文件路径)
    """
    loader = TextLoader(file_path, encoding="utf-8")
    docs = loader.load()
    char_count = len(docs[0].page_content)
    print(f"  [MD]  加载完成：{char_count} 字符 ← {Path(file_path).name}")
    return docs


def load_document(file_path: str) -> list[Document]:
    """
    统一文档加载入口。

    根据文件扩展名自动选择加载器：
        .pdf            → PyPDFLoader(文字层提取)
        .md / .markdown → TextLoader(纯文本读取)

    Args:
        file_path: 文档本地路径

    Returns:
        list[Document]

    Raises:
        ValueError: 不支持的文件类型
        FileNotFoundError: 文件不存在
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")

    ext = path.suffix.lower()
    if ext == ".pdf":
        return load_pdf(file_path)
    elif ext in (".md", ".markdown"):
        return load_markdown(file_path)
    else:
        raise ValueError(
            f"不支持的文件类型：{ext}\n"
            f"当前支持：.pdf / .md / .markdown\n"
            f"提示：可用 markitdown 将 Word/PPT 转换为 .md 后再导入"
        )


# scripts/build_knowledge_base.py(阶段版：文档加载 + 分块，5.4 / 5.5 继续补全)


# ── 模块级分块器单例 ──────────────────────────────────────────
_MD_HEADER_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[
        ("#", "H1"),
        ("##", "H2"),
        ("###", "H3"),
        ("####", "H4"),
    ],
    strip_headers=False,
)

_CHAR_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=512,
    chunk_overlap=100,
    separators=["\n\n", "\n", "。", "，", " ", ""],
)


# ── 文档加载(5.2 内容，此处合并为完整文件)────────────────────

def load_document(file_path: str) -> list[Document]:
    """统一文档加载入口，根据扩展名选择 Loader"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    ext = path.suffix.lower()
    if ext == ".pdf":
        loader = PyPDFLoader(file_path)
        pages = loader.load()
        print(f"  [PDF] 加载完成：{len(pages)} 页 ← {path.name}")
        return pages
    elif ext in (".md", ".markdown"):
        loader = TextLoader(file_path, encoding="utf-8")
        docs = loader.load()
        print(f"  [MD]  加载完成：{len(docs[0].page_content)} 字符 ← {path.name}")
        return docs
    else:
        raise ValueError(
            f"不支持的文件类型：{ext}\n"
            f"当前支持：.pdf / .md / .markdown\n"
            f"提示：可用 markitdown 将 Word/PPT 转换为 .md 后再导入"
        )


# ── PDF 分块 ──────────────────────────────────────────────────

def split_pdf_documents(pages: list[Document]) -> list[Document]:
    """PDF 文档分块：过滤空页 + RecursiveCharacterTextSplitter"""
    non_empty_pages = [p for p in pages if len(p.page_content.strip()) > 20]
    skipped = len(pages) - len(non_empty_pages)
    if skipped > 0:
        print(f"  过滤空页：{skipped} 页(图片/扫描件页)")

    chunks = _CHAR_SPLITTER.split_documents(non_empty_pages)

    for chunk in chunks:
        filename = Path(chunk.metadata.get("source", "未知文件")).stem
        page_num = chunk.metadata.get("page", 0) + 1
        chunk.metadata["source_name"] = f"{filename} 第{page_num}页"

    print(f"  [PDF] 分块完成：{len(non_empty_pages)} 页 → {len(chunks)} 个 chunk")
    return chunks


# ── Markdown 分块 ─────────────────────────────────────────────

def split_markdown_documents(
        docs: list[Document],
        chunk_size: int = 1200,  # 代码类内容默认 1200，纯文字可调低到 600~800
        chunk_overlap: int = 100,
) -> list[Document]:
    """Markdown 文档分块：MarkdownHeaderTextSplitter + MarkdownTextSplitter 两阶段"""
    splitter = MarkdownTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    header_chunks: list[Document] = []
    for doc in docs:
        sections = _MD_HEADER_SPLITTER.split_text(doc.page_content)
        source_path = doc.metadata.get("source", "")
        for section in sections:
            section.metadata["source"] = source_path
        header_chunks.extend(sections)

    final_chunks = splitter.split_documents(header_chunks)

    for chunk in final_chunks:
        source_path = chunk.metadata.get("source", "")
        filename = Path(source_path).stem if source_path else "未知文件"
        parts = [
            chunk.metadata.get("H1", ""),
            chunk.metadata.get("H2", ""),
            chunk.metadata.get("H3", ""),
            chunk.metadata.get("H4", ""),
        ]
        parts = [p for p in parts if p]
        chunk.metadata["source_name"] = (
            f"{filename} > {' > '.join(parts)}" if parts else filename
        )

    print(f"  [MD]  分块完成：{len(docs)} 个文件 → {len(final_chunks)} 个 chunk")
    return final_chunks


# ── 统一分块入口 ──────────────────────────────────────────────

def split_documents(docs: list[Document], file_path: str) -> list[Document]:
    """统一分块入口，根据文件类型自动选择分块策略"""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return split_pdf_documents(docs)
    elif ext in (".md", ".markdown"):
        return split_markdown_documents(docs)
    else:
        raise ValueError(f"不支持的文件类型：{ext}")


BATCH_SIZE = 2   # BGE-M3 批量推理大小(12 = 速度与显存的经验平衡点)


def embed_chunks(
    chunks: list[Document],
    course_id: str,
    document_id: str,
    tenant_id: str = "tenant_default",
    version: str = "1.0",
) -> list[DocumentChunk]:
    """
    对 split_documents() 产出的 chunk 列表做 BGE-M3 嵌入，返回 DocumentChunk 列表。

    BGE-M3 推理为 CPU / GPU-bound，按 BATCH_SIZE 批量处理：
    - 减少模型推理次数(每次推理有固定启动开销)
    - 控制显存/内存峰值(整批一次性推理会爆显存)

    Args:
        chunks:      split_documents() 返回的 list[Document]
        course_id:   所属课程 UUID
        document_id: 文档的 UUID(用于 Milvus 幂等更新，删旧插新)
        tenant_id:   租户 ID，用于 Milvus 多租户过滤
        version:     课程版本号

    Returns:
        list[DocumentChunk]，每项包含 dense + sparse 向量，可直接写入 Milvus
    """
    embedder = BGEMEmbedder.get_instance()   # 单例，首次调用加载模型
    all_doc_chunks: list[DocumentChunk] = []

    total = len(chunks)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = chunks[batch_start: batch_start + BATCH_SIZE]
        texts = [c.page_content for c in batch]

        # BGE-M3 批量推理：同时拿到 dense 和 sparse
        dense_vecs, sparse_vecs = embedder.encode(texts, batch_size=BATCH_SIZE)

        for i, (chunk, dense, sparse) in enumerate(zip(batch, dense_vecs, sparse_vecs)):
            global_index = batch_start + i    # 在整个文档中的顺序编号

            all_doc_chunks.append(DocumentChunk(
                id=generate_chunk_id(chunk.page_content, document_id, global_index),
                content=chunk.page_content,
                embedding=dense,
                sparse_embedding=sparse,
                course_id=course_id,
                document_id=document_id,
                source_name=chunk.metadata.get("source_name", ""),
                chunk_type=chunk.metadata.get("chunk_type", "text"),
                chunk_index=global_index,
                version=version,
                tenant_id=tenant_id,
            ))

        done = min(batch_start + BATCH_SIZE, total)
        print(f"  嵌入进度：{done}/{total}")

    print(f"  嵌入完成：{len(all_doc_chunks)} 个 DocumentChunk")
    return all_doc_chunks

if __name__ == '__main__':
    # pdf_dir = "/Users/apple/Desktop/pythonProject/Agent/samples/简历_第7版.pdf"
    #
    # docs = load_document(pdf_dir)
    # chunks = split_pdf_documents(docs)
    #
    # for chunk in chunks:
    #     print(chunk)
    #     print("=" * 80)

    md_dir = "/Users/apple/Desktop/pythonProject/Agent/samples/关于PDF加载的进阶面试题.md"
    docs = load_document(md_dir)
    chunks = split_markdown_documents(docs)
    embed_chunks(chunks, str(uuid.uuid4()), str(uuid.uuid4()))


