from __future__ import annotations

import argparse
import json
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


LECTURE_TOPICS = {
    1: ("Introduction to GIS Spatial Analysis", "空间分析导论"),
    2: ("Conceptual Frameworks for Spatial Analysis", "空间分析概念框架"),
    3: ("Data Exploration and Spatial Statistics", "空间数据探索与空间统计"),
    4: ("Spatial Pattern Analysis", "空间模式分析"),
    5: ("Spatial Correlation Analysis", "空间相关性分析"),
    6: ("Regression Analysis in Geography", "地理回归分析"),
    7: ("Spatial Regression Analysis", "空间回归分析"),
    8: ("Spatial Clustering Analysis", "空间聚类分析"),
    9: ("Machine Learning Classification", "机器学习分类"),
    10: ("Time Series Analysis", "时间序列分析"),
    11: ("Spatio-Temporal Analysis", "时空分析"),
    12: ("Project Study", "项目研究"),
    13: ("Deep Learning for Remote Sensing Data", "遥感深度学习"),
    14: ("Geospatial Big Data Analysis", "地理空间大数据分析"),
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
    "analysis",
    "spatial",
    "data",
    "page",
    "lecture",
    "course",
    "using",
    "used",
}


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_model(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def sentence_summary(text: str, limit: int = 420) -> str:
    text = normalize_text(text)
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    summary = " ".join(sentence for sentence in sentences[:3] if sentence)
    if not summary:
        summary = text
    return summary[:limit].rstrip()


def collect_keywords(chunks: list[dict], limit: int = 10) -> list[str]:
    counter: Counter[str] = Counter()
    for chunk in chunks:
        for keyword in chunk.get("keywords", []):
            if keyword:
                counter[keyword] += 3
        words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", chunk.get("content", ""))
        for word in words:
            lowered = word.lower()
            if lowered not in STOPWORDS:
                counter[word] += 1
    result = []
    seen = set()
    for keyword, _count in counter.most_common(limit * 2):
        key = keyword.lower()
        if key not in seen:
            seen.add(key)
            result.append(keyword)
        if len(result) >= limit:
            break
    return result


def source_ref(chunk: dict) -> SourceRef:
    return SourceRef(
        material_id=chunk["document_id"],
        page_id=f"{chunk['document_id']}_p{chunk['page_start']:04d}",
        page_no=chunk["page_start"],
        text_span=chunk["chunk_id"],
    )


def page_ref(chunk: dict, reason: str) -> PageRef:
    return PageRef(
        material_id=chunk["document_id"],
        page_no=chunk["page_start"],
        reason=reason,
    )


def dedupe_source_refs(refs: Iterable[SourceRef]) -> list[SourceRef]:
    result = []
    seen = set()
    for ref in refs:
        key = (ref.material_id, ref.page_id, ref.page_no, ref.text_span)
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


def section_title(lecture_title_zh: str, chunks: list[dict], index: int) -> str:
    keywords = collect_keywords(chunks, limit=3)
    if keywords:
        return f"{lecture_title_zh}：{' / '.join(keywords[:2])}"
    return f"{lecture_title_zh}：教学单元 {index}"


def make_quiz(section_id: str, keywords: list[str], refs: list[SourceRef]) -> list[QuizItem]:
    if not keywords or not refs:
        return []
    point = keywords[0]
    return [
        QuizItem(
            id=f"quiz_{section_id}_01",
            question=f"关于“{point}”，下列哪一项最能体现真正理解？",
            options=[
                f"能够说明 {point} 的含义、适用条件或典型应用",
                f"只记住 {point} 在课件中出现过",
                f"把 {point} 与所有相关方法无条件混用",
            ],
            correct_index=0,
            explanation="真正理解一个空间分析知识点，需要能说明其含义、适用条件，并能结合场景判断如何使用。",
            knowledge_point=point,
            source_refs=refs[:5],
        )
    ]


def make_interaction(keywords: list[str]) -> list[InteractionOpportunity]:
    if not keywords:
        return []
    point = keywords[0]
    return [
        InteractionOpportunity(
            type="probe",
            prompt=f"请用一个 GIS 应用场景解释 {point} 为什么重要。",
            expected_answer=f"学生应能说明 {point} 解决的空间问题、适用的数据或分析条件，以及结果如何被解释。",
            target_concept_ids=[],
            difficulty="medium",
        )
    ]


def chunk_groups(chunks: list[dict], target_group_count: int = 6) -> list[list[dict]]:
    ordered = sorted(chunks, key=lambda item: (item["page_start"], item["chunk_id"]))
    if not ordered:
        return []
    target_group_count = max(1, min(target_group_count, len(ordered)))
    group_size = max(1, (len(ordered) + target_group_count - 1) // target_group_count)
    return [ordered[index : index + group_size] for index in range(0, len(ordered), group_size)]


def build_section(
    *,
    content_slug: str,
    lecture_no: int | None,
    lecture_title_zh: str,
    chunks: list[dict],
    section_index: int,
    next_title: str = "",
) -> tuple[LearningSection, KnowledgeUnit, CourseKnowledgeTreeNode]:
    section_id = f"section_{content_slug}_{section_index:03d}"
    unit_id = f"ku_{content_slug}_{section_index:03d}"
    node_id = f"node_{content_slug}_{section_index:03d}"
    title = section_title(lecture_title_zh, chunks, section_index)
    keywords = collect_keywords(chunks, limit=10)
    refs = dedupe_source_refs(source_ref(chunk) for chunk in chunks)
    page_refs = dedupe_page_refs(page_ref(chunk, "LearningContent section source") for chunk in chunks)
    summary = sentence_summary(" ".join(chunk["content"] for chunk in chunks), limit=700)
    source_excerpts = [
        SourceExcerpt(
            id=f"excerpt_{unit_id}_{index:02d}",
            text=sentence_summary(chunk["content"], limit=500),
            type=chunk.get("knowledge_type", "claim"),
            reason="Representative excerpt selected from the spatial-analysis knowledge base.",
            importance="core" if index <= 2 else "supporting",
            usage="teach_from",
            source_refs=[source_ref(chunk)],
        )
        for index, chunk in enumerate(chunks[:5], start=1)
    ]
    concepts = [
        ConceptNote(
            id=f"concept_{unit_id}_{index:02d}",
            name=keyword,
            definition="",
            plain_explanation=f"{keyword} 是本教学单元需要解释、比较或应用的核心知识点。",
            why_it_matters="它帮助学生把空间分析方法与具体 GIS 问题联系起来。",
            source_refs=refs[:5],
        )
        for index, keyword in enumerate(keywords[:5], start=1)
    ]
    examples = []
    if any(chunk.get("knowledge_type") == "case" for chunk in chunks):
        examples.append(
            ExampleNote(
                id=f"example_{unit_id}_01",
                title=f"{title} 的应用案例",
                scenario="从课件中的案例或应用页出发，让学生判断空间分析方法的适用场景。",
                explanation=summary[:500],
                takeaway="案例学习的重点是把方法、数据条件和结果解释连接起来。",
                source_refs=refs[:5],
            )
        )
    teaching_narrative = (
        f"本节围绕“{title}”组织教学。先让学生识别核心概念和问题背景，"
        f"再结合来源页码中的定义、方法或案例说明其分析逻辑。"
        f"讲解时要强调适用条件、输入数据、分析结果解释，以及它与前后知识点的联系。\n\n"
        f"材料依据：{summary}"
    )
    role_counter = Counter(chunk.get("knowledge_type", "concept") for chunk in chunks)
    role = role_counter.most_common(1)[0][0] if role_counter else "concept"
    transition = f"下一步进入“{next_title}”，继续把本节知识放入更完整的空间分析流程中。" if next_title else ""
    quiz_items = make_quiz(section_id, keywords, refs)
    section = LearningSection(
        id=section_id,
        title=title,
        role=role,
        content_goal=f"帮助学生理解并能应用：{title}",
        summary=summary,
        key_points=keywords[:8],
        teaching_narrative=teaching_narrative[:3500],
        knowledge_points=keywords[:10],
        source_excerpts=source_excerpts,
        examples=examples,
        interaction_opportunities=make_interaction(keywords),
        transition={"to_next": transition},
        source_refs=refs[:12],
        page_refs=page_refs[:12],
        tree_node_ids=[node_id],
        quiz_items=quiz_items,
        page_nos=sorted({chunk["page_start"] for chunk in chunks}),
        outline_level=1,
        teaching_script=teaching_narrative[:3000],
        visual_summary="如来源页包含地图、流程图、统计图或模型图，建议在课件生成阶段优先保留并用于解释空间关系。",
        transition_to_next=transition,
    )
    unit = KnowledgeUnit(
        id=unit_id,
        title=title,
        unit_type=role,
        summary=summary,
        aliases=keywords[:5],
        keywords=keywords,
        concepts=concepts,
        source_excerpts=source_excerpts,
        examples=examples,
        source_refs=refs[:12],
        page_refs=page_refs[:12],
        source_unit_ids=[chunk["chunk_id"] for chunk in chunks],
        importance="core" if lecture_no and lecture_no <= 11 else "supporting",
        confidence=0.82,
    )
    node = CourseKnowledgeTreeNode(
        id=node_id,
        title=title,
        role=role,
        summary=summary,
        parent_id=f"node_lecture_{lecture_no:02d}" if lecture_no else None,
        knowledge_unit_ids=[unit_id],
        order=section_index,
        prerequisite_node_ids=[],
    )
    return section, unit, node


def make_content(
    *,
    content_id: str,
    title: str,
    subtitle: str,
    chunks_by_lecture: dict[int | None, list[dict]],
    include_lectures: list[int],
    output_mode: str,
) -> LearningContent:
    sections: list[LearningSection] = []
    units: list[KnowledgeUnit] = []
    nodes: list[CourseKnowledgeTreeNode] = []
    root_node_ids: list[str] = []
    teaching_sequence: list[str] = []
    material_ids = sorted(
        {
            chunk["document_id"]
            for lecture_no in include_lectures
            for chunk in chunks_by_lecture.get(lecture_no, [])
        }
    )
    primary_material_id = material_ids[0] if material_ids else "spatial_analysis_kb"

    section_counter = 1
    for lecture_no in include_lectures:
        lecture_chunks = chunks_by_lecture.get(lecture_no, [])
        if not lecture_chunks:
            continue
        lecture_title_en, lecture_title_zh = LECTURE_TOPICS.get(
            lecture_no, (f"Lecture {lecture_no}", f"第 {lecture_no} 讲")
        )
        root_id = f"node_lecture_{lecture_no:02d}"
        lecture_summary = sentence_summary(
            " ".join(chunk["content"] for chunk in lecture_chunks[:8]),
            limit=600,
        )
        nodes.append(
            CourseKnowledgeTreeNode(
                id=root_id,
                title=f"第 {lecture_no} 讲：{lecture_title_zh}",
                role="chapter",
                summary=lecture_summary,
                parent_id=None,
                knowledge_unit_ids=[],
                order=lecture_no,
                prerequisite_node_ids=[f"node_lecture_{lecture_no - 1:02d}"]
                if lecture_no > 1 and output_mode == "course"
                else [],
            )
        )
        root_node_ids.append(root_id)
        groups = (
            [lecture_chunks]
            if output_mode == "course"
            else chunk_groups(lecture_chunks, target_group_count=6)
        )
        planned_titles = [
            section_title(lecture_title_zh, group, index)
            for index, group in enumerate(groups, start=1)
        ]
        for index, group in enumerate(groups, start=1):
            next_title = planned_titles[index] if index < len(planned_titles) else ""
            section, unit, node = build_section(
                content_slug=f"l{lecture_no:02d}" if output_mode == "lecture" else "course",
                lecture_no=lecture_no,
                lecture_title_zh=lecture_title_zh,
                chunks=group,
                section_index=section_counter if output_mode == "course" else index,
                next_title=next_title,
            )
            if output_mode == "course":
                section = section.model_copy(update={"id": f"section_course_{section_counter:03d}"})
                unit = unit.model_copy(update={"id": f"ku_course_{section_counter:03d}"})
                node = node.model_copy(
                    update={
                        "id": f"node_course_{section_counter:03d}",
                        "knowledge_unit_ids": [f"ku_course_{section_counter:03d}"],
                        "parent_id": root_id,
                        "order": lecture_no,
                    }
                )
                section = section.model_copy(
                    update={"tree_node_ids": [node.id], "quiz_items": make_quiz(section.id, section.knowledge_points, section.source_refs)}
                )
            nodes.append(node)
            sections.append(section)
            units.append(unit)
            teaching_sequence.append(node.id)
            section_counter += 1

    tree = CourseKnowledgeTree(
        id=f"tree_{content_id.removeprefix('content_')}",
        title=title,
        nodes=nodes,
        root_node_ids=root_node_ids,
        teaching_sequence=teaching_sequence,
        orphan_unit_ids=[],
        warnings=[
            "This LearningContent was generated deterministically from the spatial_analysis knowledge-base chunks.",
            "Review low-text pages and chunk labels before high-stakes classroom use.",
        ],
    )
    objectives = [
        "理解 GIS 空间分析的核心概念、方法体系和应用场景。",
        "能够根据空间问题选择合适的数据探索、空间统计、聚类、回归或时空分析方法。",
        "能够在课堂生成阶段保留来源页码，确保讲解内容可追溯。",
    ]
    if output_mode == "lecture" and sections:
        objectives = [
            f"理解{sections[0].title.split('：')[0]}中的核心概念和方法。",
            "能够解释关键术语、适用条件和典型应用。",
            "能够基于来源页码组织课堂讲解和检查题。",
        ]
    return LearningContent(
        id=content_id,
        material_id=primary_material_id,
        material_ids=material_ids,
        collection_id="col_spatial_analysis_kb",
        title=title,
        subtitle=subtitle,
        audience={
            "level": "undergraduate",
            "background": "GIS、地理信息科学、遥感或相关专业学生",
        },
        teaching_intent={
            "goal": "把空间分析课件知识块组织为可直接进入课堂生成链路的 LearningContent。",
            "boundary": "本文件不包含向量检索、RAG 编排或最终 PPT/ClassroomPlan 生成。",
        },
        material_overview={
            "source": "spatial_analysis PDF knowledge base",
            "lecture_count": len(include_lectures),
            "section_count": len(sections),
            "knowledge_unit_count": len(units),
        },
        global_concepts=[
            ConceptNote(
                id=f"global_concept_{index:02d}",
                name=keyword,
                plain_explanation=f"{keyword} 是 GIS 空间分析课程中的高频知识点。",
                why_it_matters="后续课堂生成可以围绕这些概念组织讲解、提问和练习。",
            )
            for index, keyword in enumerate(
                collect_keywords(
                    [
                        chunk
                        for lecture_no in include_lectures
                        for chunk in chunks_by_lecture.get(lecture_no, [])
                    ],
                    limit=12,
                ),
                start=1,
            )
        ],
        knowledge_units=units,
        knowledge_tree=tree,
        objectives=objectives,
        sections=sections,
        generation_guidance={
            "recommended_use": [
                "Use sections as the main units for presentation or classroom-plan generation.",
                "Use source_refs and page_refs for citations and source-page display.",
                "Use quiz_items only as lightweight classroom checkpoints; review before formal assessment.",
            ],
            "language": "Chinese teaching narrative with necessary English GIS terms preserved.",
        },
        quality={
            "coverage_score": 1.0 if sections else 0.0,
            "warnings": [
                "Deterministic first-pass LearningContent; manual review is recommended.",
                "Image-heavy or low-text pages are tracked separately in low_text_pages.csv.",
            ],
        },
        version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate MetaClass LearningContent JSON from the spatial-analysis KB."
    )
    parser.add_argument(
        "--kb-dir",
        type=Path,
        default=Path("output/spatial_analysis_kb"),
        help="Directory containing chunks.jsonl and documents.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/spatial_analysis_learningcontent"),
        help="Directory where LearningContent JSON files are written.",
    )
    args = parser.parse_args()

    kb_dir = args.kb_dir.resolve()
    output_dir = args.output_dir.resolve()
    chunks = read_jsonl(kb_dir / "chunks.jsonl")
    documents = read_json(kb_dir / "documents.json")

    lecture_chunks: dict[int | None, list[dict]] = defaultdict(list)
    for chunk in chunks:
        lecture_no = chunk.get("lecture_no")
        if isinstance(lecture_no, int):
            lecture_chunks[lecture_no].append(chunk)

    course = make_content(
        content_id="content_spatial_analysis_course",
        title="GIS 空间分析课程内容",
        subtitle="由 spatial_analysis PDF 知识库生成的课程级 LearningContent",
        chunks_by_lecture=lecture_chunks,
        include_lectures=sorted(LECTURE_TOPICS),
        output_mode="course",
    )
    write_model(output_dir / "spatial_analysis_course_learningcontent.json", course)

    lecture_outputs = []
    for lecture_no in sorted(LECTURE_TOPICS):
        if not lecture_chunks.get(lecture_no):
            continue
        title_en, title_zh = LECTURE_TOPICS[lecture_no]
        content = make_content(
            content_id=f"content_spatial_analysis_lecture_{lecture_no:02d}",
            title=f"第 {lecture_no} 讲：{title_zh}",
            subtitle=f"{title_en} 的章节级 LearningContent",
            chunks_by_lecture=lecture_chunks,
            include_lectures=[lecture_no],
            output_mode="lecture",
        )
        filename = f"lecture_{lecture_no:02d}_learningcontent.json"
        write_model(output_dir / filename, content)
        lecture_outputs.append(
            {
                "lecture_no": lecture_no,
                "title_en": title_en,
                "title_zh": title_zh,
                "file": filename,
                "section_count": len(content.sections),
                "knowledge_unit_count": len(content.knowledge_units),
            }
        )

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_kb_dir": str(kb_dir),
        "output_dir": str(output_dir),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "course_file": "spatial_analysis_course_learningcontent.json",
        "course_section_count": len(course.sections),
        "course_knowledge_unit_count": len(course.knowledge_units),
        "lecture_outputs": lecture_outputs,
        "scope": "LearningContent generation only; retrieval and course-generation orchestration are excluded.",
    }
    write_json(output_dir / "learningcontent_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
