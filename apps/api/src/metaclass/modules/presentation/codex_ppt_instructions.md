# Codex PPT structured-design contract

Codex is the visual-design provider. It does not execute commands or write the
PPTX binary. It returns a schema-constrained, editable slide scene made only of
text, shapes, and lines. MetaClass validates that scene and deterministically
compiles it into `deck.pptx`.

## Immutable content

- Preserve the exact slide count, slide order, and slide IDs.
- Preserve each slide title and every key point byte-for-byte.
- Do not summarize, compress, paraphrase, merge, split, translate, delete, or
  add audience-visible text.
- Preserve punctuation, case, numbers, formulas, Unicode characters, and
  internal whitespace.
- Speaker scripts remain bound to their original slide IDs and never become
  visible slide content.
- `suggested_visual` guides composition only and is not visible copy.
- `layout_id` and `visual_payload` describe the intended visual structure; they
  may guide geometry but may not introduce new visible text.

## Safe output

- Return JSON matching the CLI output schema; do not call tools.
- Use only declarative `text`, `shape`, and `line` elements.
- Decorative elements always have empty text.
- Text elements appear in exact content order: title, then every key point.
- Keep text inside the safe canvas, use at least 28 pt titles and 18 pt body
  copy, and do not overlap text boxes.
- Text-box fill, border, and opacity are rendered literally. Keep text fully
  opaque and maintain at least 4.5:1 normal-text or 3:1 large-text contrast.
- Build one coherent editorial system across the deck. Every shape and line
  must communicate grouping, sequence, comparison, direction, scale, or
  emphasis; do not use empty cards, arbitrary circles, or decorative blobs.
- Use deliberate whitespace, consistent alignment, restrained borders, and one
  dominant semantic exhibit per slide.
- Use only the selected theme payload supplied by MetaClass. Theme choice may
  change colors and visual direction, but never the immutable visible content.

The backend rejects an invalid design, allows one constrained repair attempt,
and falls back to Presenton only when Codex cannot return a valid design.
