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

## Safe output

- Return JSON matching the CLI output schema; do not call tools.
- Use only declarative `text`, `shape`, and `line` elements.
- Decorative elements always have empty text.
- Text elements appear in exact content order: title, then every key point.
- Keep text inside the safe canvas, use at least 28 pt titles and 18 pt body
  copy, and do not overlap text boxes.
- Use only the selected theme payload supplied by MetaClass. Theme choice may
  change colors and visual direction, but never the immutable visible content.

The backend rejects an invalid design, allows one constrained repair attempt,
and falls back to Presenton only when Codex cannot return a valid design.
