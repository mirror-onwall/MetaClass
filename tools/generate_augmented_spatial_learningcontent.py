from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
API_SRC = REPO_ROOT / "apps" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from metaclass.modules.content.schemas import (  # noqa: E402
    ConceptNote,
    CourseKnowledgeTree,
    CourseKnowledgeTreeNode,
    ExampleNote,
    InteractionOpportunity,
    KnowledgeUnit,
    LearningContent,
    LearningSection,
    PageRef,
    QuizItem,
    SourceExcerpt,
)
from metaclass.modules.materials.schemas import SourceRef  # noqa: E402


SPATIAL_ANALYSIS_TERMS = {
    "gis": 4,
    "geographic information": 4,
    "geospatial": 4,
    "spatial analysis": 5,
    "spatial": 2,
    "map": 2,
    "mapping": 2,
    "raster": 3,
    "vector": 3,
    "remote sensing": 3,
    "coordinate": 2,
    "projection": 2,
    "buffer": 4,
    "overlay": 4,
    "network analysis": 4,
    "spatial statistics": 5,
    "moran": 5,
    "autocorrelation": 4,
    "hot spot": 4,
    "getis": 4,
    "spatial regression": 5,
    "gwr": 5,
    "clustering": 3,
    "spatio-temporal": 5,
    "空间分析": 5,
    "地理信息": 4,
    "地理空间": 4,
    "空间统计": 5,
    "空间自相关": 5,
    "空间回归": 5,
    "空间聚类": 5,
    "缓冲区": 4,
    "叠置": 4,
    "栅格": 3,
    "矢量": 3,
    "遥感": 3,
    "坐标": 2,
    "投影": 2,
    "时空": 4,
}

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "what",
    "when",
    "where",
    "which",
    "using",
    "used",
    "page",
    "lecture",
    "course",
    "analysis",
    "spatial",
    "data",
}


def read_json_or_jsonl(path: Path) -> object:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def read_jsonl(path: Path) -> list[dict]:
    return list(read_json_or_jsonl(path))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_model(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    english = [
        word
        for word in re.findall(r"[a-z][a-z0-9\-]{2,}", lowered)
        if word not in STOPWORDS
    ]
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return english + chinese


def sentence_summary(text: str, limit: int = 520) -> str:
    text = normalize_text(text)
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    summary = " ".join(sentence for sentence in sentences[:4] if sentence)
    if not summary:
        summary = text
    return summary[:limit].rstrip()


def compact(text: str, limit: int = 700) -> str:
    text = normalize_text(text)
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def load_user_pages(path: Path, material_id: str) -> list[dict]:
    payload = read_json_or_jsonl(path)
    if isinstance(payload, dict):
        if "pages" in payload:
            rows = payload["pages"]
        elif "items" in payload:
            rows = payload["items"]
        else:
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("User material input must be a JSON object/list or JSONL file")

    pages = []
    for index, row in enumerate(rows, start=1):
        page_no = int(row.get("page_no") or row.get("page") or index)
        page_id = str(row.get("id") or row.get("page_id") or f"{material_id}_p{page_no:04d}")
        raw_text = normalize_text(
            row.get("raw_text")
            or row.get("text")
            or row.get("content")
            or row.get("summary")
            or ""
        )
        title = normalize_text(row.get("title") or f"Page {page_no}")[:200]
        source_refs = row.get("source_refs") or [
            {
                "material_id": material_id,
                "page_id": page_id,
                "page_no": page_no,
                "text_span": None,
                "image_path": row.get("image_path"),
            }
        ]
        pages.append(
            {
                "id": page_id,
                "material_id": str(row.get("material_id") or material_id),
                "page_no": page_no,
                "title": title,
                "raw_text": raw_text,
                "image_path": row.get("image_path") or "",
                "source_refs": source_refs,
            }
        )
    return sorted(pages, key=lambda item: item["page_no"])


def detect_spatial_analysis(pages: list[dict], threshold: float) -> dict:
    text = " ".join(
        [page.get("title", "") for page in pages]
        + [page.get("raw_text", "")[:1500] for page in pages]
    )
    lowered = text.lower()
    weighted_hits = []
    score = 0.0
    strong_hit_count = 0
    for term, weight in SPATIAL_ANALYSIS_TERMS.items():
        count = lowered.count(term.lower()) if term.isascii() else text.count(term)
        if count:
            contribution = min(count, 5) * weight
            score += contribution
            if weight >= 4:
                strong_hit_count += 1
            weighted_hits.append({"term": term, "count": count, "weight": weight})
    tokens = tokenize(text)
    normalized_score = score / max(35.0, math.sqrt(max(len(tokens), 1)) * 4.0)
    decision = normalized_score >= threshold or strong_hit_count >= 3
    return {
        "is_spatial_analysis": decision,
        "score": round(normalized_score, 3),
        "threshold": threshold,
        "strong_hit_count": strong_hit_count,
        "decision_rule": "score_threshold_or_3_strong_terms",
        "matched_terms": sorted(weighted_hits, key=lambda item: item["weight"], reverse=True)[:20],
    }


def collect_keywords(texts: Iterable[str], limit: int = 12) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for token in tokenize(text):
            if len(token) >= 2:
                counter[token] += 1
        lowered = text.lower()
        for term, weight in SPATIAL_ANALYSIS_TERMS.items():
            if term.lower() in lowered:
                counter[term] += weight
    result = []
    seen = set()
    for keyword, _count in counter.most_common(limit * 3):
        key = keyword.lower()
        if key not in seen:
            seen.add(key)
            result.append(keyword)
        if len(result) >= limit:
            break
    return result


def build_kb_index(chunks: list[dict]) -> tuple[list[dict], dict[str, float]]:
    document_frequency: Counter[str] = Counter()
    indexed = []
    for chunk in chunks:
        text = " ".join(
            [
                chunk.get("topic", ""),
                " ".join(chunk.get("keywords", [])),
                chunk.get("content", ""),
            ]
        )
        terms = tokenize(text)
        term_counts = Counter(terms)
        for term in term_counts:
            document_frequency[term] += 1
        indexed.append({"chunk": chunk, "term_counts": term_counts, "length": len(terms) or 1})
    total = max(len(indexed), 1)
    idf = {term: math.log((total + 1) / (df + 0.5)) + 1 for term, df in document_frequency.items()}
    return indexed, idf


def retrieve_chunks(
    query: str,
    indexed_chunks: list[dict],
    idf: dict[str, float],
    *,
    top_k: int,
    min_score: float,
) -> list[dict]:
    query_terms = Counter(tokenize(query))
    if not query_terms:
        return []
    scored = []
    for indexed in indexed_chunks:
        chunk = indexed["chunk"]
        term_counts = indexed["term_counts"]
        score = 0.0
        for term, query_count in query_terms.items():
            tf = term_counts.get(term, 0)
            if tf:
                score += query_count * (1 + math.log(tf)) * idf.get(term, 1.0)
        for keyword in chunk.get("keywords", []):
            if keyword.lower() in query.lower():
                score += 2.0
        if score >= min_score:
            scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1].get("page_start", 0)))
    results = []
    seen = set()
    for score, chunk in scored:
        if chunk["chunk_id"] in seen:
            continue
        seen.add(chunk["chunk_id"])
        item = dict(chunk)
        item["retrieval_score"] = round(score, 3)
        results.append(item)
        if len(results) >= top_k:
            break
    return results


def user_source_ref(page: dict) -> SourceRef:
    ref = page.get("source_refs", [{}])[0]
    return SourceRef(
        material_id=str(ref.get("material_id") or page["material_id"]),
        page_id=str(ref.get("page_id") or page["id"]),
        page_no=int(ref.get("page_no") or page["page_no"]),
        text_span=ref.get("text_span"),
        image_path=ref.get("image_path"),
    )


def kb_source_ref(chunk: dict) -> SourceRef:
    return SourceRef(
        material_id=chunk["document_id"],
        page_id=f"{chunk['document_id']}_p{chunk['page_start']:04d}",
        page_no=chunk["page_start"],
        text_span=chunk["chunk_id"],
    )


def dedupe_source_refs(refs: Iterable[SourceRef]) -> list[SourceRef]:
    result = []
    seen = set()
    for ref in refs:
        key = (ref.material_id, ref.page_id, ref.page_no, ref.text_span, ref.image_path)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def dedupe_page_refs(refs: Iterable[PageRef]) -> list[PageRef]:
    result = []
    seen = set()
    for ref in refs:
        key = (ref.material_id, ref.page_no)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def group_pages(pages: list[dict], max_pages_per_section: int) -> list[list[dict]]:
    if not pages:
        return []
    groups = []
    current = []
    for page in pages:
        starts_new = bool(
            current
            and page["title"]
            and re.search(r"chapter|section|part|目录|大纲|章节|小节", page["title"], re.I)
        )
        if current and (len(current) >= max_pages_per_section or starts_new):
            groups.append(current)
            current = [page]
        else:
            current.append(page)
    if current:
        groups.append(current)
    return groups


def build_section(
    *,
    index: int,
    page_group: list[dict],
    retrieved: list[dict],
    content_slug: str,
) -> tuple[LearningSection, KnowledgeUnit, CourseKnowledgeTreeNode, dict]:
    section_id = f"section_{content_slug}_{index:03d}"
    unit_id = f"ku_{content_slug}_{index:03d}"
    node_id = f"node_{content_slug}_{index:03d}"
    user_text = " ".join(page["raw_text"] for page in page_group)
    kb_text = " ".join(chunk["content"] for chunk in retrieved[:5])
    keywords = collect_keywords([user_text, kb_text], limit=12)
    title_base = next((page["title"] for page in page_group if page["title"]), f"教学单元 {index}")
    title = title_base if len(title_base) <= 60 else f"教学单元 {index}：{keywords[0] if keywords else '主题讲解'}"
    user_refs = [user_source_ref(page) for page in page_group]
    kb_refs = [kb_source_ref(chunk) for chunk in retrieved[:8]]
    refs = dedupe_source_refs([*user_refs, *kb_refs])
    page_refs = dedupe_page_refs(
        [
            *[
                PageRef(
                    material_id=page["material_id"],
                    page_no=page["page_no"],
                    reason="Uploaded material source page",
                )
                for page in page_group
            ],
            *[
                PageRef(
                    material_id=chunk["document_id"],
                    page_no=chunk["page_start"],
                    reason="Retrieved spatial-analysis knowledge base support",
                )
                for chunk in retrieved[:8]
            ],
        ]
    )
    user_summary = sentence_summary(user_text, limit=650)
    kb_summary = sentence_summary(kb_text, limit=650) if retrieved else ""
    summary = user_summary
    if kb_summary:
        summary = f"{user_summary}\n\n知识库补充：{kb_summary}"[:1200]
    source_excerpts = [
        SourceExcerpt(
            id=f"excerpt_{unit_id}_user_{i:02d}",
            text=compact(page["raw_text"], 450),
            type="uploaded_material",
            reason="Representative text from the uploaded user material.",
            importance="core",
            usage="teach_from",
            source_refs=[user_source_ref(page)],
        )
        for i, page in enumerate(page_group[:3], start=1)
        if page["raw_text"]
    ]
    source_excerpts.extend(
        [
            SourceExcerpt(
                id=f"excerpt_{unit_id}_kb_{i:02d}",
                text=compact(chunk["content"], 500),
                type=chunk.get("knowledge_type", "reference"),
                reason="Retrieved from the GIS spatial-analysis knowledge base.",
                importance="supporting",
                usage="augment",
                source_refs=[kb_source_ref(chunk)],
            )
            for i, chunk in enumerate(retrieved[:4], start=1)
        ]
    )
    concepts = [
        ConceptNote(
            id=f"concept_{unit_id}_{i:02d}",
            name=keyword,
            plain_explanation=f"{keyword} 是本节需要结合上传材料和空间分析知识库解释的知识点。",
            why_it_matters="用于把用户材料中的主题与 GIS 空间分析课程体系连接起来。",
            source_refs=refs[:8],
        )
        for i, keyword in enumerate(keywords[:6], start=1)
    ]
    examples = []
    if retrieved:
        examples.append(
            ExampleNote(
                id=f"example_{unit_id}_01",
                title="空间分析知识库补充案例",
                scenario="结合检索到的课程知识，补充用户上传材料中未充分展开的概念、方法或应用场景。",
                explanation=kb_summary[:600],
                takeaway="PPT 生成时可把这些补充内容作为解释页、案例页或课堂提问依据。",
                source_refs=kb_refs[:5],
            )
        )
    teaching_narrative = (
        f"本节先围绕用户上传材料中的“{title}”建立课堂主线。"
        "讲解时先复述材料中的核心信息，再补充空间分析知识库中可支撑的概念、方法、适用条件和案例。"
        "如果用户材料较简略，应使用知识库补充内容帮助学生理解背景和方法逻辑；"
        "如果用户材料已经完整，则知识库内容主要用于校验、扩展和生成课堂提问。\n\n"
        f"用户材料要点：{user_summary}"
    )
    if kb_summary:
        teaching_narrative += f"\n\n知识库增强要点：{kb_summary}"
    quiz_items = []
    if keywords and refs:
        point = keywords[0]
        quiz_items.append(
            QuizItem(
                id=f"quiz_{section_id}_01",
                question=f"关于“{point}”，哪一项最能说明学生已经理解本节内容？",
                options=[
                    f"能结合上传材料解释 {point} 的含义、条件或应用",
                    f"只记住 {point} 这个词",
                    f"不区分场景地使用 {point}",
                ],
                correct_index=0,
                explanation="本节目标不是记忆术语，而是把术语、空间问题、数据条件和应用解释联系起来。",
                knowledge_point=point,
                source_refs=refs[:8],
            )
        )
    role = "method" if retrieved else "concept"
    section = LearningSection(
        id=section_id,
        title=title,
        role=role,
        content_goal="基于用户上传材料生成可讲授、可追溯、可继续生成 PPT 的课程内容。",
        summary=summary,
        key_points=keywords[:8],
        teaching_narrative=teaching_narrative[:4000],
        knowledge_points=keywords[:10],
        source_excerpts=source_excerpts[:8],
        examples=examples,
        interaction_opportunities=[
            InteractionOpportunity(
                type="probe",
                prompt=f"请结合本节材料说明 {keywords[0]} 可以解决什么空间问题。" if keywords else "",
                expected_answer="学生应能说出问题场景、数据条件、方法逻辑和结果解释。",
                difficulty="medium",
            )
        ]
        if keywords
        else [],
        transition={"to_next": ""},
        source_refs=refs[:14],
        page_refs=page_refs[:14],
        tree_node_ids=[node_id],
        quiz_items=quiz_items,
        page_nos=[page["page_no"] for page in page_group],
        outline_level=1,
        teaching_script=teaching_narrative[:3500],
        visual_summary="若用户上传页包含图表、地图或流程图，PPT 生成阶段应优先保留原始视觉材料并结合本节讲稿解释。",
    )
    unit = KnowledgeUnit(
        id=unit_id,
        title=title,
        unit_type=role,
        summary=summary,
        aliases=keywords[:5],
        keywords=keywords,
        concepts=concepts,
        source_excerpts=source_excerpts[:8],
        examples=examples,
        source_refs=refs[:14],
        page_refs=page_refs[:14],
        source_unit_ids=[chunk["chunk_id"] for chunk in retrieved],
        importance="core",
        confidence=0.86 if retrieved else 0.72,
    )
    node = CourseKnowledgeTreeNode(
        id=node_id,
        title=title,
        role=role,
        summary=summary,
        parent_id=None,
        knowledge_unit_ids=[unit_id],
        order=index,
        prerequisite_node_ids=[f"node_{content_slug}_{index - 1:03d}"] if index > 1 else [],
    )
    report = {
        "section_id": section_id,
        "title": title,
        "user_pages": [page["page_no"] for page in page_group],
        "retrieved_chunk_ids": [chunk["chunk_id"] for chunk in retrieved],
        "retrieved_count": len(retrieved),
        "top_scores": [chunk.get("retrieval_score", 0) for chunk in retrieved[:5]],
    }
    return section, unit, node, report


def generate_learning_content(
    *,
    pages: list[dict],
    kb_chunks: list[dict],
    content_id: str,
    title: str,
    spatial_decision: dict,
    max_pages_per_section: int,
    top_k: int,
    min_score: float,
) -> tuple[LearningContent, dict]:
    indexed_chunks, idf = build_kb_index(kb_chunks) if spatial_decision["is_spatial_analysis"] else ([], {})
    groups = group_pages(pages, max_pages_per_section=max_pages_per_section)
    sections = []
    units = []
    nodes = []
    retrieval_rows = []
    content_slug = re.sub(r"[^a-zA-Z0-9]+", "_", content_id.removeprefix("content_")).strip("_")
    for index, group in enumerate(groups, start=1):
        query = " ".join(
            [
                *(page["title"] for page in group),
                *(page["raw_text"][:1200] for page in group),
            ]
        )
        retrieved = (
            retrieve_chunks(query, indexed_chunks, idf, top_k=top_k, min_score=min_score)
            if spatial_decision["is_spatial_analysis"]
            else []
        )
        section, unit, node, report = build_section(
            index=index,
            page_group=group,
            retrieved=retrieved,
            content_slug=content_slug or "uploaded",
        )
        sections.append(section)
        units.append(unit)
        nodes.append(node)
        retrieval_rows.append(report)

    tree = CourseKnowledgeTree(
        id=f"tree_{content_id.removeprefix('content_')}",
        title=title,
        nodes=nodes,
        root_node_ids=[node.id for node in nodes],
        teaching_sequence=[node.id for node in nodes],
        orphan_unit_ids=[],
        warnings=[] if spatial_decision["is_spatial_analysis"] else ["Uploaded material was not classified as spatial analysis; KB retrieval was skipped."],
    )
    material_ids = sorted({page["material_id"] for page in pages})
    all_keywords = collect_keywords([page["raw_text"] for page in pages], limit=12)
    retrieval_enabled = spatial_decision["is_spatial_analysis"]
    content = LearningContent(
        id=content_id,
        material_id=material_ids[0] if material_ids else "uploaded_material",
        material_ids=material_ids,
        collection_id=None,
        title=title,
        subtitle="空间分析知识库增强 LearningContent" if retrieval_enabled else "未启用空间分析知识库增强的 LearningContent",
        audience={
            "level": "undergraduate",
            "background": "根据用户上传材料自动推断；空间分析材料默认面向 GIS 相关课程学生。",
        },
        teaching_intent={
            "goal": "生成可供 PPT 生成模块直接使用的 LearningContent。",
            "retrieval_policy": "Only retrieve spatial_analysis_kb when the uploaded material is classified as GIS/spatial-analysis related.",
        },
        material_overview={
            "uploaded_page_count": len(pages),
            "section_count": len(sections),
            "retrieval_enabled": retrieval_enabled,
            "spatial_analysis_score": spatial_decision["score"],
            "matched_terms": spatial_decision["matched_terms"],
        },
        global_concepts=[
            ConceptNote(
                id=f"global_concept_{index:02d}",
                name=keyword,
                plain_explanation=f"{keyword} 是用户上传材料中识别出的关键主题。",
            )
            for index, keyword in enumerate(all_keywords, start=1)
        ],
        knowledge_units=units,
        knowledge_tree=tree,
        objectives=[
            "把用户上传材料组织为结构化教学内容。",
            "在材料属于空间分析主题时，使用 GIS 空间分析知识库补充概念、方法和案例。",
            "保留用户材料与知识库来源引用，供后续 PPT 生成和课堂生成使用。",
        ],
        sections=sections,
        generation_guidance={
            "for_ppt_generator": [
                "Use sections as the slide/storyboard source.",
                "Use teaching_narrative as speaker-script material.",
                "Use source_refs/page_refs for source-page citation and evidence lookup.",
                "When retrieval_enabled is false, do not assume GIS spatial-analysis background knowledge.",
            ],
            "retrieval_enabled": retrieval_enabled,
        },
        quality={
            "retrieval_enabled": retrieval_enabled,
            "spatial_analysis_score": spatial_decision["score"],
            "spatial_analysis_threshold": spatial_decision["threshold"],
            "retrieved_chunk_count": sum(row["retrieved_count"] for row in retrieval_rows),
            "warnings": []
            if retrieval_enabled
            else ["主题保护机制触发：用户上传材料未被判定为空间分析主题，因此没有检索空间分析知识库。"],
        },
        version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    report = {
        "content_id": content_id,
        "retrieval_enabled": retrieval_enabled,
        "spatial_decision": spatial_decision,
        "section_reports": retrieval_rows,
    }
    return content, report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate retrieval-augmented LearningContent for uploaded materials."
    )
    parser.add_argument("--user-pages", type=Path, required=True, help="User uploaded material pages as JSON/JSONL.")
    parser.add_argument("--kb-dir", type=Path, default=Path("output/spatial_analysis_kb"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/augmented_learningcontent"))
    parser.add_argument("--content-id", default="content_uploaded_spatial_augmented")
    parser.add_argument("--material-id", default="uploaded_material")
    parser.add_argument("--title", default="用户上传材料 LearningContent")
    parser.add_argument("--threshold", type=float, default=0.5, help="Spatial-analysis classification threshold.")
    parser.add_argument("--top-k", type=int, default=6, help="Retrieved KB chunks per generated section.")
    parser.add_argument("--min-score", type=float, default=2.0, help="Minimum lexical retrieval score.")
    parser.add_argument("--max-pages-per-section", type=int, default=4)
    args = parser.parse_args()

    pages = load_user_pages(args.user_pages.resolve(), args.material_id)
    decision = detect_spatial_analysis(pages, threshold=args.threshold)
    kb_chunks = read_jsonl(args.kb_dir.resolve() / "chunks.jsonl") if decision["is_spatial_analysis"] else []
    content, report = generate_learning_content(
        pages=pages,
        kb_chunks=kb_chunks,
        content_id=args.content_id,
        title=args.title,
        spatial_decision=decision,
        max_pages_per_section=args.max_pages_per_section,
        top_k=args.top_k,
        min_score=args.min_score,
    )
    output_dir = args.output_dir.resolve()
    write_model(output_dir / f"{args.content_id}.json", content)
    write_json(output_dir / f"{args.content_id}_retrieval_report.json", report)
    print(
        json.dumps(
            {
                "content_file": str(output_dir / f"{args.content_id}.json"),
                "retrieval_report": str(output_dir / f"{args.content_id}_retrieval_report.json"),
                "retrieval_enabled": report["retrieval_enabled"],
                "spatial_analysis_score": decision["score"],
                "section_count": len(content.sections),
                "retrieved_chunk_count": content.quality["retrieved_chunk_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
