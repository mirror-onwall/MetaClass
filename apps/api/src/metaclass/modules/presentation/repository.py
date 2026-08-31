from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, select

from metaclass.infrastructure.database import Database
from metaclass.modules.presentation.models import (
    PPTArtifactRecord,
    PPTGenerationJobRecord,
    PresentationPlanRecord,
    PresentationResourceRecord,
)
from metaclass.modules.presentation.schemas import (
    PPTArtifact,
    PPTGenerationJob,
    PPTSlideImage,
    PresentationPlan,
    PresentationPlanLibrarySummary,
    PresentationResource,
    PresentationSlideResource,
    SlidePlan,
)


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class PresentationRepository(Protocol):
    def save_plan(self, plan: PresentationPlan) -> None: ...

    def get_plan(self, plan_id: str) -> PresentationPlan | None: ...

    def get_plan_for_content(self, content_id: str) -> PresentationPlan | None: ...

    def list_plan_summaries(self) -> list[PresentationPlanLibrarySummary]: ...

    def save_job(self, job: PPTGenerationJob) -> None: ...

    def get_job(self, job_id: str) -> PPTGenerationJob | None: ...

    def list_jobs(self) -> list[PPTGenerationJob]: ...

    def delete_job(self, job_id: str) -> None: ...

    def save_artifact(self, artifact: PPTArtifact) -> None: ...

    def get_artifact(self, artifact_id: str) -> PPTArtifact | None: ...

    def get_artifact_for_job(self, job_id: str) -> PPTArtifact | None: ...

    def get_artifact_for_plan(self, plan_id: str) -> PPTArtifact | None: ...

    def save_resource(self, resource: PresentationResource) -> None: ...

    def get_resource_for_plan(self, plan_id: str) -> PresentationResource | None: ...


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
                    mode=plan.mode,
                    source_material_id=plan.source_material_id,
                    source_paper_material_id=plan.source_paper_material_id,
                    paper_artifact_bundle_id=plan.paper_artifact_bundle_id,
                    presentation_resource_id=plan.presentation_resource_id,
                    slides=[slide.model_dump(mode="json") for slide in plan.slides],
                    generation_source=plan.generation_source,
                    generation_provider=plan.generation_provider,
                    generation_model=plan.generation_model,
                    fallback_reason=plan.fallback_reason,
                    interaction_intensity=plan.interaction_intensity,
                    interaction_node_ids=plan.interaction_node_ids,
                    interaction_planning_status=plan.interaction_planning_status,
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

    def list_plan_summaries(self) -> list[PresentationPlanLibrarySummary]:
        with self.database.session() as session:
            records = session.scalars(
                select(PresentationPlanRecord).order_by(
                    PresentationPlanRecord.created_at.desc()
                )
            ).all()
            summaries = []
            for record in records:
                artifact = session.scalar(
                    select(PPTArtifactRecord)
                    .where(PPTArtifactRecord.presentation_plan_id == record.id)
                    .order_by(PPTArtifactRecord.created_at.desc())
                )
                summaries.append(
                    PresentationPlanLibrarySummary(
                        id=record.id,
                        content_id=record.content_id,
                        title=record.title,
                        slide_count=len(record.slides or []),
                        artifact_id=artifact.id if artifact else None,
                        created_at=ensure_utc(record.created_at),
                    )
                )
            return summaries

    def save_job(self, job: PPTGenerationJob) -> None:
        with self.database.session() as session:
            session.merge(
                PPTGenerationJobRecord(
                    id=job.id,
                    presentation_plan_id=job.presentation_plan_id,
                    theme_id=job.theme_id,
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

    def list_jobs(self) -> list[PPTGenerationJob]:
        with self.database.session() as session:
            records = session.scalars(
                select(PPTGenerationJobRecord).order_by(PPTGenerationJobRecord.updated_at.desc())
            ).all()
            return [self._job(record) for record in records]

    def delete_job(self, job_id: str) -> None:
        with self.database.session() as session:
            job = session.get(PPTGenerationJobRecord, job_id)
            if not job:
                return
            fallback_artifact = session.scalar(
                select(PPTArtifactRecord)
                .where(
                    PPTArtifactRecord.presentation_plan_id == job.presentation_plan_id,
                    PPTArtifactRecord.job_id != job_id,
                )
                .order_by(PPTArtifactRecord.created_at.desc())
            )
            resource = session.scalar(
                select(PresentationResourceRecord).where(
                    PresentationResourceRecord.presentation_plan_id
                    == job.presentation_plan_id
                )
            )
            if resource:
                resource.artifact_id = (
                    fallback_artifact.id if fallback_artifact else None
                )
            session.execute(delete(PPTArtifactRecord).where(PPTArtifactRecord.job_id == job_id))
            session.execute(delete(PPTGenerationJobRecord).where(PPTGenerationJobRecord.id == job_id))

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

    def get_artifact_for_plan(self, plan_id: str) -> PPTArtifact | None:
        with self.database.session() as session:
            record = session.scalar(
                select(PPTArtifactRecord)
                .where(PPTArtifactRecord.presentation_plan_id == plan_id)
                .order_by(PPTArtifactRecord.created_at.desc())
            )
            return self._artifact(record) if record else None

    def save_resource(self, resource: PresentationResource) -> None:
        with self.database.session() as session:
            session.merge(
                PresentationResourceRecord(
                    id=resource.id,
                    presentation_plan_id=resource.presentation_plan_id,
                    kind=resource.kind,
                    source_material_id=resource.source_material_id,
                    artifact_id=resource.artifact_id,
                    source_file_hash=resource.source_file_hash,
                    source_page_count=resource.source_page_count,
                    slides=[slide.model_dump(mode="json") for slide in resource.slides],
                    created_at=resource.created_at,
                    updated_at=resource.updated_at,
                )
            )

    def get_resource_for_plan(self, plan_id: str) -> PresentationResource | None:
        with self.database.session() as session:
            record = session.scalar(
                select(PresentationResourceRecord).where(
                    PresentationResourceRecord.presentation_plan_id == plan_id
                )
            )
            if not record:
                return None
            return PresentationResource(
                id=record.id,
                presentation_plan_id=record.presentation_plan_id,
                kind=record.kind,
                source_material_id=record.source_material_id,
                artifact_id=record.artifact_id,
                source_file_hash=record.source_file_hash,
                source_page_count=record.source_page_count,
                slides=[
                    PresentationSlideResource.model_validate(slide)
                    for slide in (record.slides or [])
                ],
                created_at=ensure_utc(record.created_at),
                updated_at=ensure_utc(record.updated_at),
            )

    @staticmethod
    def _plan(record: PresentationPlanRecord) -> PresentationPlan:
        mode = "generated" if record.mode == "hybrid" else record.mode or "generated"
        slides = []
        for payload in record.slides:
            normalized = dict(payload)
            if (
                "source_kind" not in normalized
                and mode == "source_deck"
                and normalized.get("source_page_no")
            ):
                normalized["source_kind"] = "source"
            slides.append(SlidePlan.model_validate(normalized))
        return PresentationPlan(
            id=record.id,
            content_id=record.content_id,
            title=record.title,
            mode=mode,
            source_material_id=record.source_material_id,
            source_paper_material_id=record.source_paper_material_id,
            paper_artifact_bundle_id=record.paper_artifact_bundle_id,
            presentation_resource_id=record.presentation_resource_id,
            slides=slides,
            generation_source=record.generation_source or "unknown",
            generation_provider=record.generation_provider,
            generation_model=record.generation_model,
            fallback_reason=record.fallback_reason,
            interaction_intensity=record.interaction_intensity,
            interaction_node_ids=record.interaction_node_ids or [],
            interaction_planning_status=(
                record.interaction_planning_status or "not_started"
            ),
            created_at=ensure_utc(record.created_at),
            updated_at=ensure_utc(record.updated_at),
        )

    @staticmethod
    def _job(record: PPTGenerationJobRecord) -> PPTGenerationJob:
        return PPTGenerationJob(
            id=record.id,
            presentation_plan_id=record.presentation_plan_id,
            theme_id=record.theme_id or "academic_blue",
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
