from fastapi import APIRouter
from fastapi.responses import FileResponse

from metaclass.modules.video.schemas import VideoJob, VideoResult
from metaclass.modules.video.service import VideoService


def create_router(videos: VideoService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["video"])

    @router.post(
        "/learning-contents/{content_id}/videos",
        response_model=VideoJob,
        status_code=201,
    )
    async def create_video_job(content_id: str) -> VideoJob:
        return videos.create_job(content_id)

    @router.get("/video-jobs/{job_id}", response_model=VideoJob)
    async def get_video_job(job_id: str) -> VideoJob:
        return videos.get_job(job_id)

    @router.get("/video-jobs/{job_id}/result", response_model=VideoResult)
    async def get_video_job_result(job_id: str) -> VideoResult:
        return videos.get_result_for_job(job_id)

    @router.get("/videos/{result_id}", response_model=VideoResult)
    async def get_video_result(result_id: str) -> VideoResult:
        return videos.get_result(result_id)

    @router.get("/videos/{result_id}/download")
    async def download_video(result_id: str) -> FileResponse:
        result = videos.get_result(result_id)
        return FileResponse(
            result.video_path,
            media_type="video/mp4",
            filename=f"{result.id}.mp4",
        )

    return router
