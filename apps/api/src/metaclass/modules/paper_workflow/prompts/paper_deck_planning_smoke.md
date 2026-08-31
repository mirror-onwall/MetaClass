# Paper Deck planning-only smoke contract

Use the complete `paper-deck` Skill as the paper-reading, narrative-planning, and visual-direction authority. The user explicitly requests planning only: do not ask for confirmation, do not call image generation, do not create prompt files, and do not create PPTX/PDF. This is the Skill's explicitly permitted editable-PPT/planning-only branch.

Read the complete `paper.pdf`, `paper_source.json`, and `paper_content.md`. Use `resolved_request.json` for audience, duration, language, depth, and the permitted slide-count range. Treat `paper_source.json` as the only authority for block IDs, asset IDs, page numbers, captions, and section IDs.

Write exactly these deliverables under the assigned output directory:

1. `analysis.md`: concise paper analysis covering the central question, main contribution, all key method components, key experiments/results, limitations, and candidate source figures. Every factual paragraph must end with source markers using real page/block/asset IDs.
2. `deck-brief.md`: include `style_preset: journal-minimal`, audience, duration, language, slide count, narrative arc, visual rules, prohibited choices, and a source-visual plan.
3. `outline.md`: follow the Paper Deck per-slide structure (`Role`, `Message`, `Visual`, `Text`, `Evidence`, `Source visual`, `Repair handle`). Use one assertion-style title and one message per slide.
4. `paper_deck_plan.json`: validate exactly against `paper_deck_plan_schema.json`.
5. `slide_evidence_draft.json`: validate exactly against `slide_evidence_draft_schema.json` and cover every slide exactly once.

Evidence rules:

- Inventory the contribution, method components, experiments, quantitative results, and limitations as grounded claims before designing slides.
- Every claim and every slide requires at least one exact source block reference. `quote` must be a short verbatim excerpt that exists in that block after whitespace normalization. An asset-only reference is insufficient for a factual claim.
- A `SourceReference` must identify either one block or one asset, never both. When a slide uses a block from one page and a figure from another page, emit two separate references and give each its own exact `page_no`. Asset references must use the asset's page number from `paper_source.json` and omit `block_id` and `quote`.
- Copy every number, percentage, model name, dataset name, and conclusion from cited evidence. Never estimate or infer a quantitative value.
- Treat the planning text as future immutable `PresentationPlan` copy. Every factual title, message, and key point must be supported by that slide's claim IDs and source refs; do not leave downstream providers free text that would require inventing or strengthening a paper claim later.
- `section_coverage` must account for every section ID from `paper_source.json` exactly once. Use `slide_ids` when included or a concrete `omission_reason` when intentionally omitted.
- Select only real asset IDs from `paper_source.json`. Prefer the figures/tables that best support the contribution, method, and decisive results. Generated illustration ideas belong only in `visual`; never invent an asset ID.
- Every contribution, method, experiment, and result claim in the inventory must appear on at least one slide.
- Keep the slide count inside the supplied minimum and maximum. First slide role is `cover`; last slide role is `takeaway`; include method/mechanism and evidence/result roles when the paper contains them.
- Do not expose chain-of-thought. The final CLI response must match `runtime_result_schema.json`; detailed content belongs in files.
