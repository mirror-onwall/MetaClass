from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pdfplumber
from pypdf import PdfReader


KNOWLEDGE_TYPES = {
    "concept": [
        "concept",
        "definition",
        "framework",
        "introduction",
        "overview",
    ],
    "method": [
        "method",
        "analysis",
        "classification",
        "clustering",
        "regression",
        "correlation",
        "statistics",
        "interpolation",
    ],
    "formula": [
        "equation",
        "formula",
        "model",
        "coefficient",
        "moran",
        "ols",
        "gwr",
    ],
    "workflow": [
        "step",
        "procedure",
        "workflow",
        "process",
        "project",
        "study",
    ],
    "case": [
        "case",
        "example",
        "application",
        "remote sensing",
        "big data",
    ],
    "summary": [
        "review",
        "summary",
        "conclusion",
    ],
}


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


@dataclass
class DocumentRecord:
    document_id: str
    file_name: str
    relative_path: str
    category: str
    lecture_no: int | None
    title_en: str
    title_zh: str
    page_count: int
    file_size_bytes: int
    sha256: str
    status: str
    notes: list[str]


@dataclass
class PageRecord:
    page_id: str
    document_id: str
    file_name: str
    page: int
    text: str
    char_count: int
    word_count: int
    table_count: int
    image_count: int


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    file_name: str
    lecture_no: int | None
    lecture_title_en: str
    lecture_title_zh: str
    topic: str
    knowledge_type: str
    difficulty: str
    language: str
    page_start: int
    page_end: int
    keywords: list[str]
    content: str
    source_path: str
    source_sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_id(*parts: object, length: int = 16) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z][a-z])", text)
    return [part.strip() for part in parts if len(part.strip()) >= 30]


def chunk_page_text(text: str, max_chars: int = 1800, min_chars: int = 260) -> list[str]:
    paragraphs = split_paragraphs(text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current).strip())
                current = []
                current_len = 0
            for start in range(0, len(paragraph), max_chars):
                piece = paragraph[start : start + max_chars].strip()
                if len(piece) >= min_chars:
                    chunks.append(piece)
            continue

        if current and current_len + len(paragraph) + 2 > max_chars:
            chunks.append("\n\n".join(current).strip())
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += len(paragraph) + 2

    if current:
        joined = "\n\n".join(current).strip()
        if len(joined) >= min_chars:
            chunks.append(joined)

    return chunks


def parse_lecture_no(file_name: str) -> int | None:
    match = re.search(r"lect\.?\s*(\d+)", file_name, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def infer_document_metadata(path: Path) -> tuple[str, int | None, str, str, list[str]]:
    file_name = path.name
    lower = file_name.lower()
    notes: list[str] = []
    lecture_no = parse_lecture_no(file_name)

    if lecture_no in LECTURE_TOPICS:
        title_en, title_zh = LECTURE_TOPICS[lecture_no]
        category = "lecture"
    elif "review" in lower:
        title_en = "Course Review"
        title_zh = "课程复习"
        category = "review"
    elif "rogerson" in lower or "book" in lower:
        title_en = "Statistical Methods for Geography"
        title_zh = "地理统计方法参考教材"
        category = "reference_book"
    else:
        title_en = path.stem
        title_zh = ""
        category = "supplement"

    if "备份" in file_name or "backup" in lower:
        notes.append("backup_candidate")

    return category, lecture_no, title_en, title_zh, notes


def infer_knowledge_type(text: str, title: str, category: str) -> str:
    if category == "review":
        return "summary"
    lowered = f"{title}\n{text[:500]}".lower()
    scores = {
        kind: sum(1 for keyword in keywords if keyword in lowered)
        for kind, keywords in KNOWLEDGE_TYPES.items()
    }
    best, score = max(scores.items(), key=lambda item: item[1])
    return best if score > 0 else "concept"


def infer_difficulty(lecture_no: int | None, category: str) -> str:
    if category == "reference_book":
        return "advanced"
    if lecture_no is None:
        return "intermediate"
    if lecture_no <= 3:
        return "basic"
    if lecture_no <= 8:
        return "intermediate"
    return "advanced"


def infer_language(text: str) -> str:
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    if cjk_count > ascii_letters * 0.2:
        return "zh"
    return "en"


def extract_keywords(text: str, title: str, limit: int = 12) -> list[str]:
    candidates = [
        "GIS",
        "spatial analysis",
        "spatial statistics",
        "spatial pattern",
        "spatial correlation",
        "spatial autocorrelation",
        "Moran",
        "regression",
        "spatial regression",
        "clustering",
        "classification",
        "machine learning",
        "deep learning",
        "remote sensing",
        "time series",
        "spatio-temporal",
        "geospatial big data",
        "OLS",
        "GWR",
        "model",
        "project",
    ]
    haystack = f"{title}\n{text}".lower()
    found = []
    for candidate in candidates:
        if candidate.lower() in haystack and candidate not in found:
            found.append(candidate)
    return found[:limit]


def iter_pdf_paths(input_dir: Path) -> Iterable[Path]:
    yield from sorted(input_dir.rglob("*.pdf"), key=lambda item: item.name.lower())


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact_preview(text: str, limit: int = 420) -> str:
    preview = re.sub(r"\s+", " ", text).strip()
    if len(preview) <= limit:
        return preview
    return preview[: limit - 3].rstrip() + "..."


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def low_text_reason(page: PageRecord, threshold: int) -> str:
    reasons = []
    if page.char_count == 0:
        reasons.append("empty_text")
    elif page.char_count < threshold:
        reasons.append("low_text")
    if page.image_count > 0:
        reasons.append("has_images")
    if page.table_count > 0:
        reasons.append("has_tables")
    return ";".join(reasons)


def suggested_page_action(page: PageRecord) -> str:
    if page.char_count == 0 and page.image_count > 0:
        return "render_or_ocr"
    if page.char_count < 80 and page.image_count > 0:
        return "inspect_visual_page"
    if page.char_count < 80:
        return "check_if_cover_or_section_divider"
    return "keep"


def write_review_exports(
    output_dir: Path,
    pages: list[PageRecord],
    chunks: list[ChunkRecord],
    low_text_threshold: int,
) -> None:
    low_text_rows = []
    for page in pages:
        if page.char_count >= low_text_threshold:
            continue
        low_text_rows.append(
            {
                "page_id": page.page_id,
                "document_id": page.document_id,
                "file_name": page.file_name,
                "page": page.page,
                "char_count": page.char_count,
                "word_count": page.word_count,
                "image_count": page.image_count,
                "table_count": page.table_count,
                "reason": low_text_reason(page, low_text_threshold),
                "suggested_action": suggested_page_action(page),
                "text_preview": compact_preview(page.text, 260),
                "review_status": "",
                "review_notes": "",
            }
        )

    write_csv(
        output_dir / "low_text_pages.csv",
        [
            "page_id",
            "document_id",
            "file_name",
            "page",
            "char_count",
            "word_count",
            "image_count",
            "table_count",
            "reason",
            "suggested_action",
            "text_preview",
            "review_status",
            "review_notes",
        ],
        low_text_rows,
    )

    chunk_rows = []
    for chunk in chunks:
        chunk_rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "file_name": chunk.file_name,
                "lecture_no": chunk.lecture_no if chunk.lecture_no is not None else "",
                "lecture_title_en": chunk.lecture_title_en,
                "lecture_title_zh": chunk.lecture_title_zh,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "knowledge_type": chunk.knowledge_type,
                "difficulty": chunk.difficulty,
                "language": chunk.language,
                "keywords": "; ".join(chunk.keywords),
                "content_preview": compact_preview(chunk.content, 520),
                "keep": "",
                "is_core_knowledge": "",
                "corrected_topic": "",
                "corrected_knowledge_type": "",
                "review_notes": "",
            }
        )

    write_csv(
        output_dir / "chunk_review.csv",
        [
            "chunk_id",
            "document_id",
            "file_name",
            "lecture_no",
            "lecture_title_en",
            "lecture_title_zh",
            "page_start",
            "page_end",
            "knowledge_type",
            "difficulty",
            "language",
            "keywords",
            "content_preview",
            "keep",
            "is_core_knowledge",
            "corrected_topic",
            "corrected_knowledge_type",
            "review_notes",
        ],
        chunk_rows,
    )


def build_knowledge_tree() -> dict:
    children = [
        {
            "id": f"lecture_{lecture_no:02d}",
            "title_en": title_en,
            "title_zh": title_zh,
            "lecture_no": lecture_no,
        }
        for lecture_no, (title_en, title_zh) in LECTURE_TOPICS.items()
    ]
    children.extend(
        [
            {
                "id": "course_review",
                "title_en": "Course Review",
                "title_zh": "课程复习",
                "lecture_no": None,
            },
            {
                "id": "reference_book",
                "title_en": "Statistical Methods for Geography",
                "title_zh": "地理统计方法参考教材",
                "lecture_no": None,
            },
        ]
    )
    return {
        "id": "gis_spatial_analysis",
        "title_en": "GIS Spatial Analysis Knowledge Base",
        "title_zh": "GIS空间分析知识库",
        "children": children,
    }


def extract_with_pdfplumber(
    pdf_path: Path,
    document_id: str,
    file_hash: str,
    relative_path: str,
    category: str,
    lecture_no: int | None,
    title_en: str,
    title_zh: str,
    extract_tables: bool,
) -> tuple[int, list[PageRecord], list[ChunkRecord]]:
    pages: list[PageRecord] = []
    chunks: list[ChunkRecord] = []

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            raw_text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            text = clean_text(raw_text)
            table_count = 0
            if extract_tables:
                try:
                    table_count = len(page.extract_tables() or [])
                except Exception:
                    table_count = 0
            image_count = len(page.images or [])
            page_record, chunk_records = build_page_and_chunks(
                document_id=document_id,
                file_name=pdf_path.name,
                lecture_no=lecture_no,
                title_en=title_en,
                title_zh=title_zh,
                category=category,
                page_number=index,
                text=text,
                table_count=table_count,
                image_count=image_count,
                source_path=relative_path,
                source_sha256=file_hash,
            )
            pages.append(page_record)
            chunks.extend(chunk_records)

    return page_count, pages, chunks


def extract_with_pypdf(
    pdf_path: Path,
    document_id: str,
    file_hash: str,
    relative_path: str,
    category: str,
    lecture_no: int | None,
    title_en: str,
    title_zh: str,
) -> tuple[int, list[PageRecord], list[ChunkRecord]]:
    pages: list[PageRecord] = []
    chunks: list[ChunkRecord] = []

    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    for index, page in enumerate(reader.pages, start=1):
        text = clean_text(page.extract_text() or "")
        image_count = 0
        try:
            image_count = len(page.images)
        except Exception:
            image_count = 0
        page_record, chunk_records = build_page_and_chunks(
            document_id=document_id,
            file_name=pdf_path.name,
            lecture_no=lecture_no,
            title_en=title_en,
            title_zh=title_zh,
            category=category,
            page_number=index,
            text=text,
            table_count=0,
            image_count=image_count,
            source_path=relative_path,
            source_sha256=file_hash,
        )
        pages.append(page_record)
        chunks.extend(chunk_records)

    return page_count, pages, chunks


def build_page_and_chunks(
    document_id: str,
    file_name: str,
    lecture_no: int | None,
    title_en: str,
    title_zh: str,
    category: str,
    page_number: int,
    text: str,
    table_count: int,
    image_count: int,
    source_path: str,
    source_sha256: str,
) -> tuple[PageRecord, list[ChunkRecord]]:
    page_record = PageRecord(
        page_id=f"{document_id}_p{page_number:04d}",
        document_id=document_id,
        file_name=file_name,
        page=page_number,
        text=text,
        char_count=len(text),
        word_count=len(re.findall(r"\b\w+\b", text)),
        table_count=table_count,
        image_count=image_count,
    )
    chunk_records: list[ChunkRecord] = []
    for chunk_index, content in enumerate(chunk_page_text(text), start=1):
        knowledge_type = infer_knowledge_type(content, title_en, category)
        chunk_records.append(
            ChunkRecord(
                chunk_id=f"{document_id}_p{page_number:04d}_c{chunk_index:02d}",
                document_id=document_id,
                file_name=file_name,
                lecture_no=lecture_no,
                lecture_title_en=title_en,
                lecture_title_zh=title_zh,
                topic=title_en,
                knowledge_type=knowledge_type,
                difficulty=infer_difficulty(lecture_no, category),
                language=infer_language(content),
                page_start=page_number,
                page_end=page_number,
                keywords=extract_keywords(content, title_en),
                content=content,
                source_path=source_path,
                source_sha256=source_sha256,
            )
        )
    return page_record, chunk_records


def build(
    input_dir: Path,
    output_dir: Path,
    extract_tables: bool = False,
    engine: str = "pypdf",
    low_text_threshold: int = 80,
) -> dict:
    documents: list[DocumentRecord] = []
    pages: list[PageRecord] = []
    chunks: list[ChunkRecord] = []
    failures: list[dict] = []
    source_hashes: Counter[str] = Counter()

    pdf_paths = list(iter_pdf_paths(input_dir))

    for pdf_path in pdf_paths:
        relative_path = pdf_path.relative_to(input_dir.parent).as_posix()
        file_hash = sha256_file(pdf_path)
        source_hashes[file_hash] += 1
        category, lecture_no, title_en, title_zh, notes = infer_document_metadata(pdf_path)
        document_id = stable_id(relative_path, file_hash)

        try:
            if engine == "pdfplumber":
                page_count, document_pages, document_chunks = extract_with_pdfplumber(
                    pdf_path=pdf_path,
                    document_id=document_id,
                    file_hash=file_hash,
                    relative_path=relative_path,
                    category=category,
                    lecture_no=lecture_no,
                    title_en=title_en,
                    title_zh=title_zh,
                    extract_tables=extract_tables,
                )
            else:
                page_count, document_pages, document_chunks = extract_with_pypdf(
                    pdf_path=pdf_path,
                    document_id=document_id,
                    file_hash=file_hash,
                    relative_path=relative_path,
                    category=category,
                    lecture_no=lecture_no,
                    title_en=title_en,
                    title_zh=title_zh,
                )

            documents.append(
                DocumentRecord(
                    document_id=document_id,
                    file_name=pdf_path.name,
                    relative_path=relative_path,
                    category=category,
                    lecture_no=lecture_no,
                    title_en=title_en,
                    title_zh=title_zh,
                    page_count=page_count,
                    file_size_bytes=pdf_path.stat().st_size,
                    sha256=file_hash,
                    status="parsed",
                    notes=notes,
                )
            )
            pages.extend(document_pages)
            chunks.extend(document_chunks)
        except Exception as exc:
            failures.append(
                {
                    "file_name": pdf_path.name,
                    "relative_path": relative_path,
                    "error": repr(exc),
                }
            )
            documents.append(
                DocumentRecord(
                    document_id=document_id,
                    file_name=pdf_path.name,
                    relative_path=relative_path,
                    category=category,
                    lecture_no=lecture_no,
                    title_en=title_en,
                    title_zh=title_zh,
                    page_count=0,
                    file_size_bytes=pdf_path.stat().st_size,
                    sha256=file_hash,
                    status="failed",
                    notes=notes,
                )
            )

    duplicate_hashes = {sha for sha, count in source_hashes.items() if count > 1}
    for document in documents:
        if document.sha256 in duplicate_hashes and "duplicate_sha256" not in document.notes:
            document.notes.append("duplicate_sha256")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "documents.json", [asdict(document) for document in documents])
    write_jsonl(output_dir / "pages.jsonl", (asdict(page) for page in pages))
    write_jsonl(output_dir / "chunks.jsonl", (asdict(chunk) for chunk in chunks))
    write_json(output_dir / "knowledge_tree.json", build_knowledge_tree())
    write_review_exports(output_dir, pages, chunks, low_text_threshold)

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "pdf_count": len(pdf_paths),
        "document_count": len(documents),
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "failure_count": len(failures),
        "duplicate_hash_count": len(duplicate_hashes),
        "extract_tables": extract_tables,
        "engine": engine,
        "low_text_threshold": low_text_threshold,
        "knowledge_types": dict(Counter(chunk.knowledge_type for chunk in chunks)),
        "categories": dict(Counter(document.category for document in documents)),
        "failures": failures,
    }
    write_json(output_dir / "build_manifest.json", manifest)
    write_report(
        output_dir / "processing_report.md",
        documents,
        pages,
        chunks,
        manifest,
        low_text_threshold,
    )
    return manifest


def write_report(
    path: Path,
    documents: list[DocumentRecord],
    pages: list[PageRecord],
    chunks: list[ChunkRecord],
    manifest: dict,
    low_text_threshold: int,
) -> None:
    category_counts = Counter(document.category for document in documents)
    type_counts = Counter(chunk.knowledge_type for chunk in chunks)
    low_text_pages = [page for page in pages if page.char_count < low_text_threshold]
    backup_candidates = [
        document for document in documents if "backup_candidate" in document.notes
    ]
    duplicate_candidates = [
        document for document in documents if "duplicate_sha256" in document.notes
    ]

    lines = [
        "# GIS Spatial Analysis Knowledge Base Processing Report",
        "",
        f"- Built at: `{manifest['built_at']}`",
        f"- Source PDF count: {manifest['pdf_count']}",
        f"- Parsed document count: {manifest['document_count']}",
        f"- Parsed page count: {manifest['page_count']}",
        f"- Knowledge chunk count: {manifest['chunk_count']}",
        f"- Failed document count: {manifest['failure_count']}",
        f"- Low-text page count (<{low_text_threshold} chars): {len(low_text_pages)}",
        "",
        "## Document Categories",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in sorted(category_counts.items()))
    lines.extend(["", "## Knowledge Types", ""])
    lines.extend(f"- {name}: {count}" for name, count in sorted(type_counts.items()))
    lines.extend(["", "## Documents", ""])
    for document in sorted(documents, key=lambda item: (item.lecture_no or 999, item.file_name)):
        notes = f" ({', '.join(document.notes)})" if document.notes else ""
        lines.append(
            f"- {document.file_name}: {document.page_count} pages, "
            f"{document.category}, {document.title_en}{notes}"
        )

    if backup_candidates:
        lines.extend(["", "## Backup Candidates", ""])
        lines.extend(f"- {document.file_name}" for document in backup_candidates)

    if duplicate_candidates:
        lines.extend(["", "## Duplicate SHA-256 Candidates", ""])
        lines.extend(f"- {document.file_name}" for document in duplicate_candidates)

    if manifest["failures"]:
        lines.extend(["", "## Failures", ""])
        lines.extend(
            f"- {failure['file_name']}: `{failure['error']}`"
            for failure in manifest["failures"]
        )

    lines.extend(
        [
            "",
            "## Output Files",
            "",
            "- `documents.json`: PDF-level source inventory and metadata.",
            "- `pages.jsonl`: page-level extracted text and extraction statistics.",
            "- `chunks.jsonl`: reusable knowledge chunks with page citations.",
            "- `knowledge_tree.json`: course knowledge tree for GIS spatial analysis.",
            "- `build_manifest.json`: machine-readable build summary.",
            "- `low_text_pages.csv`: pages that need OCR/visual/manual inspection.",
            "- `chunk_review.csv`: chunk-level manual review table.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a structured GIS spatial analysis knowledge base from PDF lectures."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("spatial_analysis"),
        help="Directory containing source PDF files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/spatial_analysis_kb"),
        help="Directory where knowledge base artifacts will be written.",
    )
    parser.add_argument(
        "--extract-tables",
        action="store_true",
        help="Also attempt table detection on each PDF page. This is slower.",
    )
    parser.add_argument(
        "--engine",
        choices=["pypdf", "pdfplumber"],
        default="pypdf",
        help="PDF text extraction engine. Use pdfplumber for slower layout-aware extraction.",
    )
    parser.add_argument(
        "--low-text-threshold",
        type=int,
        default=80,
        help="Pages below this character count are exported to low_text_pages.csv.",
    )
    args = parser.parse_args()

    manifest = build(
        args.input_dir.resolve(),
        args.output_dir.resolve(),
        extract_tables=args.extract_tables,
        engine=args.engine,
        low_text_threshold=args.low_text_threshold,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
