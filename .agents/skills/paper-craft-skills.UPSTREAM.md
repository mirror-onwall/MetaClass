# paper-craft-skills upstream and MetaClass version record

- Source: https://github.com/zsyggg/paper-craft-skills
- Upstream commit: `3be47a2a53cc35a411c587bca5231a08de57287a`
- Retrieved: 2026-08-10
- Upstream license declaration: MIT (as stated in the upstream README)
- MetaClass version: `paper-deck-metaclass-v2`
- MetaClass modification reason: preserve the upstream Paper Deck analysis,
  narrative planning, visual direction, and raster generation strengths while
  allowing paper figures, tables, experimental plots, screenshots, and other
  exact source evidence to remain faithful in the final deck.

## MetaClass differences from upstream

- Keeps the upstream Paper Deck Steps 1–5 unchanged as the authority for paper
  analysis, deck brief, narrative structure, outline, and per-slide prompts.
- Adds a per-slide render-mode decision owned by Paper Deck, not by the hosting
  platform:
  - `native-raster` retains the original full-slide raster workflow.
  - `source-grounded-hybrid` uses a generated visual base plus deterministically
    embedded source evidence and editable annotations.
- Requires source-grounded rendering for paper Figures, Tables, experimental
  plots, real screenshots, and other visuals whose labels, values, or factual
  content must not be regenerated.
- Extends generation, merge, and quality-gate guidance for the hybrid mode. The
  implementation is intentionally incremental: slides without exact source
  evidence continue to use the original Paper Deck raster-first behavior.

MetaClass vendors the upstream `paper-deck`, `paper-comic`, and `paper-analyzer`
skills so every backend instance uses the same reviewed instructions. The PPT
runtime stages only `SKILL.md` and referenced Markdown/text guidance from
`paper-deck` and `paper-comic`; upstream scripts are not executed by the PPT
generation process.
