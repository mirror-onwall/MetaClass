# Spatial Analysis LearningContent Handoff

This document describes the LearningContent artifacts generated from the
`spatial_analysis` knowledge base.

## Scope

This layer belongs to LearningContent generation. It converts the structured
knowledge-base chunks into MetaClass `LearningContent` JSON files.

It does not implement:

- vector retrieval
- RAG orchestration
- final course/PPT generation
- ClassroomPlan generation

## Build Command

Run the knowledge-base build first:

```powershell
& "C:\Users\17847\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\tools\build_spatial_analysis_kb.py --input-dir .\spatial_analysis --output-dir .\output\spatial_analysis_kb
```

Then generate LearningContent:

```powershell
& "C:\Users\17847\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\tools\generate_spatial_analysis_learningcontent.py --kb-dir .\output\spatial_analysis_kb --output-dir .\output\spatial_analysis_learningcontent
```

## Outputs

Generated files are written to:

```text
output/spatial_analysis_learningcontent/
```

Main files:

- `spatial_analysis_course_learningcontent.json`: course-level LearningContent
  covering Lectures 1-14.
- `lecture_01_learningcontent.json` through `lecture_14_learningcontent.json`:
  lecture-level LearningContent files with finer section granularity.
- `learningcontent_manifest.json`: summary of generated files and section counts.

## How Downstream Teammates Should Use It

Use the course-level file when the next step needs the full GIS spatial analysis
course outline:

```text
output/spatial_analysis_learningcontent/spatial_analysis_course_learningcontent.json
```

Use a lecture-level file when the next step generates one class session, one PPT,
or one classroom plan for a specific lecture:

```text
output/spatial_analysis_learningcontent/lecture_04_learningcontent.json
```

The most important fields are:

- `title` and `subtitle`: display-level course or lecture title.
- `objectives`: teaching goals.
- `sections`: the main teaching units.
- `sections[].summary`: concise section explanation.
- `sections[].teaching_narrative`: teacher-facing narrative script.
- `sections[].knowledge_points`: key terms and concepts.
- `sections[].source_refs`: traceable source chunk/page references.
- `sections[].page_refs`: source PDF page references.
- `sections[].quiz_items`: lightweight classroom checkpoint questions.
- `knowledge_units`: normalized knowledge units behind the sections.
- `knowledge_tree`: section organization and teaching sequence.

## Source Traceability

Every generated section keeps source references:

```json
{
  "material_id": "source PDF document id",
  "page_id": "source PDF page id",
  "page_no": 12,
  "text_span": "source chunk id",
  "image_path": null
}
```

`text_span` points back to `chunk_id` in:

```text
output/spatial_analysis_kb/chunks.jsonl
```

This lets downstream modules cite the PDF page or retrieve the original chunk
content without inventing sources.

## Current Generation Result

- Course-level LearningContent files: 1
- Lecture-level LearningContent files: 14
- Course-level sections: 14
- Lecture-level sections: usually 5-6 per lecture
- Schema validation: passed against `metaclass.modules.content.schemas.LearningContent`

## Notes

The current generator is deterministic and does not call an LLM. It is intended
as a stable first pass that downstream teammates can use immediately. Manual
review is still recommended for low-text pages, image-heavy pages, and quiz
items before classroom use.
