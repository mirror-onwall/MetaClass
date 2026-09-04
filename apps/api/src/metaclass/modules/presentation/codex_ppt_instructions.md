# Codex-hosted Paper Deck contract

Codex is the execution host; the repository-pinned `$paper-deck` skill is the
visual director. Codex never independently chooses a second backend layout and
never writes the final PPTX binary. MetaClass also exposes the pinned
`$paper-comic` skill when an explanatory figure genuinely helps. Their
hash-verified guidance is embedded in a disposable workspace request so Windows
sandbox file permissions cannot block skill loading.
Each slide runs in an isolated Codex session. Because this product requires
PowerPoint editability, Paper Deck returns an editable-layer manifest instead of
flattening the page into its usual full-slide raster: every frame, node, band,
rule, connector, local illustration, and text block has its own geometry and stable
object identity. Cover, mechanism, method, case, evidence, and comparison pages
must use one or two text-free local scientific illustrations under
`generated_visuals/`; ordinary text-led pages may use one when it improves the
composition. Every picture is a separate top-level PowerPoint object and can never
be the slide background. MetaClass ignores literal model copy, injects the exact title
and key points from PresentationPlan, validates every layer, and deterministically
compiles `deck.pptx` without grouping the visual objects.

## Immutable content

- Preserve the exact slide count, slide order, and slide IDs.
- Preserve each slide title and every key point byte-for-byte.
- Do not summarize, compress, paraphrase, merge, split, translate, or delete
  PresentationPlan copy. The only permitted additional audience-visible text is
  a backend-authored missing-asset label whose wording is deterministically
  derived from `suggested_visual` or `visual_payload`.
- Preserve punctuation, case, numbers, formulas, Unicode characters, and
  internal whitespace.
- Speaker scripts remain bound to their original slide IDs and never become
  visible slide content.
- `suggested_visual` guides composition only and is not visible copy.
- `layout_id` and `visual_payload` describe the intended visual structure; they
  may guide geometry but may not introduce model-authored visible text.

## Immutable visual preset

- Every Paper Deck slide uses `journal-minimal`, regardless of the selected PPT
  color theme: Nature/IEEE-inspired academic presentation, publication-quality
  scientific figure language, disciplined typography, restrained graphic blocks,
  medium information density, and evidence-first hierarchy.
- The selected theme supplies palette tokens only. It may change background,
  ink, and one or two accent colors, but it may not switch the deck to
  `warm-notes`, `liquid-glass`, `business-research`, classroom-paper, botanical,
  cyber, or any other visual language.
- Color changes must preserve the same journal-quality composition, spacing,
  typography, diagram language, and professional academic tone across the deck.
- Use a distributed editorial composition. Give each key point and each approved
  missing-asset region its own visual island with clear whitespace; do not fuse
  unrelated points into one mega-card, nested panel, collage, overlapping cluster,
  or crowded central pile.
- Every `key_points.N` text block must have its own independent editorial anchor
  with the same `content_ref`: either a restrained containing frame or an adjacent
  rule, accent band, marker, underline, or callout edge. Do not place every point
  in a rounded card. Keep the closest edge of a non-containing anchor within
  `0.04` of its text block. Prefer paper-figure composition, side notes, process paths,
  evidence strips, and local zoom arrangements. No connector or partial shape may
  cross a text writing area.
- Every page follows its backend-assigned Paper Deck composition role. Across a
  deck, alternate editorial heroes, focal rails, central annotated mechanisms,
  horizontal or vertical processes, comparison fields, evidence strips, matrices,
  and synthesis paths. Keep one visual identity, but do not repeat a generic
  left/right silhouette on adjacent content pages.
- When a content page uses a local illustration or missing-asset placeholder,
  place it inside the assigned composition. The visual may be central, wide,
  horizontal, inset, asymmetric, or paired. A placeholder must remain useful
  (`w>=0.24`, `h>=0.18`, area `>=0.08`), and every unrelated object keeps at
  least `0.015` clearance. Two local pictures remain separate top-level objects.
  `visual_assets` and `visual_placeholders` are mutually exclusive; exact-copy
  editorial text may surround the visual region but may not overlap it.
- Separation is structural as well as visual: each frame, node, rule, connector,
  and local picture must become a separate top-level PowerPoint object with a
  unique `object_id`. Never return one all-page group or one full-slide picture.

## Safe output

- Return JSON matching the CLI output schema.
- Paper-craft mode returns `modules`, `visual_assets`, `text_blocks`, and
  `visual_placeholders`. Each module is one text-free editable shape or line with a
  unique `object_id`, one immutable `content_ref`, safe geometry, z-order, and a
  schema-approved `style_token`. The backend maps tokens to theme colors.
- `visual_assets` contains zero to two local pictures according to the backend's
  per-slide visual classification. Mechanism, method, case, evidence, comparison,
  and cover slides require at least one; source-specific unavailable visuals use a
  placeholder instead. Image files must be relative
  paths directly under `generated_visuals/`, remain smaller than the slide, and
  contain no text, labels, numbers, formulas, logos, watermarks, pseudo-text, or
  invented evidence. Prefer transparent-background scientific cutouts, mechanism
  fragments, evidence motifs, and zoomed details. Reference the exact generated
  filename of the final accepted image; if a draft is rejected and regenerated,
  return only the replacement image in `visual_assets`. No full-slide picture is accepted.
- Do not run shell commands, third-party scripts, package managers, web search,
  or network requests. The workspace is disposable and the final PPTX remains
  backend-owned.
- The slide background is a native PowerPoint fill. Cards, nodes, timelines,
  callouts, dividers, and underlines remain independent DrawingML objects. An
  approved missing-asset placeholder is another independent editable object.
- Before returning a page, the Codex-hosted Skill visually inspects any local image
  and regenerates it if it finds glyphs, pseudo-text, a watermark, or invented
  evidence. The manifest carries the required `raster_audit` attestation even when
  `visual_assets` is empty; the backend rejects a missing audit.
- Every text block identifies `title` or `key_points.N` and defines its padded inner
  writing area, font role, size range, weight, color, alignment, and preferred line
  count. Exact copy may wrap to additional lines only when real-font measurement
  proves the declared block height can contain it completely.
  MetaClass may only shrink inside that declared range; it may not move, recolor,
  restyle, truncate, merge, or rewrite the content.
- A non-cover slide may return at most one `visual_placeholders` entry. It contains
  only an immutable Plan visual reference and safe geometry, never model-authored
  copy. Use it only for an unavailable real or source-specific photograph,
  screenshot, map, remote-sensing image, experimental chart, paper figure, or
  document page. Do not use it for a truthful explanatory diagram that Paper Deck
  can construct from `visual_payload`.
- Safe-canvas validation keeps every editable object inside the declared margins.
- Repair prompts retain cumulative failures and require a complete fit, contrast,
  bounds, and overlap re-audit so correcting one block cannot silently break another.
- The backend text elements appear in exact content order: title, then every key
  point. Their text boxes have no fill and no border because their independently
  movable visual treatment sits beneath them as a separate object. An authorized missing-asset label is
  validated separately and rendered in a bordered editable text box; it never
  becomes a key point or changes the PresentationPlan contract.
- Use only the selected color payload supplied by MetaClass. Theme choice may
  change colors, but never the `journal-minimal` visual direction or immutable
  visible content.

If any slide lacks valid editable modules or fails the asset/layout gate,
the strict Paper Deck attempt fails and the configured provider chain may use
Presenton. It never silently emits the old vector-only Codex design while
Paper Deck mode is enabled. Neither provider may change PresentationPlan
content or scripts.
