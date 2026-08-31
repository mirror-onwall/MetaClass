import hashlib
import json
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pymupdf

from metaclass.modules.materials.schemas import MaterialType, PageMetadata
from metaclass.modules.materials.service import MaterialRichParseResult, MaterialService
from metaclass.modules.paper_workflow.schemas import (
    PaperSourceBundle,
    SourceAsset,
    SourceBlock,
    SourceSection,
)


class PaperSourceBundleBuilder:
    """Project Material/MinerU data into a deterministic, traceable paper bundle."""

    def __init__(self, materials: MaterialService) -> None:
        self.materials = materials

    def build(self, *, material_id: str, workspace: Path) -> PaperSourceBundle:
        material = self.materials.get(material_id)
        if material.file_type != MaterialType.PDF:
            raise ValueError("Paper source bundle requires a PDF material")
        pages = self.materials.pages(material_id)
        if not pages:
            pages = self.materials.parse(material_id)

        source_dir = (workspace / "source").resolve()
        source_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = source_dir / "paper.pdf"
        shutil.copyfile(material.storage_path, pdf_path)
        assets_dir = source_dir / "existing_assets"
        assets_dir.mkdir(exist_ok=True)
        mineru_dir = source_dir / "mineru"
        mineru_dir.mkdir(exist_ok=True)

        rich = self.materials.rich_parse_result(material_id)
        if rich:
            self._copy_sanitized_mineru(rich, mineru_dir)
            raw_items = self._items_from_rich(rich)
        else:
            raw_items = self._items_from_pages(pages)

        blocks, assets = self._normalize_items(raw_items, rich, assets_dir)
        assets.extend(self._extract_captioned_pdf_figures(pdf_path, assets_dir, assets))
        sections = self._sections(blocks)
        page_count = max(
            material.page_count,
            len(pages),
            max((item.page_no for item in [*blocks, *assets]), default=0),
        )
        if page_count < 1:
            raise ValueError("Paper source bundle requires at least one page")

        bundle = PaperSourceBundle(
            material_id=material.id,
            file_hash=f"sha256:{material.file_hash or self._file_hash(pdf_path)}",
            page_count=page_count,
            pdf_path="paper.pdf",
            paper_source_path="paper_source.json",
            paper_content_path="paper_content.md",
            asset_directory="existing_assets",
            parser_source=rich.format if rich else "page_metadata",
            metadata_candidates=self._metadata_candidates(blocks),
            sections=sections,
            blocks=blocks,
            assets=assets,
        )
        source_payload = bundle.model_dump(mode="json")
        (source_dir / bundle.paper_source_path).write_text(
            json.dumps(source_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (source_dir / bundle.paper_content_path).write_text(
            self._markdown(material.filename, blocks, assets), encoding="utf-8"
        )
        return bundle

    @staticmethod
    def _extract_captioned_pdf_figures(
        pdf_path: Path,
        assets_dir: Path,
        existing_assets: list[SourceAsset],
    ) -> list[SourceAsset]:
        """Crop vector figures that PDF embedded-image extraction cannot see.

        The crop is anchored by a Figure caption and the nearest substantial drawing
        rectangle immediately above it. This deliberately excludes tables and avoids
        guessing a crop when the PDF exposes no reliable drawing boundary.
        """
        label_pattern = re.compile(
            r"^\s*(?:figure|fig\.)\s*(\d+[A-Za-z]?)\s*[:.]", re.IGNORECASE
        )
        existing_labels = {
            matched.group(1).lower()
            for asset in existing_assets
            if asset.caption and (matched := label_pattern.match(asset.caption))
        }
        extracted: list[SourceAsset] = []
        document = pymupdf.open(pdf_path)
        try:
            for page in document:
                drawings = [item["rect"] for item in page.get_drawings()]
                for block in page.get_text("blocks"):
                    caption = " ".join(str(block[4]).split())
                    matched = label_pattern.match(caption)
                    if not matched or matched.group(1).lower() in existing_labels:
                        continue
                    caption_rect = pymupdf.Rect(block[:4])
                    candidates = [
                        rect
                        for rect in drawings
                        if rect.width >= 100
                        and rect.height >= 60
                        and rect.y1 <= caption_rect.y0 + 2
                        and 0 <= caption_rect.y0 - rect.y1 <= 50
                    ]
                    if not candidates:
                        continue
                    drawing_rect = max(candidates, key=lambda rect: rect.width * rect.height)
                    clip = pymupdf.Rect(
                        min(drawing_rect.x0, caption_rect.x0) - 6,
                        drawing_rect.y0 - 6,
                        max(drawing_rect.x1, caption_rect.x1) + 6,
                        caption_rect.y1 + 4,
                    ) & page.rect
                    if clip.width < 100 or clip.height < 60:
                        continue
                    label = matched.group(1).lower()
                    asset_id = f"asset_figure_crop_{page.number + 1:03d}_{label}"
                    destination = assets_dir / f"{asset_id}.png"
                    page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=clip, alpha=False).save(
                        destination
                    )
                    extracted.append(
                        SourceAsset(
                            id=asset_id,
                            type="figure",
                            page_no=page.number + 1,
                            path=f"existing_assets/{destination.name}",
                            caption=caption,
                            bbox=(
                                clip.x0 * 1000 / page.rect.width,
                                clip.y0 * 1000 / page.rect.height,
                                clip.x1 * 1000 / page.rect.width,
                                clip.y1 * 1000 / page.rect.height,
                            ),
                            source="pdf",
                        )
                    )
                    existing_labels.add(label)
        finally:
            document.close()
        return extracted

    def bundle_hash(self, bundle: PaperSourceBundle, workspace: Path) -> str:
        """Hash normalized content and copied assets, independent of absolute paths."""
        source_dir = (workspace / "source").resolve()
        digest = hashlib.sha256()
        for relative in [bundle.pdf_path, bundle.paper_source_path, bundle.paper_content_path]:
            self._update_file_hash(digest, source_dir, relative)
        asset_dir = (source_dir / bundle.asset_directory).resolve()
        if asset_dir.is_dir():
            for path in sorted(item for item in asset_dir.rglob("*") if item.is_file()):
                self._update_file_hash(digest, source_dir, str(path.relative_to(source_dir)))
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _items_from_pages(pages: list[PageMetadata]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in sorted(pages, key=lambda item: item.page_no):
            if page.raw_text.strip():
                items.append(
                    {
                        "type": "paragraph",
                        "page_no": page.page_no,
                        "text": page.raw_text,
                        "title": page.title,
                    }
                )
            items.extend(
                {
                    "type": "figure",
                    "page_no": image.page_no,
                    "img_path": image.image_path,
                    "caption": image.description,
                }
                for image in page.embedded_images
            )
        return items

    def _items_from_rich(self, rich: MaterialRichParseResult) -> list[dict[str, Any]]:
        if rich.format.startswith("content_list"):
            payload = rich.payload
            return (
                [item for item in payload if isinstance(item, dict)]
                if isinstance(payload, list)
                else []
            )
        return list(self._walk_middle(rich.payload))

    def _walk_middle(
        self, node: object, inherited_page: int | None = None
    ) -> Iterable[dict[str, Any]]:
        if isinstance(node, list):
            for item in node:
                yield from self._walk_middle(item, inherited_page)
            return
        if not isinstance(node, dict):
            return
        page = self._page_no(node, inherited_page)
        children = self._children(node)
        kind = self._kind(node)
        text = self._text(node)
        if kind or text or self._asset_path(node):
            item = dict(node)
            item["page_no"] = page
            if text:
                item["text"] = text
            yield item
        for child in children:
            yield from self._walk_middle(child, page)

    @staticmethod
    def _children(node: dict[str, Any]) -> list[object]:
        result: list[object] = []
        for key in ("pdf_info", "pages", "para_blocks", "blocks", "children"):
            value = node.get(key)
            if isinstance(value, list):
                result.extend(value)
        return result

    def _normalize_items(
        self,
        items: list[dict[str, Any]],
        rich: MaterialRichParseResult | None,
        assets_dir: Path,
    ) -> tuple[list[SourceBlock], list[SourceAsset]]:
        blocks: list[SourceBlock] = []
        assets: list[SourceAsset] = []
        section_path: list[str] = []
        page_ordinals: dict[int, int] = {}
        asset_ordinals: dict[tuple[int, str], int] = {}
        for item in items:
            page_no = self._page_no(item)
            kind = self._kind(item) or "paragraph"
            text = self._text(item)
            bbox = self._bbox(item)
            if kind == "title" and text:
                level = self._heading_level(item)
                section_path = section_path[: max(level - 1, 0)] + [text]
            if kind in {"figure", "table", "equation"}:
                key = (page_no, kind)
                asset_ordinals[key] = asset_ordinals.get(key, 0) + 1
                asset_id = f"asset_{kind}_{page_no:03d}_{asset_ordinals[key]:03d}"
                relative_path = self._copy_asset(item, rich, assets_dir, asset_id)
                assets.append(
                    SourceAsset(
                        id=asset_id,
                        type=kind,
                        page_no=page_no,
                        path=relative_path,
                        caption=self._caption(item) or text or None,
                        bbox=bbox,
                        source="mineru" if rich else "pdf",
                    )
                )
            block_type = (
                kind
                if kind
                in {
                    "title",
                    "paragraph",
                    "figure",
                    "table",
                    "equation",
                    "algorithm",
                    "code",
                    "reference",
                }
                else "paragraph"
            )
            if not text and block_type not in {"figure", "table", "equation"}:
                continue
            page_ordinals[page_no] = page_ordinals.get(page_no, 0) + 1
            blocks.append(
                SourceBlock(
                    id=f"block_{page_no:03d}_{page_ordinals[page_no]:04d}",
                    type=block_type,
                    page_no=page_no,
                    bbox=bbox,
                    section_path=list(section_path),
                    text=text,
                )
            )
        return blocks, assets

    @staticmethod
    def _page_no(item: dict[str, Any], inherited: int | None = None) -> int:
        for key in ("page_no", "page", "page_number"):
            if key in item:
                try:
                    return max(int(item[key]), 1)
                except (TypeError, ValueError):
                    pass
        if "page_idx" in item:
            try:
                return max(int(item["page_idx"]) + 1, 1)
            except (TypeError, ValueError):
                pass
        return inherited or 1

    @staticmethod
    def _kind(item: dict[str, Any]) -> str:
        raw = str(item.get("type") or item.get("block_type") or item.get("tag") or "").lower()
        aliases = {
            "text": "paragraph",
            "image": "figure",
            "img": "figure",
            "interline_equation": "equation",
            "display_formula": "equation",
            "list": "paragraph",
            "index": "reference",
            "reference": "reference",
            "table": "table",
            "title": "title",
            "paragraph": "paragraph",
            "algorithm": "algorithm",
            "code": "code",
            "equation": "equation",
            "figure": "figure",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _text(item: dict[str, Any]) -> str:
        values: list[str] = []
        for key in ("text", "content", "html", "table_body", "latex"):
            value = item.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                values.append(str(value).strip())
        if not values:
            for key in ("lines", "spans"):
                value = item.get(key)
                if isinstance(value, list):
                    values.extend(
                        str(part.get("content") or part.get("text") or "").strip()
                        for part in value
                        if isinstance(part, dict)
                    )
        return "\n".join(value for value in values if value).strip()

    @staticmethod
    def _caption(item: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in (
            "caption",
            "image_caption",
            "table_caption",
            "footnote",
            "image_footnote",
            "table_footnote",
        ):
            values = item.get(key)
            if isinstance(values, list):
                parts.extend(str(value).strip() for value in values if str(value).strip())
            elif values:
                parts.append(str(values).strip())
        return " ".join(parts)

    @staticmethod
    def _asset_path(item: dict[str, Any]) -> str | None:
        for key in ("img_path", "image_path", "asset_path", "path"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _copy_asset(
        self,
        item: dict[str, Any],
        rich: MaterialRichParseResult | None,
        assets_dir: Path,
        asset_id: str,
    ) -> str:
        raw_path = self._asset_path(item)
        if raw_path:
            candidate = Path(raw_path)
            if not candidate.is_absolute() and rich:
                candidate = rich.root / candidate
            elif not candidate.is_absolute():
                candidate = self.materials.data_dir / candidate
            try:
                resolved = candidate.resolve()
                allowed_root = rich.root.resolve() if rich else self.materials.data_dir.resolve()
                resolved.relative_to(allowed_root)
                if resolved.is_file():
                    suffix = resolved.suffix.lower() or ".bin"
                    destination = assets_dir / f"{asset_id}{suffix}"
                    shutil.copyfile(resolved, destination)
                    return f"existing_assets/{destination.name}"
            except (OSError, ValueError):
                pass
        destination = assets_dir / f"{asset_id}.json"
        destination.write_text(
            json.dumps(
                {"type": self._kind(item), "content": self._text(item)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return f"existing_assets/{destination.name}"

    @staticmethod
    def _bbox(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
        value = item.get("bbox") or item.get("box")
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            coordinates = [float(number) for number in value]
        except (TypeError, ValueError):
            return None
        width = item.get("page_width") or item.get("width")
        height = item.get("page_height") or item.get("height")
        try:
            if float(width) > 0 and float(height) > 0:
                coordinates = [
                    coordinates[0] * 1000 / float(width),
                    coordinates[1] * 1000 / float(height),
                    coordinates[2] * 1000 / float(width),
                    coordinates[3] * 1000 / float(height),
                ]
        except (TypeError, ValueError):
            pass
        return tuple(max(0.0, min(number, 1000.0)) for number in coordinates)

    @staticmethod
    def _heading_level(item: dict[str, Any]) -> int:
        try:
            return max(1, min(int(item.get("level", item.get("text_level", 1))), 6))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _sections(blocks: list[SourceBlock]) -> list[SourceSection]:
        headings = [(index, block) for index, block in enumerate(blocks) if block.type == "title"]
        result: list[SourceSection] = []
        for ordinal, (index, block) in enumerate(headings, start=1):
            next_index = headings[ordinal][0] if ordinal < len(headings) else len(blocks)
            end_page = max(
                (item.page_no for item in blocks[index:next_index]), default=block.page_no
            )
            result.append(
                SourceSection(
                    id=f"section_{ordinal:03d}",
                    title=block.text,
                    level=len(block.section_path) or 1,
                    start_page=block.page_no,
                    end_page=end_page,
                )
            )
        return result

    @staticmethod
    def _metadata_candidates(blocks: list[SourceBlock]) -> dict[str, object]:
        title = next((block.text for block in blocks if block.type == "title"), None)
        return {"title": title} if title else {}

    @staticmethod
    def _markdown(filename: str, blocks: list[SourceBlock], assets: list[SourceAsset]) -> str:
        assets_by_page_type: dict[tuple[int, str], list[SourceAsset]] = {}
        for asset in assets:
            assets_by_page_type.setdefault((asset.page_no, asset.type), []).append(asset)
        asset_indexes: dict[tuple[int, str], int] = {}
        lines = [
            "---",
            f'source_filename: "{filename}"',
            "provenance: paper_source.json",
            "---",
            "",
        ]
        for block in blocks:
            lines.append(f"<!-- source: page={block.page_no} block={block.id} -->")
            if block.type == "title":
                lines.extend([f"{'#' * min(max(len(block.section_path), 1), 6)} {block.text}", ""])
            elif block.type in {"figure", "table", "equation"}:
                key = (block.page_no, block.type)
                index = asset_indexes.get(key, 0)
                candidates = assets_by_page_type.get(key, [])
                asset = candidates[index] if index < len(candidates) else None
                asset_indexes[key] = index + 1
                if asset:
                    lines.append(f"<!-- {block.type}: {asset.id} page={asset.page_no} -->")
                    if block.type == "figure":
                        lines.append(f"![{asset.caption or asset.id}]({asset.path})")
                    else:
                        lines.append(f"[{block.type.upper()} {asset.id}]({asset.path})")
                    if asset.caption:
                        lines.append(f"Caption: {asset.caption}")
                lines.extend([block.text, ""])
            else:
                lines.extend([block.text, ""])
        return "\n".join(lines).rstrip() + "\n"

    def _copy_sanitized_mineru(self, rich: MaterialRichParseResult, destination: Path) -> None:
        payload = self._sanitize_paths(rich.payload, rich.root)
        (destination / rich.path.name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _sanitize_paths(self, value: object, root: Path) -> object:
        if isinstance(value, dict):
            return {str(key): self._sanitize_paths(item, root) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_paths(item, root) for item in value]
        if isinstance(value, str) and Path(value).is_absolute():
            try:
                return str(Path(value).resolve().relative_to(root.resolve()))
            except ValueError:
                return Path(value).name
        return value

    @staticmethod
    def _update_file_hash(digest: Any, source_dir: Path, relative: str) -> None:
        path = (source_dir / relative).resolve()
        path.relative_to(source_dir)
        digest.update(relative.encode("utf-8"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256(path.read_bytes())
        return digest.hexdigest()
