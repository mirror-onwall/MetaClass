from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import FileResponse

from metaclass.modules.video.schemas import (
    TTSArtifact,
    TTSArtifactRequest,
    VideoJob,
    VideoResult,
)
from metaclass.modules.video.service import VideoService


def create_router(videos: VideoService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["video"])

    @router.post(
        "/learning-contents/{content_id}/videos",
        response_model=VideoJob,
        status_code=202,
    )
    async def create_video_job(
        content_id: str,
        background_tasks: BackgroundTasks,
        presentation_artifact_id: str | None = None,
        presentation_plan_id: str | None = None,
    ) -> VideoJob:
        job = videos.queue_job(content_id)
        background_tasks.add_task(
            videos.run_job,
            job.id,
            presentation_artifact_id,
            presentation_plan_id,
        )
        return job

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

    @router.post("/tts-artifacts", response_model=TTSArtifact, status_code=201)
    def create_tts_artifact(request: TTSArtifactRequest) -> TTSArtifact:
        return videos.create_tts_artifact(request)

    @router.get("/tts-artifacts/{artifact_id}", response_model=TTSArtifact)
    async def get_tts_artifact(artifact_id: str) -> TTSArtifact:
        return videos.get_tts_artifact(artifact_id)

    @router.get("/tts-artifacts/{artifact_id}/audio")
    async def get_tts_audio(artifact_id: str) -> FileResponse:
        artifact = videos.get_tts_artifact(artifact_id)
        return FileResponse(
            artifact.audio_path,
            media_type="audio/wav",
            filename=f"{artifact.id}.wav",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    return router
