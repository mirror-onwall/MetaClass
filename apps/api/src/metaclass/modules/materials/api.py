from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from metaclass.modules.materials.schemas import (
    Material,
    PageMetadata,
    ProcessedMaterial,
    ProcessedMaterials,
)
from metaclass.modules.materials.service import MaterialService


def create_router(materials: MaterialService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/materials", tags=["materials"])

    @router.post("", response_model=Material, status_code=201)
    async def upload_material(file: UploadFile = File(...)) -> Material:
        return await materials.create(file)

    @router.post("/process", response_model=ProcessedMaterial, status_code=201)
    async def upload_and_parse_material(file: UploadFile = File(...)) -> ProcessedMaterial:
        material = await materials.create(file)
        pages = materials.parse(material.id)
        return ProcessedMaterial(material=materials.get(material.id), pages=pages)

    @router.post("/batch-process", response_model=ProcessedMaterials, status_code=201)
    async def upload_and_parse_materials(
        files: list[UploadFile] = File(...),
    ) -> ProcessedMaterials:
        processed: list[ProcessedMaterial] = []
        for file in files:
            material = await materials.create(file)
            pages = materials.parse(material.id)
            processed.append(ProcessedMaterial(material=materials.get(material.id), pages=pages))
        return ProcessedMaterials(items=processed)

    @router.get("/{material_id}", response_model=Material)
    async def get_material(material_id: str) -> Material:
        return materials.get(material_id)

    @router.post("/{material_id}/parse", response_model=list[PageMetadata])
    async def parse_material(material_id: str) -> list[PageMetadata]:
        return materials.parse(material_id)

    @router.get("/{material_id}/pages", response_model=list[PageMetadata])
    async def list_pages(material_id: str) -> list[PageMetadata]:
        return materials.pages(material_id)

    @router.get("/{material_id}/pages/{page_number}/image")
    async def get_page_image(material_id: str, page_number: int) -> FileResponse:
        page = next(
            (item for item in materials.pages(material_id) if item.page_no == page_number),
            None,
        )
        if not page:
            raise HTTPException(404, "Page not found")
        return FileResponse(page.image_path, media_type="image/png")

    return router
