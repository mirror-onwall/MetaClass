# GIS Spatial Analysis Knowledge Base Build

This document describes the current knowledge-base build pipeline for the PDF
materials in `spatial_analysis/`.

## Scope

The current build only covers the knowledge-base construction layer:

- source PDF inventory
- page-level text extraction
- reusable knowledge chunk generation
- metadata tagging
- GIS spatial analysis knowledge tree
- processing report
- quality review CSV exports
- Excel review workbook

It does not implement retrieval, RAG, course generation, embedding generation, or
student-facing features.

## Source Materials

The source directory is:

```text
spatial_analysis/
```

The current folder contains 17 PDF files:

- 14 lecture PDFs
- 2 course review PDFs, including one backup candidate
- 1 reference book PDF

## Build Command

Use the bundled Codex Python runtime:

```powershell
& "C:\Users\17847\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\tools\build_spatial_analysis_kb.py --input-dir .\spatial_analysis --output-dir .\output\spatial_analysis_kb
```

By default, the script uses `pypdf` for faster text extraction.

For slower layout-aware extraction, use:

```powershell
& "C:\Users\17847\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\tools\build_spatial_analysis_kb.py --input-dir .\spatial_analysis --output-dir .\output\spatial_analysis_kb --engine pdfplumber
```

For table detection, add:

```powershell
--engine pdfplumber --extract-tables
```

## Output

Generated artifacts are written to:

```text
output/spatial_analysis_kb/
```

Files:

- `documents.json`: PDF-level inventory, document category, lecture number,
  page count, SHA-256 hash, and parsing status.
- `pages.jsonl`: page-level extracted text and extraction statistics.
- `chunks.jsonl`: reusable knowledge chunks with source PDF, page number,
  lecture metadata, knowledge type, difficulty, language, and keywords.
- `knowledge_tree.json`: course knowledge tree for GIS spatial analysis.
- `build_manifest.json`: machine-readable build summary.
- `processing_report.md`: human-readable processing report.
- `low_text_pages.csv`: pages whose extracted text is below the configured
  threshold and should be reviewed for OCR, visual capture, or exclusion.
- `chunk_review.csv`: chunk-level review table with editable columns for
  retention, core knowledge marking, topic correction, type correction, and
  reviewer notes.
- `spatial_analysis_kb_review.xlsx`: Excel workbook containing the two review
  tables.
- `low_text_pages_preview.png` and `chunk_review_preview.png`: visual QA
  previews for the review workbook.

## Review Workbook

After running the PDF build, create or refresh the Excel review workbook:

```powershell
& "C:\Users\17847\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" .\tools\build_spatial_analysis_review_workbook.mjs .\output\spatial_analysis_kb .\output\spatial_analysis_kb\spatial_analysis_kb_review.xlsx
```

The workbook has two sheets:

- `Low Text Pages`: page-level QA checklist for pages that may need OCR,
  rendered-page inspection, or manual exclusion.
- `Chunk Review`: chunk-level manual review table for deciding whether to keep,
  revise, merge, split, or mark a chunk as core knowledge.

## Current Build Result

Latest verified build:

- Source PDFs: 17
- Parsed documents: 17
- Parsed pages: 1335
- Knowledge chunks: 1725
- Low-text pages: 123
- Failed documents: 0
- Duplicate SHA-256 files: 0
- Default engine: `pypdf`

## Knowledge Types

The current chunk classifier uses lightweight rules and assigns one of:

- `concept`
- `method`
- `formula`
- `workflow`
- `case`
- `summary`

These labels are intended as a first-pass structure. They can be manually
corrected later or replaced with a stronger classifier.

## Notes

The file whose name starts with `备份 Review` is marked as a backup candidate in
`documents.json` and `processing_report.md`. It is not byte-identical to the
other review PDF, so the script keeps it in the current build instead of
discarding it automatically.
