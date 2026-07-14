from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from metaclass.modules.materials.schemas import (
    Material,
    MaterialCollection,
    MaterialCollectionCreate,
    MaterialProcessingJob,
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

    @router.get("", response_model=list[Material])
    async def list_materials() -> list[Material]:
        return materials.list_materials()

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
        collection = materials.create_collection(
            title="上传课程资料集",
            material_ids=[item.material.id for item in processed],
        )
        return ProcessedMaterials(items=processed, collection=collection)

    @router.post("/processing-jobs", response_model=MaterialProcessingJob, status_code=202)
    async def create_processing_job(
        background_tasks: BackgroundTasks,
        files: list[UploadFile] = File(...),
    ) -> MaterialProcessingJob:
        job = await materials.create_processing_job(files)
        background_tasks.add_task(materials.run_processing_job, job.id)
        return job

    @router.get("/processing-jobs/{job_id}", response_model=MaterialProcessingJob)
    async def get_processing_job(job_id: str) -> MaterialProcessingJob:
        return materials.get_processing_job(job_id)

    @router.get("/processing-jobs/{job_id}/result", response_model=ProcessedMaterials)
    async def get_processing_job_result(job_id: str) -> ProcessedMaterials:
        return materials.processing_job_result(job_id)

    @router.post("/collections", response_model=MaterialCollection, status_code=201)
    async def create_collection(payload: MaterialCollectionCreate) -> MaterialCollection:
        return materials.create_collection(
            payload.title,
            payload.material_ids,
            payload.primary_material_id,
        )

    @router.get("/collections", response_model=list[MaterialCollection])
    async def list_collections() -> list[MaterialCollection]:
        return materials.list_collections()

    @router.get("/collections/{collection_id}", response_model=MaterialCollection)
    async def get_collection(collection_id: str) -> MaterialCollection:
        return materials.get_collection(collection_id)

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
