from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from metaclass.core.schemas import SchemaModel
from metaclass.modules.paper_workflow.schemas import (
    PaperSourceBundle,
    SourceAsset,
    SourceBlock,
    SourceReference,
)


class PaperEvidenceContext(SchemaModel):
    page_no: int = Field(ge=1)
    block_id: str | None = None
    asset_id: str | None = None
    source_type: str = Field(min_length=1)
    exact_text: str = Field(min_length=1)
    before_text: str = ""
    after_text: str = ""
    section_path: list[str] = Field(default_factory=list)
    caption: str = ""
    usage: str = "support"


class PaperEvidenceRetriever:
    """Resolve slide references to bounded verbatim context from paper_source.json."""

    def __init__(self, source: PaperSourceBundle) -> None:
        self.source = source
        self.blocks_by_id = {block.id: block for block in source.blocks}
        self.assets_by_id = {asset.id: asset for asset in source.assets}
        self.block_index = {block.id: index for index, block in enumerate(source.blocks)}
        self.blocks_by_page: dict[int, list[SourceBlock]] = {}
        for block in source.blocks:
            self.blocks_by_page.setdefault(block.page_no, []).append(block)

    def retrieve(self, refs: list[SourceReference]) -> list[PaperEvidenceContext]:
        contexts: list[PaperEvidenceContext] = []
        seen: set[tuple[int, str | None, str | None, str]] = set()
        for ref in refs:
            for context in self._resolve(ref):
                key = (
                    context.page_no,
                    context.block_id,
                    context.asset_id,
                    context.exact_text,
                )
                if key not in seen:
                    contexts.append(context)
                    seen.add(key)
        return contexts

    def _resolve(self, ref: SourceReference) -> Iterable[PaperEvidenceContext]:
        if ref.block_id and ref.block_id in self.blocks_by_id:
            yield self._from_block(self.blocks_by_id[ref.block_id], ref=ref)
            return
        if ref.asset_id and ref.asset_id in self.assets_by_id:
            yield self._from_asset(self.assets_by_id[ref.asset_id])
            return

        page_blocks = self.blocks_by_page.get(ref.page_no, [])
        if not page_blocks:
            if ref.quote:
                yield PaperEvidenceContext(
                    page_no=ref.page_no,
                    source_type="quoted_reference",
                    exact_text=self._bounded(ref.quote, 1200),
                )
            return
        if ref.quote:
            normalized_quote = self._normalize(ref.quote)
            ranked = sorted(
                page_blocks,
                key=lambda block: (
                    normalized_quote not in self._normalize(block.text),
                    block.type in {"title", "reference"},
                ),
            )
            yield self._from_block(ranked[0], ref=ref)
            return

        meaningful = [
            block
            for block in page_blocks
            if block.text.strip() and block.type not in {"title", "reference"}
        ] or [block for block in page_blocks if block.text.strip()]
        for block in meaningful[:2]:
            yield self._from_block(block, ref=ref)

    def _from_block(self, block: SourceBlock, *, ref: SourceReference) -> PaperEvidenceContext:
        index = self.block_index[block.id]
        return PaperEvidenceContext(
            page_no=block.page_no,
            block_id=block.id,
            asset_id=ref.asset_id,
            source_type=block.type,
            exact_text=self._bounded(block.text or ref.quote or "Source block", 1200),
            before_text=self._adjacent_text(index - 1, block.page_no),
            after_text=self._adjacent_text(index + 1, block.page_no),
            section_path=block.section_path,
            caption=(
                self.assets_by_id[ref.asset_id].caption or ""
                if ref.asset_id and ref.asset_id in self.assets_by_id
                else ""
            ),
        )

    def _from_asset(self, asset: SourceAsset) -> PaperEvidenceContext:
        page_blocks = self.blocks_by_page.get(asset.page_no, [])
        nearby = next(
            (block for block in page_blocks if block.type == asset.type and block.text.strip()),
            None,
        )
        exact = asset.caption or (nearby.text if nearby else "") or f"{asset.type} {asset.id}"
        return PaperEvidenceContext(
            page_no=asset.page_no,
            block_id=nearby.id if nearby else None,
            asset_id=asset.id,
            source_type=asset.type,
            exact_text=self._bounded(exact, 1200),
            before_text=(
                self._adjacent_text(self.block_index[nearby.id] - 1, asset.page_no)
                if nearby
                else ""
            ),
            after_text=(
                self._adjacent_text(self.block_index[nearby.id] + 1, asset.page_no)
                if nearby
                else ""
            ),
            section_path=nearby.section_path if nearby else [],
            caption=asset.caption or "",
        )

    def _adjacent_text(self, index: int, page_no: int) -> str:
        if index < 0 or index >= len(self.source.blocks):
            return ""
        block = self.source.blocks[index]
        if block.page_no != page_no or block.type == "reference":
            return ""
        return self._bounded(block.text, 500)

    @staticmethod
    def _bounded(value: str, limit: int) -> str:
        return " ".join(value.split())[:limit].strip()

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(value.lower().split())
