import subprocess
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg
from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.providers.base import TTSProvider
from metaclass.modules.content.service import ContentService
from metaclass.modules.video.repository import VideoRepository
from metaclass.modules.video.schemas import TTSArtifact, TTSArtifactRequest, VideoJob, VideoResult


def srt_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


class VideoService:
    def __init__(
        self,
        data_dir: Path,
        repository: VideoRepository,
        contents: ContentService,
        tts: TTSProvider,
    ) -> None:
        self.data_dir = data_dir
        self.repository = repository
        self.contents = contents
        self.tts = tts

    def create_job(self, content_id: str) -> VideoJob:
        self.contents.get(content_id)
        job = VideoJob(id=f"video_job_{uuid4().hex[:12]}", content_id=content_id)
        self.repository.save_job(job)

        job.status = "running"
        job.updated_at = utc_now()
        self.repository.save_job(job)
        try:
            result = self._generate(job)
            self.repository.save_result(result)
            job.result_id = result.id
            job.progress = 1.0
            job.status = "finished"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
        job.updated_at = utc_now()
        self.repository.save_job(job)
        return job

    def get_job(self, job_id: str) -> VideoJob:
        job = self.repository.get_job(job_id)
        if not job:
            raise HTTPException(404, "Video job not found")
        return job

    def get_result(self, result_id: str) -> VideoResult:
        result = self.repository.get_result(result_id)
        if not result:
            raise HTTPException(404, "Video result not found")
        return result

    def get_result_for_job(self, job_id: str) -> VideoResult:
        self.get_job(job_id)
        result = self.repository.get_result_for_job(job_id)
        if not result:
            raise HTTPException(409, "Video result is not available")
        return result

    def create_tts_artifact(self, request: TTSArtifactRequest) -> TTSArtifact:
        artifact_id = f"tts_artifact_{uuid4().hex[:12]}"
        directory = self.data_dir / "generated" / "tts" / artifact_id
        audio_path = directory / "audio.wav"
        duration = self.tts.synthesize(request.text, audio_path)
        duration_ms = round(duration * 1000)
        artifact = TTSArtifact(
            id=artifact_id,
            text=request.text,
            scope=request.scope,
            ref_id=request.ref_id,
            voice=request.voice,
            audio_path=str(audio_path),
            audio_url=f"/api/v1/tts-artifacts/{artifact_id}/audio",
            duration_ms=duration_ms,
            duration_seconds=duration,
        )
        self.repository.save_tts_artifact(artifact)
        return artifact

    def get_tts_artifact(self, artifact_id: str) -> TTSArtifact:
        artifact = self.repository.get_tts_artifact(artifact_id)
        if not artifact:
            raise HTTPException(404, "TTS artifact not found")
        return artifact

    def _generate(self, job: VideoJob) -> VideoResult:
        content = self.contents.get(job.content_id)
        if not content.sections:
            raise ValueError("Cannot generate video from empty learning content")
        result_id = f"video_result_{uuid4().hex[:12]}"
        directory = self.data_dir / "generated" / "videos" / job.id
        directory.mkdir(parents=True, exist_ok=True)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        segments = []
        subtitles = []
        cursor = 0.0
        for index, section in enumerate(content.sections, start=1):
            text = f"{section.title}。{section.summary}"
            audio = directory / f"audio_{index:03d}.wav"
            duration = self.tts.synthesize(text, audio)
            image = section.source_refs[0].image_path
            if not image:
                raise ValueError(f"Section {section.id} has no source image")
            segment = directory / f"segment_{index:03d}.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    image,
                    "-i",
                    str(audio),
                    "-t",
                    str(duration),
                    "-vf",
                    "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(segment),
                ],
                check=True,
                capture_output=True,
            )
            segments.append(segment)
            subtitles.append(
                f"{index}\n{srt_timestamp(cursor)} --> {srt_timestamp(cursor + duration)}\n{text}\n"
            )
            cursor += duration

        subtitles_path = directory / "subtitles.srt"
        subtitles_path.write_text("\n".join(subtitles), encoding="utf-8")
        concat_path = directory / "concat.txt"
        concat_path.write_text(
            "\n".join(f"file '{segment.name}'" for segment in segments), encoding="utf-8"
        )
        base_video = directory / "video_without_subtitles.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(base_video),
            ],
            check=True,
            capture_output=True,
        )
        output = directory / "output.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(base_video),
                "-i",
                str(subtitles_path),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                "mov_text",
                str(output),
            ],
            check=True,
            capture_output=True,
        )
        return VideoResult(
            id=result_id,
            job_id=job.id,
            content_id=job.content_id,
            video_path=str(output),
            subtitles_path=str(subtitles_path),
            duration_seconds=cursor,
        )
