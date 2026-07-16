import hashlib
import json
import os
import shlex
import subprocess
import tempfile
from threading import Lock
from pathlib import Path
from uuid import uuid4

import fitz
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageDraw
from pptx import Presentation

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.storage import safe_filename, save_upload
from metaclass.modules.materials.repository import MaterialRepository
from metaclass.modules.materials.schemas import (
    Material,
    MaterialCollection,
    MaterialProcessingJob,
    MaterialProcessingJobStatus,
    PageMetadata,
    PageImage,
    ProcessedMaterial,
    ProcessedMaterials,
    SourceRef,
)


class MaterialService:
    def __init__(
        self,
        data_dir: Path,
        repository: MaterialRepository,
        *,
        parser_backend: str = "auto",
        mineru_command: str = "mineru",
        mineru_timeout_seconds: float = 180.0,
    ) -> None:
        self.data_dir = data_dir
        self.repository = repository
        self.parser_backend = parser_backend
        self.mineru_command = mineru_command
        self.mineru_timeout_seconds = mineru_timeout_seconds
        self._jobs: dict[str, MaterialProcessingJob] = {}
        self._job_lock = Lock()

    async def create(self, upload: UploadFile) -> Material:
        filename = safe_filename(upload.filename)
        suffix = Path(filename).suffix.lower().lstrip(".")
        if suffix not in {"pdf", "pptx"}:
            raise HTTPException(415, "Only PDF and PPTX files are supported")
        material_id = f"mat_{uuid4().hex[:12]}"
        path = self.data_dir / "raw" / material_id / f"source.{suffix}"
        await save_upload(upload, path)
        file_hash = self._sha256(path)
        material = Material(
            id=material_id,
            filename=filename,
            file_type=suffix,
            file_hash=file_hash,
            storage_path=str(path),
        )
        self.repository.save_material(material)
        return material

    def list_materials(self) -> list[Material]:
        return self.repository.list_materials()

    def get(self, material_id: str) -> Material:
        material = self.repository.get_material(material_id)
        if not material:
            raise HTTPException(404, "Material not found")
        return material

    def create_collection(
        self,
        title: str,
        material_ids: list[str],
        primary_material_id: str | None = None,
    ) -> MaterialCollection:
        if not material_ids:
            raise HTTPException(400, "Material collection requires at least one material")
        for material_id in material_ids:
            self.get(material_id)
        primary = primary_material_id or material_ids[0]
        if primary not in material_ids:
            raise HTTPException(400, "Primary material must be included in material_ids")
        collection = MaterialCollection(
            id=f"col_{uuid4().hex[:12]}",
            title=title,
            material_ids=material_ids,
            primary_material_id=primary,
        )
        self.repository.save_collection(collection)
        return collection

    def get_collection(self, collection_id: str) -> MaterialCollection:
        collection = self.repository.get_collection(collection_id)
        if not collection:
            raise HTTPException(404, "Material collection not found")
        return collection

    def list_collections(self) -> list[MaterialCollection]:
        return self.repository.list_collections()

    async def create_processing_job(
        self,
        uploads: list[UploadFile],
        *,
        title: str = "上传课程资料集",
    ) -> MaterialProcessingJob:
        if not uploads:
            raise HTTPException(400, "Processing job requires at least one material")
        materials = [await self.create(upload) for upload in uploads]
        collection = self.create_collection(title, [material.id for material in materials])
        job = MaterialProcessingJob(
            id=f"mat_job_{uuid4().hex[:12]}",
            status=MaterialProcessingJobStatus.QUEUED,
            progress=0,
            step="queued",
            message="Waiting to process materials",
            material_ids=[material.id for material in materials],
            collection_id=collection.id,
        )
        self._save_job(job)
        return job

    def get_processing_job(self, job_id: str) -> MaterialProcessingJob:
        with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise HTTPException(404, "Material processing job not found")
            return job.model_copy(deep=True)

    def processing_job_result(self, job_id: str) -> ProcessedMaterials:
        job = self.get_processing_job(job_id)
        if job.status == MaterialProcessingJobStatus.FAILED:
            raise HTTPException(422, job.error or "Material processing job failed")
        if job.status != MaterialProcessingJobStatus.SUCCEEDED:
            raise HTTPException(409, "Material processing job is not finished")
        collection = self.get_collection(job.collection_id) if job.collection_id else None
        items = [
            ProcessedMaterial(material=self.get(material_id), pages=self.pages(material_id))
            for material_id in job.material_ids
        ]
        return ProcessedMaterials(items=items, collection=collection)

    def run_processing_job(self, job_id: str) -> None:
        job = self.get_processing_job(job_id)
        try:
            total = max(len(job.material_ids), 1)
            job.status = MaterialProcessingJobStatus.RUNNING
            job.step = "parsing"
            job.message = "Parsing uploaded materials"
            job.progress = 5
            job.updated_at = utc_now()
            self._save_job(job)
            for index, material_id in enumerate(job.material_ids, start=1):
                material = self.get(material_id)
                job.step = f"parsing:{material.filename}"
                job.message = f"Parsing {material.filename}"
                job.progress = min(95, 5 + int(((index - 1) / total) * 90))
                job.updated_at = utc_now()
                self._save_job(job)
                self.parse(material_id)
                job.progress = min(95, 5 + int((index / total) * 90))
                job.updated_at = utc_now()
                self._save_job(job)
            job.status = MaterialProcessingJobStatus.SUCCEEDED
            job.step = "completed"
            job.message = "Material processing completed"
            job.progress = 100
            job.updated_at = utc_now()
            self._save_job(job)
        except Exception as exc:
            job.status = MaterialProcessingJobStatus.FAILED
            job.step = "failed"
            job.message = "Material processing failed"
            job.progress = 100
            job.error = str(exc)
            job.updated_at = utc_now()
            self._save_job(job)

    def pages(self, material_id: str) -> list[PageMetadata]:
        self.get(material_id)
        return self.repository.list_pages(material_id)

    def parse(self, material_id: str) -> list[PageMetadata]:
        material = self.get(material_id)
        material.status = "parsing"
        material.updated_at = utc_now()
        self.repository.save_material(material)
        try:
            pages = (
                self._parse_pdf(material)
                if material.file_type == "pdf"
                else self._parse_pptx(material)
            )
            material.status = "parsed"
            material.page_count = len(pages)
            material.error = None
            material.updated_at = utc_now()
            self.repository.save_material(material)
            return pages
        except Exception as exc:
            material.status = "failed"
            material.error = str(exc)
            material.updated_at = utc_now()
            self.repository.save_material(material)
            raise HTTPException(422, f"Material parsing failed: {exc}") from exc

    def _save_job(self, job: MaterialProcessingJob) -> None:
        with self._job_lock:
            self._jobs[job.id] = job.model_copy(deep=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _parse_pdf(self, material: Material) -> list[PageMetadata]:
        if self.parser_backend in {"auto", "mineru"}:
            try:
                return self._parse_pdf_with_mineru(material)
            except Exception:
                if self.parser_backend == "mineru":
                    raise

        return self._parse_pdf_locally(material)

    def _parse_pdf_with_mineru(self, material: Material) -> list[PageMetadata]:
        output_dir = self.data_dir / "processed" / material.id / "mineru"
        output_dir.mkdir(parents=True, exist_ok=True)
        command = self._mineru_command()
        subprocess.run(
            [*command, "-p", material.storage_path, "-o", str(output_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=self.mineru_timeout_seconds,
        )
        pages = self._metadata_from_mineru_output(material, output_dir)
        if not pages:
            raise ValueError("MinerU did not produce page metadata")
        return self._save_pages(material.id, pages)

    def _parse_pdf_locally(self, material: Material) -> list[PageMetadata]:
        document = fitz.open(material.storage_path)
        images_dir = self.data_dir / "processed" / material.id / "pages"
        images_dir.mkdir(parents=True, exist_ok=True)
        embedded_images = self._extract_pdf_embedded_images(material)
        result = []
        for index, page in enumerate(document):
            number = index + 1
            text = page.get_text("text").strip()
            image_path = images_dir / f"page_{number:03d}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(image_path)
            result.append(
                self._metadata(
                    material.id,
                    number,
                    text,
                    image_path,
                    embedded_images=embedded_images.get(number, []),
                )
            )
        document.close()
        return self._save_pages(material.id, result)

    def _metadata_from_mineru_output(
        self, material: Material, output_dir: Path
    ) -> list[PageMetadata]:
        page_images = self._render_pdf_pages(material)
        embedded_images = self._extract_pdf_embedded_images(material)
        content_list = self._load_mineru_content_list(output_dir)
        if content_list:
            texts_by_page = self._mineru_text_by_page(content_list)
        else:
            texts_by_page = self._mineru_markdown_by_page(output_dir)

        pages = []
        max_page = max(len(page_images), max(texts_by_page.keys(), default=0))
        for number in range(1, max_page + 1):
            image_path = (
                page_images[number - 1]
                if number <= len(page_images)
                else self._placeholder(
                    self.data_dir / "processed" / material.id / "pages", number
                )
            )
            pages.append(
                self._metadata(
                    material.id,
                    number,
                    texts_by_page.get(number, ""),
                    image_path,
                    embedded_images=embedded_images.get(number, []),
                )
            )
        return pages

    def _extract_pdf_embedded_images(self, material: Material) -> dict[int, list[PageImage]]:
        output_dir = self.data_dir / "processed" / material.id / "embedded_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        result: dict[int, list[PageImage]] = {}
        document = fitz.open(material.storage_path)
        try:
            for page_index, page in enumerate(document):
                page_no = page_index + 1
                seen_xrefs: set[int] = set()
                for image_index, image_info in enumerate(page.get_images(full=True), start=1):
                    xref = int(image_info[0])
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    try:
                        extracted = document.extract_image(xref)
                    except (RuntimeError, ValueError):
                        continue
                    image_bytes = extracted.get("image")
                    if not image_bytes:
                        continue
                    width = int(extracted.get("width") or 0)
                    height = int(extracted.get("height") or 0)
                    if width <= 1 or height <= 1:
                        continue
                    extension = str(extracted.get("ext") or "png").lower()
                    if extension == "jpeg":
                        extension = "jpg"
                    image_id = f"{material.id}_page_{page_no:03d}_image_{image_index:02d}"
                    image_path = output_dir / f"page_{page_no:03d}_image_{image_index:02d}.{extension}"
                    image_path.write_bytes(image_bytes)
                    result.setdefault(page_no, []).append(
                        PageImage(
                            id=image_id,
                            material_id=material.id,
                            page_id=f"{material.id}_page_{page_no:03d}",
                            page_no=page_no,
                            image_path=str(image_path),
                            width=width,
                            height=height,
                            description=f"Embedded image {image_index} from page {page_no}.",
                        )
                    )
        finally:
            document.close()
        return result

    def _render_pdf_pages(self, material: Material) -> list[Path]:
        document = fitz.open(material.storage_path)
        images_dir = self.data_dir / "processed" / material.id / "pages"
        images_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for index, page in enumerate(document):
            image_path = images_dir / f"page_{index + 1:03d}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(image_path)
            paths.append(image_path)
        document.close()
        return paths

    def _load_mineru_content_list(self, output_dir: Path) -> list[dict] | None:
        for path in sorted(output_dir.rglob("*content_list*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
        return None

    def _mineru_text_by_page(self, content_list: list[dict]) -> dict[int, str]:
        pages: dict[int, list[str]] = {}
        for item in content_list:
            page_no = self._mineru_page_no(item)
            text = self._mineru_item_text(item)
            if text:
                pages.setdefault(page_no, []).append(text)
        return {page_no: "\n".join(parts).strip() for page_no, parts in pages.items()}

    @staticmethod
    def _mineru_page_no(item: dict) -> int:
        value = item.get("page_no", item.get("page", item.get("page_idx", 0)))
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 1
        return number + 1 if "page_idx" in item and "page_no" not in item else max(number, 1)

    @staticmethod
    def _mineru_item_text(item: dict) -> str:
        candidates = [
            item.get("text"),
            item.get("html"),
            item.get("table_body"),
            item.get("latex"),
            item.get("caption"),
        ]
        return "\n".join(str(value).strip() for value in candidates if value).strip()

    def _mineru_markdown_by_page(self, output_dir: Path) -> dict[int, str]:
        markdown_files = sorted(output_dir.rglob("*.md"))
        if not markdown_files:
            return {}
        text = markdown_files[0].read_text(encoding="utf-8").strip()
        if not text:
            return {}
        parts = [part.strip() for part in text.split("\f")]
        return {index + 1: part for index, part in enumerate(parts) if part}

    def _mineru_command(self) -> list[str]:
        return shlex.split(self.mineru_command, posix=os.name != "nt") or ["mineru"]

    def _parse_pptx(self, material: Material) -> list[PageMetadata]:
        presentation = Presentation(material.storage_path)
        texts = []
        for slide in presentation.slides:
            texts.append("\n".join(shape.text for shape in slide.shapes if hasattr(shape, "text")))
        images = self._render_pptx(material, len(texts))
        pages = [
            self._metadata(material.id, index + 1, text, images[index])
            for index, text in enumerate(texts)
        ]
        return self._save_pages(material.id, pages)

    def _render_pptx(self, material: Material, page_count: int) -> list[Path]:
        images_dir = self.data_dir / "processed" / material.id / "pages"
        images_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                subprocess.run(
                    [
                        "soffice",
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        temp_dir,
                        material.storage_path,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                pdf_path = Path(temp_dir) / f"{Path(material.storage_path).stem}.pdf"
                document = fitz.open(pdf_path)
                paths = []
                for index, page in enumerate(document):
                    path = images_dir / f"page_{index + 1:03d}.png"
                    page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(path)
                    paths.append(path)
                document.close()
                if len(paths) == page_count:
                    return paths
        except (FileNotFoundError, subprocess.SubprocessError, fitz.FileDataError):
            pass
        return [self._placeholder(images_dir, index + 1) for index in range(page_count)]

    @staticmethod
    def _placeholder(directory: Path, number: int) -> Path:
        path = directory / f"page_{number:03d}.png"
        image = Image.new("RGB", (1280, 720), "white")
        ImageDraw.Draw(image).text((60, 60), f"Slide {number}", fill="black")
        image.save(path)
        return path

    def _metadata(
        self,
        material_id: str,
        number: int,
        text: str,
        image_path: Path,
        *,
        embedded_images: list[PageImage] | None = None,
    ) -> PageMetadata:
        title = next(
            (line.strip() for line in text.splitlines() if line.strip()), f"第 {number} 页"
        )
        ref = SourceRef(
            material_id=material_id,
            page_id=f"{material_id}_page_{number:03d}",
            page_no=number,
            text_span=text[:120] or None,
            image_path=str(image_path),
        )
        return PageMetadata(
            id=f"{material_id}_page_{number:03d}",
            material_id=material_id,
            page_no=number,
            title=title[:100],
            raw_text=text,
            image_path=str(image_path),
            embedded_images=embedded_images or [],
            source_refs=[ref],
        )

    def _save_pages(self, material_id: str, pages: list[PageMetadata]) -> list[PageMetadata]:
        self.repository.replace_pages(material_id, pages)
        return pages
