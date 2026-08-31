from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field

from metaclass.core.schemas import SchemaModel, utc_now
from metaclass.modules.content.service import ContentService
from metaclass.modules.interaction_planning.service import (
    InteractionIntensity,
    InteractionPlanningService,
)
from metaclass.modules.presentation.repository import PresentationRepository


class InteractionPlanningJob(SchemaModel):
    id: str = Field(min_length=1)
    presentation_plan_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    intensity: InteractionIntensity
    status: Literal["queued", "running", "succeeded", "failed", "paused"] = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    step: str = "queued"
    message: str = "等待互动规划"
    selected_node_count: int = Field(default=0, ge=0)
    completed_node_count: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class InteractionPlanningJobService:
    def __init__(
        self,
        data_dir: Path,
        planner: InteractionPlanningService,
        contents: ContentService,
        presentations: PresentationRepository,
    ) -> None:
        self.data_dir = data_dir
        self.planner = planner
        self.contents = contents
        self.presentations = presentations
        self.jobs_dir = data_dir / "runtime" / "interaction_planning" / "jobs"
        self._jobs: dict[str, InteractionPlanningJob] = {}
        self._lock = Lock()
        self._restore_jobs()

    def create_and_submit(
        self, plan_id: str, intensity: InteractionIntensity
    ) -> InteractionPlanningJob:
        plan = self.presentations.get_plan(plan_id)
        if not plan:
            raise HTTPException(404, "PresentationPlan not found")
        existing = self.latest_for_plan(plan_id)
        if existing and existing.status in {"queued", "running"}:
            return existing
        job = InteractionPlanningJob(
            id=f"interaction_job_{uuid4().hex[:12]}",
            presentation_plan_id=plan.id,
            content_id=plan.content_id,
            intensity=intensity,
        )
        self._save(job)
        self.submit(job.id)
        return job

    def submit(self, job_id: str) -> None:
        Thread(target=self.run, args=(job_id,), daemon=True).start()

    def run(self, job_id: str) -> None:
        job = self.get(job_id)
        try:
            job.status = "running"
            job.error = None
            self._update(job, 5, "starting", "正在启动互动规划")
            plan = self.presentations.get_plan(job.presentation_plan_id)
            if not plan:
                raise RuntimeError("PresentationPlan not found")
            content = self.contents.get(job.content_id)

            def report(progress: int, step: str, message: str) -> None:
                current = self.get(job_id)
                self._update(current, progress, step, message)

            result = self.planner.plan(
                content,
                plan,
                job.intensity,
                progress_callback=report,
            )
            job = self.get(job_id)
            job.selected_node_count = len(result.selected_slide_ids)
            prepared = self.planner.repository.list_for_plan(plan.id)
            selected = set(result.selected_slide_ids)
            job.completed_node_count = len(
                {item.slide_id for item in prepared if item.slide_id in selected}
            )
            job.status = "succeeded"
            self._update(job, 100, "completed", "互动规划已完成")
        except Exception as exc:  # noqa: BLE001 - task failures become durable job state
            job = self.get(job_id)
            job.status = "failed"
            job.error = str(exc)
            self._update(job, job.progress, "failed", f"互动规划失败：{exc}")

    def get(self, job_id: str) -> InteractionPlanningJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise HTTPException(404, "InteractionPlanningJob not found")
            return job.model_copy(deep=True)

    def latest_for_plan(self, plan_id: str) -> InteractionPlanningJob | None:
        with self._lock:
            matches = [
                job for job in self._jobs.values() if job.presentation_plan_id == plan_id
            ]
            if not matches:
                return None
            return max(matches, key=lambda item: str(item.created_at)).model_copy(deep=True)

    def retry(self, job_id: str) -> InteractionPlanningJob:
        job = self.get(job_id)
        if job.status not in {"failed", "paused"}:
            raise HTTPException(409, "Only failed or paused jobs can be retried")
        job.status = "queued"
        job.error = None
        self._update(job, job.progress, "queued", "等待恢复互动规划")
        self.submit(job.id)
        return self.get(job.id)

    def _restore_jobs(self) -> None:
        if not self.jobs_dir.exists():
            return
        for path in self.jobs_dir.glob("*.json"):
            try:
                job = InteractionPlanningJob.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if job.status == "running":
                job.status = "paused"
                job.step = "paused"
                job.message = "服务中断，互动节点和已生成问答已保存"
            self._jobs[job.id] = job
            self._write(job)

    def _update(
        self, job: InteractionPlanningJob, progress: int, step: str, message: str
    ) -> None:
        job.progress = progress
        job.step = step
        job.message = message
        job.updated_at = utc_now()
        self._save(job)

    def _save(self, job: InteractionPlanningJob) -> None:
        with self._lock:
            self._jobs[job.id] = job.model_copy(deep=True)
            self._write(job)

    def _write(self, job: InteractionPlanningJob) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        target = self.jobs_dir / f"{job.id}.json"
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
