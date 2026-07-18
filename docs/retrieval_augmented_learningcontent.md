# Retrieval-Augmented LearningContent

This document describes the final responsibility boundary for spatial-analysis
LearningContent generation.

## Responsibility

This module owns:

```text
uploaded spatial-analysis material
  -> topic guard
  -> spatial_analysis knowledge-base retrieval
  -> augmented LearningContent JSON
```

It does not own:

- PPT layout generation
- PresentationPlan generation
- final PPT export
- ClassroomPlan execution

The PPT teammate should receive the final `LearningContent` JSON and generate
slides from `sections`.

## Script

```text
tools/generate_augmented_spatial_learningcontent.py
```

## Input

The script expects user-uploaded material pages as JSON or JSONL. It supports a
platform-like shape:

```json
{
  "pages": [
    {
      "page_no": 1,
      "title": "Spatial Clustering Analysis",
      "raw_text": "..."
    }
  ]
}
```

It also accepts fields named `page`, `text`, `content`, or `summary` for simpler
intermediate exports.

## Command

```powershell
& "C:\Users\17847\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\tools\generate_augmented_spatial_learningcontent.py --user-pages .\path\to\user_pages.json --kb-dir .\output\spatial_analysis_kb --output-dir .\output\augmented_learningcontent --content-id content_user_upload --material-id mat_user_upload --title "用户上传材料 LearningContent"
```

## Topic Guard

Before retrieval, the script computes a spatial-analysis relevance score from
the uploaded page titles and text.

Default threshold:

```text
0.5
```

The guard enables retrieval when either the score reaches the threshold or the
material matches at least three high-weight spatial-analysis terms.

If the material is classified as spatial analysis:

```text
retrieval_enabled = true
```

The script retrieves relevant chunks from:

```text
output/spatial_analysis_kb/chunks.jsonl
```

If the material is not classified as spatial analysis:

```text
retrieval_enabled = false
```

The script still generates LearningContent from the uploaded material, but it
does not retrieve or mix in GIS spatial-analysis knowledge.

The decision is written into:

```text
quality.retrieval_enabled
quality.spatial_analysis_score
quality.spatial_analysis_threshold
material_overview.matched_terms
```

## Output

For a content id such as `content_user_upload`, the script writes:

```text
output/augmented_learningcontent/content_user_upload.json
output/augmented_learningcontent/content_user_upload_retrieval_report.json
```

The first file is the final `LearningContent` for the PPT teammate.

The second file is an audit report showing whether retrieval was enabled and
which knowledge-base chunks were retrieved for each section.

## What PPT Generation Should Use

The PPT generation teammate should mainly use:

- `sections[].title`
- `sections[].summary`
- `sections[].teaching_narrative`
- `sections[].knowledge_points`
- `sections[].source_excerpts`
- `sections[].quiz_items`
- `sections[].source_refs`
- `sections[].page_refs`

If `quality.retrieval_enabled` is false, the PPT generator should avoid
claiming the material was enhanced by the GIS spatial-analysis knowledge base.

## Verified Examples

Example inputs and outputs are in:

```text
output/augmented_learningcontent_examples/
```

Verified behavior:

- `spatial_clustering_user_pages.json` enables retrieval and retrieves 6 chunks.
- `non_spatial_user_pages.json` disables retrieval and retrieves 0 chunks.

Both generated outputs validate against the existing
`metaclass.modules.content.schemas.LearningContent` schema.
