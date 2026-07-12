from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select

from metaclass.infrastructure.database import Database
from metaclass.modules.video.models import TTSArtifactRecord, VideoJobRecord, VideoResultRecord
from metaclass.modules.video.schemas import TTSArtifact, VideoJob, VideoResult


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class VideoRepository(Protocol):
    def save_job(self, job: VideoJob) -> None: ...

    def get_job(self, job_id: str) -> VideoJob | None: ...

    def save_result(self, result: VideoResult) -> None: ...

    def get_result(self, result_id: str) -> VideoResult | None: ...

    def get_result_for_job(self, job_id: str) -> VideoResult | None: ...

    def save_tts_artifact(self, artifact: TTSArtifact) -> None: ...

    def get_tts_artifact(self, artifact_id: str) -> TTSArtifact | None: ...


class SqlAlchemyVideoRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_job(self, job: VideoJob) -> None:
        with self.database.session() as session:
            session.merge(
                VideoJobRecord(
                    id=job.id,
                    content_id=job.content_id,
                    status=job.status.value,
                    progress=job.progress,
                    result_id=job.result_id,
                    error=job.error,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
            )

    def get_job(self, job_id: str) -> VideoJob | None:
        with self.database.session() as session:
            record = session.get(VideoJobRecord, job_id)
            return self._job(record) if record else None

    def save_result(self, result: VideoResult) -> None:
        with self.database.session() as session:
            session.merge(
                VideoResultRecord(
                    id=result.id,
                    job_id=result.job_id,
                    content_id=result.content_id,
                    video_path=result.video_path,
                    subtitles_path=result.subtitles_path,
                    duration_seconds=result.duration_seconds,
                    created_at=result.created_at,
                )
            )

    def get_result(self, result_id: str) -> VideoResult | None:
        with self.database.session() as session:
            record = session.get(VideoResultRecord, result_id)
            return self._result(record) if record else None

    def get_result_for_job(self, job_id: str) -> VideoResult | None:
        with self.database.session() as session:
            record = session.scalar(
                select(VideoResultRecord).where(VideoResultRecord.job_id == job_id)
            )
            return self._result(record) if record else None

    def save_tts_artifact(self, artifact: TTSArtifact) -> None:
        with self.database.session() as session:
            session.merge(
                TTSArtifactRecord(
                    id=artifact.id,
                    text=artifact.text,
                    scope=artifact.scope,
                    ref_id=artifact.ref_id,
                    voice=artifact.voice,
                    audio_path=artifact.audio_path,
                    audio_url=artifact.audio_url,
                    duration_ms=artifact.duration_ms,
                    duration_seconds=artifact.duration_seconds,
                    created_at=artifact.created_at,
                )
            )

    def get_tts_artifact(self, artifact_id: str) -> TTSArtifact | None:
        with self.database.session() as session:
            record = session.get(TTSArtifactRecord, artifact_id)
            return self._tts_artifact(record) if record else None

    @staticmethod
    def _job(record: VideoJobRecord) -> VideoJob:
        return VideoJob(
            id=record.id,
            content_id=record.content_id,
            status=record.status,
            progress=record.progress,
            result_id=record.result_id,
            error=record.error,
            created_at=ensure_utc(record.created_at),
            updated_at=ensure_utc(record.updated_at),
        )

    @staticmethod
    def _result(record: VideoResultRecord) -> VideoResult:
        return VideoResult(
            id=record.id,
            job_id=record.job_id,
            content_id=record.content_id,
            video_path=record.video_path,
            subtitles_path=record.subtitles_path,
            duration_seconds=record.duration_seconds,
            created_at=ensure_utc(record.created_at),
        )

    @staticmethod
    def _tts_artifact(record: TTSArtifactRecord) -> TTSArtifact:
        return TTSArtifact(
            id=record.id,
            text=record.text,
            scope=record.scope,
            ref_id=record.ref_id,
            voice=record.voice,
            audio_path=record.audio_path,
            audio_url=record.audio_url,
            duration_ms=record.duration_ms,
            duration_seconds=record.duration_seconds,
            created_at=ensure_utc(record.created_at),
        )
