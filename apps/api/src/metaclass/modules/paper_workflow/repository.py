from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import inspect, select

from metaclass.infrastructure.database import Database
from metaclass.modules.paper_workflow.models import (
    PaperArtifactBundleRecord,
    PaperWorkflowJobRecord,
)
from metaclass.modules.paper_workflow.schemas import (
    PaperArtifactBundle,
    PaperWorkflowCheckpoint,
    PaperWorkflowJob,
    PaperWorkflowRequest,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class PaperWorkflowRepository(Protocol):
    def save_job(
        self,
        job: PaperWorkflowJob,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint | None = None,
    ) -> None: ...

    def get_job(self, job_id: str) -> PaperWorkflowJob | None: ...

    def list_jobs(self) -> list[PaperWorkflowJob]: ...

    def get_request(self, job_id: str) -> PaperWorkflowRequest | None: ...

    def get_checkpoint(self, job_id: str) -> PaperWorkflowCheckpoint | None: ...

    def save_bundle(self, bundle: PaperArtifactBundle) -> None: ...

    def get_bundle(self, bundle_id: str) -> PaperArtifactBundle | None: ...

    def get_bundle_for_job(self, job_id: str) -> PaperArtifactBundle | None: ...


class SqlAlchemyPaperWorkflowRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_job(
        self,
        job: PaperWorkflowJob,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint | None = None,
    ) -> None:
        with self.database.session() as session:
            current = session.get(PaperWorkflowJobRecord, job.id)
            checkpoint_payload = (
                checkpoint.model_dump(mode="json")
                if checkpoint is not None
                else current.checkpoint_payload
                if current
                else None
            )
            session.merge(
                PaperWorkflowJobRecord(
                    id=job.id,
                    source_material_id=job.source_material_id,
                    request_payload=request.model_dump(mode="json"),
                    strategy_requested=job.strategy_requested.value,
                    strategy_selected=(
                        job.strategy_selected.value if job.strategy_selected else None
                    ),
                    status=job.status.value,
                    stage=job.stage.value,
                    progress=job.progress,
                    provider_attempts=job.provider_attempts,
                    checkpoint_version=job.checkpoint_version,
                    checkpoint_payload=checkpoint_payload,
                    artifact_bundle_id=job.artifact_bundle_id,
                    derived_material_id=job.derived_material_id,
                    fallback_reason=job.fallback_reason,
                    required_input=(
                        job.required_input.model_dump(mode="json") if job.required_input else None
                    ),
                    error=job.error,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
            )

    def get_job(self, job_id: str) -> PaperWorkflowJob | None:
        with self.database.session() as session:
            record = session.get(PaperWorkflowJobRecord, job_id)
            return self._job(record) if record else None

    def list_jobs(self) -> list[PaperWorkflowJob]:
        if not inspect(self.database.engine).has_table(PaperWorkflowJobRecord.__tablename__):
            return []
        with self.database.session() as session:
            records = session.scalars(
                select(PaperWorkflowJobRecord).order_by(PaperWorkflowJobRecord.created_at.desc())
            ).all()
            return [self._job(record) for record in records]

    def get_request(self, job_id: str) -> PaperWorkflowRequest | None:
        with self.database.session() as session:
            record = session.get(PaperWorkflowJobRecord, job_id)
            return PaperWorkflowRequest.model_validate(record.request_payload) if record else None

    def get_checkpoint(self, job_id: str) -> PaperWorkflowCheckpoint | None:
        with self.database.session() as session:
            record = session.get(PaperWorkflowJobRecord, job_id)
            if not record or not record.checkpoint_payload:
                return None
            return PaperWorkflowCheckpoint.model_validate(record.checkpoint_payload)

    def save_bundle(self, bundle: PaperArtifactBundle) -> None:
        with self.database.session() as session:
            session.merge(
                PaperArtifactBundleRecord(
                    id=bundle.id,
                    job_id=bundle.job_id,
                    source_material_id=bundle.source_material_id,
                    provider=bundle.provider,
                    root_path=bundle.root_path,
                    files=[item.model_dump(mode="json") for item in bundle.files],
                    validation_status=bundle.validation_status,
                    derived_material_id=bundle.derived_material_id,
                    created_at=bundle.created_at,
                )
            )

    def get_bundle(self, bundle_id: str) -> PaperArtifactBundle | None:
        with self.database.session() as session:
            record = session.get(PaperArtifactBundleRecord, bundle_id)
            return self._bundle(record) if record else None

    def get_bundle_for_job(self, job_id: str) -> PaperArtifactBundle | None:
        with self.database.session() as session:
            record = session.scalar(
                select(PaperArtifactBundleRecord).where(PaperArtifactBundleRecord.job_id == job_id)
            )
            return self._bundle(record) if record else None

    @staticmethod
    def _job(record: PaperWorkflowJobRecord) -> PaperWorkflowJob:
        return PaperWorkflowJob.model_validate(
            {
                "id": record.id,
                "source_material_id": record.source_material_id,
                "strategy_requested": record.strategy_requested,
                "strategy_selected": record.strategy_selected,
                "status": record.status,
                "stage": record.stage,
                "progress": record.progress,
                "provider_attempts": record.provider_attempts or [],
                "checkpoint_version": record.checkpoint_version,
                "artifact_bundle_id": record.artifact_bundle_id,
                "derived_material_id": record.derived_material_id,
                "fallback_reason": record.fallback_reason,
                "required_input": record.required_input,
                "error": record.error,
                "created_at": _utc(record.created_at),
                "updated_at": _utc(record.updated_at),
            }
        )

    @staticmethod
    def _bundle(record: PaperArtifactBundleRecord) -> PaperArtifactBundle:
        return PaperArtifactBundle.model_validate(
            {
                "id": record.id,
                "job_id": record.job_id,
                "source_material_id": record.source_material_id,
                "provider": record.provider,
                "root_path": record.root_path,
                "files": record.files,
                "validation_status": record.validation_status,
                "derived_material_id": record.derived_material_id,
                "created_at": _utc(record.created_at),
            }
        )
