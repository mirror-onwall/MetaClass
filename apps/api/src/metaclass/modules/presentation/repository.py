from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select

from metaclass.infrastructure.database import Database
from metaclass.modules.presentation.models import (
    PPTArtifactRecord,
    PPTGenerationJobRecord,
    PresentationPlanRecord,
)
from metaclass.modules.presentation.schemas import (
    PPTArtifact,
    PPTGenerationJob,
    PPTSlideImage,
    PresentationPlan,
    SlidePlan,
)


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class PresentationRepository(Protocol):
    def save_plan(self, plan: PresentationPlan) -> None: ...

    def get_plan(self, plan_id: str) -> PresentationPlan | None: ...

    def get_plan_for_content(self, content_id: str) -> PresentationPlan | None: ...

    def save_job(self, job: PPTGenerationJob) -> None: ...

    def get_job(self, job_id: str) -> PPTGenerationJob | None: ...

    def save_artifact(self, artifact: PPTArtifact) -> None: ...

    def get_artifact(self, artifact_id: str) -> PPTArtifact | None: ...

    def get_artifact_for_job(self, job_id: str) -> PPTArtifact | None: ...


class SqlAlchemyPresentationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_plan(self, plan: PresentationPlan) -> None:
        with self.database.session() as session:
            session.merge(
                PresentationPlanRecord(
                    id=plan.id,
                    content_id=plan.content_id,
                    title=plan.title,
                    slides=[slide.model_dump(mode="json") for slide in plan.slides],
                    created_at=plan.created_at,
                    updated_at=plan.updated_at,
                )
            )

    def get_plan(self, plan_id: str) -> PresentationPlan | None:
        with self.database.session() as session:
            record = session.get(PresentationPlanRecord, plan_id)
            return self._plan(record) if record else None

    def get_plan_for_content(self, content_id: str) -> PresentationPlan | None:
        with self.database.session() as session:
            record = session.scalar(
                select(PresentationPlanRecord)
                .where(PresentationPlanRecord.content_id == content_id)
                .order_by(PresentationPlanRecord.created_at.desc())
            )
            return self._plan(record) if record else None

    def save_job(self, job: PPTGenerationJob) -> None:
        with self.database.session() as session:
            session.merge(
                PPTGenerationJobRecord(
                    id=job.id,
                    presentation_plan_id=job.presentation_plan_id,
                    status=job.status.value,
                    progress=job.progress,
                    artifact_id=job.artifact_id,
                    error=job.error,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
            )

    def get_job(self, job_id: str) -> PPTGenerationJob | None:
        with self.database.session() as session:
            record = session.get(PPTGenerationJobRecord, job_id)
            return self._job(record) if record else None

    def save_artifact(self, artifact: PPTArtifact) -> None:
        with self.database.session() as session:
            session.merge(
                PPTArtifactRecord(
                    id=artifact.id,
                    job_id=artifact.job_id,
                    presentation_plan_id=artifact.presentation_plan_id,
                    pptx_path=artifact.pptx_path,
                    skill_request_path=artifact.skill_request_path,
                    slide_images=[
                        slide_image.model_dump(mode="json")
                        for slide_image in artifact.slide_images
                    ],
                    created_at=artifact.created_at,
                )
            )

    def get_artifact(self, artifact_id: str) -> PPTArtifact | None:
        with self.database.session() as session:
            record = session.get(PPTArtifactRecord, artifact_id)
            return self._artifact(record) if record else None

    def get_artifact_for_job(self, job_id: str) -> PPTArtifact | None:
        with self.database.session() as session:
            record = session.scalar(
                select(PPTArtifactRecord).where(PPTArtifactRecord.job_id == job_id)
            )
            return self._artifact(record) if record else None

    @staticmethod
    def _plan(record: PresentationPlanRecord) -> PresentationPlan:
        return PresentationPlan(
            id=record.id,
            content_id=record.content_id,
            title=record.title,
            slides=[SlidePlan.model_validate(slide) for slide in record.slides],
            created_at=ensure_utc(record.created_at),
            updated_at=ensure_utc(record.updated_at),
        )

    @staticmethod
    def _job(record: PPTGenerationJobRecord) -> PPTGenerationJob:
        return PPTGenerationJob(
            id=record.id,
            presentation_plan_id=record.presentation_plan_id,
            status=record.status,
            progress=record.progress,
            artifact_id=record.artifact_id,
            error=record.error,
            created_at=ensure_utc(record.created_at),
            updated_at=ensure_utc(record.updated_at),
        )

    @staticmethod
    def _artifact(record: PPTArtifactRecord) -> PPTArtifact:
        return PPTArtifact(
            id=record.id,
            job_id=record.job_id,
            presentation_plan_id=record.presentation_plan_id,
            pptx_path=record.pptx_path,
            skill_request_path=record.skill_request_path,
            slide_images=[
                PPTSlideImage.model_validate(slide_image)
                for slide_image in (record.slide_images or [])
            ],
            created_at=ensure_utc(record.created_at),
        )
