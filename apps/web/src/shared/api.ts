import type {
  ClassroomPlanJob,
  ClassroomNavigationResult,
  ClassroomSession,
  ClassroomPlanLibrarySummary,
  AutoClassroomStep,
  ContentGenerationJob,
  ControllerResult,
  LearningContent,
  LearningContentDiagnostics,
  LearningMode,
  InteractionIntensity,
  InteractionPlanningJob,
  Material,
  MaterialCollection,
  MaterialLearningContentSummary,
  MaterialProcessingJob,
  PageMetadata,
  ProcessedMaterial,
  ProcessedMaterials,
  PPTArtifact,
  PPTGenerationJob,
  PPTThemeOption,
  PresentationPlanJob,
  PresentationPlan,
  PresentationPlanLibrarySummary,
  PresentationResource,
  StudentAgentType,
  TTSArtifact,
  TTSArtifactRequest,
  QuestionBank,
  PaperDeckCourseResult,
  PaperOutline,
  PaperSlideEvidence,
  PaperWorkflowJob,
  VideoJob,
  VideoResult,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "";

type RequestOptions = RequestInit & { timeoutMs?: number };

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { timeoutMs, ...requestInit } = init ?? {};
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...requestInit,
      signal: requestInit.signal ?? AbortSignal.timeout(timeoutMs ?? 600_000),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new Error("请求超时，请检查后端服务或缩小材料后重试");
    }
    throw new Error("无法连接 AxiomEarth API，请确认前后端服务已经启动");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

function classroomCommandHeaders(expectedVersion: number) {
  return {
    "X-Request-ID": globalThis.crypto?.randomUUID?.()
      ?? `classroom-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    "X-Expected-Session-Version": String(expectedVersion),
  };
}

async function poll<T>(
  load: () => Promise<T>,
  isDone: (value: T) => boolean,
  isFailed: (value: T) => string | undefined,
): Promise<T> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 600_000) {
    const value = await load();
    const error = isFailed(value);
    if (error) throw new Error(error);
    if (isDone(value)) return value;
    await sleep(1_000);
  }
  throw new Error("任务处理超时，请稍后刷新查看结果");
}

export const api = {
  createPaperWorkflow(materialId: string, settings: {
    duration_minutes: number;
    audience: string;
    interaction_intensity: InteractionIntensity;
  }) {
    return request<PaperWorkflowJob>("/api/v1/paper-workflows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        material_id: materialId,
        strategy: "native_paper_deck",
        language: "zh-CN",
        depth: "standard",
        ...settings,
      }),
    });
  },
  getPaperWorkflow(jobId: string) {
    return request<PaperWorkflowJob>(`/api/v1/paper-workflows/${jobId}`);
  },
  getLatestPaperWorkflow(materialId: string) {
    return request<PaperWorkflowJob>(
      `/api/v1/paper-workflows/latest?material_id=${encodeURIComponent(materialId)}`,
    );
  },
  listPaperWorkflows() {
    return request<PaperWorkflowJob[]>("/api/v1/paper-workflows");
  },
  runPaperWorkflow(jobId: string) {
    return request<PaperWorkflowJob>(`/api/v1/paper-workflows/${jobId}/run`, {
      method: "POST",
      timeoutMs: 3_600_000,
    });
  },
  submitPaperWorkflow(jobId: string) {
    return request<PaperWorkflowJob>(`/api/v1/paper-workflows/${jobId}/submit`, {
      method: "POST",
    });
  },
  async waitForPaperWorkflow(
    jobId: string,
    onUpdate?: (job: PaperWorkflowJob) => void,
  ) {
    // paper-deck itself may use the full 60-minute allowance; polling requests
    // remain short and the durable backend Job is not canceled by this UI guard.
    const startedAt = Date.now();
    while (Date.now() - startedAt < 7_200_000) {
      const job = await api.getPaperWorkflow(jobId);
      onUpdate?.(job);
      if (job.status === "succeeded") return job;
      if (job.status === "paused") throw new Error("任务已暂停，进度已经保存");
      if (job.status === "failed" || job.status === "canceled") {
        throw new Error(job.error ?? `论文讲解任务${job.status}`);
      }
      await sleep(1_500);
    }
    throw new Error("论文讲解仍在后台运行，请稍后刷新查看进度");
  },
  pausePaperWorkflow(jobId: string) {
    return request<PaperWorkflowJob>(`/api/v1/paper-workflows/${jobId}/pause`, { method: "POST" });
  },
  resumePaperWorkflow(jobId: string) {
    return request<PaperWorkflowJob>(`/api/v1/paper-workflows/${jobId}/resume`, { method: "POST" });
  },
  getPaperOutline(jobId: string) {
    return request<PaperOutline>(`/api/v1/paper-workflows/${jobId}/outline`);
  },
  getPaperSlideEvidence(jobId: string) {
    return request<PaperSlideEvidence>(`/api/v1/paper-workflows/${jobId}/slide-evidence`);
  },
  createPaperDeckCourse(jobId: string) {
    return request<PaperDeckCourseResult>(`/api/v1/paper-workflows/${jobId}/create-paper-deck-course`, {
      method: "POST",
      timeoutMs: 1_800_000,
    });
  },
  async upload(file: File) {
    const result = await api.uploadMany([file]);
    if (!result.items[0]) throw new Error("材料解析任务没有返回结果");
    return result.items[0];
  },
  uploadSync(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<ProcessedMaterial>("/api/v1/materials/process", {
      method: "POST",
      body: form,
    });
  },
  async uploadMany(
    files: File[],
    onProgress?: (job: MaterialProcessingJob) => void,
  ) {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    const job = await request<MaterialProcessingJob>("/api/v1/materials/processing-jobs", {
      method: "POST",
      body: form,
      timeoutMs: 600_000,
    });
    onProgress?.(job);
    return api.waitForMaterialProcessingJob(job.id, onProgress);
  },
  getMaterialProcessingJob(jobId: string) {
    return request<MaterialProcessingJob>(`/api/v1/materials/processing-jobs/${jobId}`);
  },
  listMaterialProcessingJobs() {
    return request<MaterialProcessingJob[]>("/api/v1/materials/processing-jobs");
  },
  getMaterialProcessingJobResult(jobId: string) {
    return request<ProcessedMaterials>(`/api/v1/materials/processing-jobs/${jobId}/result`);
  },
  pauseMaterialProcessingJob(jobId: string) {
    return request<MaterialProcessingJob>(
      `/api/v1/materials/processing-jobs/${jobId}/pause`,
      { method: "POST" },
    );
  },
  resumeMaterialProcessingJob(jobId: string) {
    return request<MaterialProcessingJob>(`/api/v1/materials/processing-jobs/${jobId}/resume`, { method: "POST" });
  },
  discardMaterialProcessingJob(jobId: string) {
    return request<void>(`/api/v1/materials/processing-jobs/${jobId}`, { method: "DELETE" });
  },
  async waitForMaterialProcessingJob(
    jobId: string,
    onProgress?: (job: MaterialProcessingJob) => void,
  ) {
    for (;;) {
      const job = await api.getMaterialProcessingJob(jobId);
      onProgress?.(job);
      if (job.status === "succeeded") return api.getMaterialProcessingJobResult(jobId);
      if (job.status === "failed") {
        throw new Error(job.error ?? "材料解析任务失败");
      }
      if (job.status === "canceled") throw new Error("材料处理已取消");
      if (job.status === "paused") throw new Error("任务已暂停，进度已经保存");
      await wait(1500);
    }
  },
  listMaterials() {
    return request<Material[]>("/api/v1/materials");
  },
  deleteMaterial(materialId: string) {
    return request<{ material_id: string; deleted: boolean }>(
      `/api/v1/materials/${materialId}`,
      { method: "DELETE" },
    );
  },
  listMaterialCollections() {
    return request<MaterialCollection[]>("/api/v1/materials/collections");
  },
  listMaterialLearningContentSummaries() {
    return request<MaterialLearningContentSummary[]>(
      "/api/v1/material-learning-content-summaries",
    );
  },
  getMaterialPages(materialId: string) {
    return request<PageMetadata[]>(`/api/v1/materials/${materialId}/pages`);
  },
  createMaterialCollection(title: string, materialIds: string[], primaryMaterialId?: string) {
    return request<MaterialCollection>("/api/v1/materials/collections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        material_ids: materialIds,
        primary_material_id: primaryMaterialId,
      }),
    });
  },
  parse(materialId: string) {
    return request<PageMetadata[]>(`/api/v1/materials/${materialId}/parse`, { method: "POST" });
  },
  buildContentSync(materialId: string) {
    return request<LearningContent>(`/api/v1/materials/${materialId}/learning-content`, {
      method: "POST",
    });
  },
  async buildContent(
    materialId: string,
    onProgress?: (job: ContentGenerationJob) => void,
  ) {
    const job = await request<ContentGenerationJob>(
      `/api/v1/materials/${materialId}/learning-content-jobs`,
      { method: "POST" },
    );
    onProgress?.(job);
    return api.waitForContentGenerationJob(job.id, onProgress);
  },
  async buildSourceDeckContent(
    materialId: string,
    onProgress?: (job: ContentGenerationJob) => void,
  ) {
    const job = await request<ContentGenerationJob>(
      `/api/v1/materials/${materialId}/source-deck-learning-content-jobs`,
      { method: "POST" },
    );
    onProgress?.(job);
    return api.waitForContentGenerationJob(job.id, onProgress);
  },
  async buildCollectionContent(
    collectionId: string,
    onProgress?: (job: ContentGenerationJob) => void,
  ) {
    const job = await request<ContentGenerationJob>(
      `/api/v1/material-collections/${collectionId}/learning-content-jobs`,
      { method: "POST" },
    );
    onProgress?.(job);
    return api.waitForContentGenerationJob(job.id, onProgress);
  },
  getContentGenerationJob(jobId: string) {
    return request<ContentGenerationJob>(`/api/v1/learning-content-jobs/${jobId}`);
  },
  listContentGenerationJobs() {
    return request<ContentGenerationJob[]>("/api/v1/learning-content-jobs");
  },
  getContentGenerationJobResult(jobId: string) {
    return request<LearningContent>(`/api/v1/learning-content-jobs/${jobId}/result`);
  },
  pauseContentGenerationJob(jobId: string) {
    return request<ContentGenerationJob>(`/api/v1/learning-content-jobs/${jobId}/pause`, { method: "POST" });
  },
  resumeContentGenerationJob(jobId: string) {
    return request<ContentGenerationJob>(`/api/v1/learning-content-jobs/${jobId}/resume`, { method: "POST" });
  },
  discardContentGenerationJob(jobId: string) {
    return request<void>(`/api/v1/learning-content-jobs/${jobId}`, { method: "DELETE" });
  },
  getContentDiagnostics(contentId: string) {
    return request<LearningContentDiagnostics>(
      `/api/v1/learning-contents/${contentId}/diagnostics`,
    );
  },
  getLearningContent(contentId: string) {
    return request<LearningContent>(`/api/v1/learning-contents/${contentId}`);
  },
  async waitForContentGenerationJob(
    jobId: string,
    onProgress?: (job: ContentGenerationJob) => void,
  ) {
    for (;;) {
      const job = await api.getContentGenerationJob(jobId);
      onProgress?.(job);
      if (job.status === "succeeded") return api.getContentGenerationJobResult(jobId);
      if (job.status === "failed") {
        throw new Error(job.error ?? "学习内容生成任务失败");
      }
      if (job.status === "paused") throw new Error("任务已暂停，进度已经保存");
      await wait(1500);
    }
  },
  async createSession(
    contentId: string,
    presentationPlanId: string,
    mode: LearningMode,
    studentAgentTypes?: StudentAgentType[],
    onProgress?: (job: ClassroomPlanJob) => void,
  ) {
    const created = await api.createClassroomPlanJob(
      contentId, presentationPlanId, onProgress,
    );
    return api.resumeClassroomPlanJob(
      created.id,
      mode,
      studentAgentTypes,
      onProgress,
    );
  },
  async createClassroomPlanJob(
    contentId: string,
    presentationPlanId: string,
    onProgress?: (job: ClassroomPlanJob) => void,
  ) {
    const created = await request<ClassroomPlanJob>(
      `/api/v1/learning-contents/${contentId}/classroom-plan-jobs?presentation_plan_id=${encodeURIComponent(presentationPlanId)}`,
      { method: "POST" },
    );
    onProgress?.(created);
    return created;
  },
  getClassroomPlanJob(jobId: string) {
    return request<ClassroomPlanJob>(`/api/v1/classroom-plan-jobs/${jobId}`);
  },
  getLatestClassroomPlanJob(contentId: string, presentationPlanId: string) {
    const query = new URLSearchParams({ presentation_plan_id: presentationPlanId });
    return request<ClassroomPlanJob>(
      `/api/v1/learning-contents/${contentId}/latest-classroom-plan-job?${query}`,
    );
  },
  async resumeClassroomPlanJob(
    jobId: string,
    mode: LearningMode,
    studentAgentTypes?: StudentAgentType[],
    onProgress?: (job: ClassroomPlanJob) => void,
  ) {
    const job = await api.waitForClassroomPlanJob(jobId, onProgress);
    return request<ClassroomSession>(`/api/v1/classroom-plans/${job.plan_id}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, student_agent_types: studentAgentTypes }),
    });
  },
  async waitForClassroomPlanJob(
    jobId: string,
    onProgress?: (job: ClassroomPlanJob) => void,
  ) {
    let job: ClassroomPlanJob;
    for (;;) {
      job = await api.getClassroomPlanJob(jobId);
      onProgress?.(job);
      if (job.status === "failed") {
        throw new Error(job.error ?? job.message ?? "课堂计划生成失败");
      }
      if (job.status === "succeeded" && job.plan_id) break;
      await wait(1500);
    }
    return job as ClassroomPlanJob & { plan_id: string };
  },
  generateQuestionBank(planId: string) {
    return request(`/api/v1/presentation-plans/${planId}/question-bank`, { method: "POST" });
  },
  regenerateQuestionBank(planId: string) {
    return request<QuestionBank>(`/api/v1/presentation-plans/${planId}/question-bank/regenerate`, {
      method: "POST",
      timeoutMs: 1_800_000,
    });
  },
  listQuestionBankVersions(planId: string) {
    return request<QuestionBank[]>(`/api/v1/presentation-plans/${planId}/question-bank/versions`);
  },
  archiveQuestionBankVersion(planId: string, generationId: string) {
    return request<{ archived: boolean }>(
      `/api/v1/presentation-plans/${planId}/question-bank/versions/${encodeURIComponent(generationId)}`,
      { method: "DELETE" },
    );
  },
  getQuestionBank(planId: string) {
    return request<QuestionBank>(`/api/v1/presentation-plans/${planId}/question-bank`);
  },
  createLectureVariant(planId: string) {
    return request<{ id: string }>(`/api/v1/classroom-plans/${planId}/lecture-variant`, {
      method: "POST",
    });
  },
  createSessionForPlan(
    planId: string,
    mode: LearningMode,
    studentAgentTypes?: StudentAgentType[],
  ) {
    return request<ClassroomSession>(`/api/v1/classroom-plans/${planId}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, student_agent_types: studentAgentTypes }),
    });
  },
  replaySession(sessionId: string) {
    return request<ClassroomSession>(`/api/v1/classroom-sessions/${sessionId}/replay`, {
      method: "POST",
    });
  },
  getClassroomSession(sessionId: string) {
    return request<ClassroomSession>(`/api/v1/classroom-sessions/${sessionId}`);
  },
  switchClassroomMode(
    sessionId: string,
    mode: LearningMode,
    expectedVersion: number,
    studentAgentTypes?: StudentAgentType[],
  ) {
    return request<ClassroomSession>(`/api/v1/classroom-sessions/${sessionId}/mode`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...classroomCommandHeaders(expectedVersion),
      },
      body: JSON.stringify({ mode, student_agent_types: studentAgentTypes }),
    });
  },
  async createPresentationDeck(
    contentId: string,
    prepareQuestionBank: boolean,
    themeId: string,
    onPlanProgress?: (job: PresentationPlanJob) => void,
    onPptProgress?: (job: PPTGenerationJob) => void,
  ) {
    const createdPlanJob = await request<PresentationPlanJob>(
      `/api/v1/learning-contents/${contentId}/presentation-plan-jobs?prepare_question_bank=${prepareQuestionBank}`,
      { method: "POST" },
    );
    onPlanProgress?.(createdPlanJob);
    const planJob = await api.waitForPresentationPlanJob(createdPlanJob.id, onPlanProgress);
    const plan = await request<PresentationPlan>(
      `/api/v1/presentation-plan-jobs/${planJob.id}/result`,
    );
    const job = await request<PPTGenerationJob>(
      `/api/v1/presentation-plans/${plan.id}/ppt-jobs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme_id: themeId }),
      },
    );
    onPptProgress?.(job);
    const finished = await api.waitForPptJob(job.id, onPptProgress);
    const artifact = await request<PPTArtifact>(`/api/v1/ppt-jobs/${finished.id}/artifact`);
    return { plan, artifact };
  },
  async createPresentationPlan(
    contentId: string,
    prepareQuestionBank = true,
    onProgress?: (job: PresentationPlanJob) => void,
    interactionIntensity: InteractionIntensity = "standard",
  ) {
    const query = new URLSearchParams({
      prepare_question_bank: String(prepareQuestionBank),
      interaction_intensity: interactionIntensity,
    });
    const created = await request<PresentationPlanJob>(
      `/api/v1/learning-contents/${contentId}/presentation-plan-jobs?${query}`,
      { method: "POST" },
    );
    onProgress?.(created);
    const finished = await api.waitForPresentationPlanJob(created.id, onProgress);
    return request<PresentationPlan>(`/api/v1/presentation-plan-jobs/${finished.id}/result`);
  },
  async createSourceDeckPresentationPlan(
    contentId: string,
    sourceMaterialId: string,
    prepareQuestionBank = true,
    onProgress?: (job: PresentationPlanJob) => void,
    interactionIntensity: InteractionIntensity = "standard",
  ) {
    const query = new URLSearchParams({
      source_material_id: sourceMaterialId,
      prepare_question_bank: String(prepareQuestionBank),
      interaction_intensity: interactionIntensity,
    });
    const created = await request<PresentationPlanJob>(
      `/api/v1/learning-contents/${contentId}/source-deck-presentation-plan-jobs?${query}`,
      { method: "POST" },
    );
    onProgress?.(created);
    const finished = await api.waitForPresentationPlanJob(created.id, onProgress);
    return request<PresentationPlan>(`/api/v1/presentation-plan-jobs/${finished.id}/result`);
  },
  updateSlideSpeakerScript(planId: string, slideId: string, speakerScript: string) {
    return request<PresentationPlan>(
      `/api/v1/presentation-plans/${planId}/slides/${slideId}/speaker-script`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speaker_script: speakerScript }),
      },
    );
  },
  getPresentationResource(planId: string) {
    return request<PresentationResource>(`/api/v1/presentation-plans/${planId}/resource`);
  },
  presentationResourceImage(imageUrl: string) {
    if (/^https?:\/\//i.test(imageUrl)) return imageUrl;
    return `${API_BASE}${imageUrl}`;
  },
  async generatePptForPlan(
    planId: string,
    themeId: string,
    onProgress?: (job: PPTGenerationJob) => void,
  ) {
    const created = await request<PPTGenerationJob>(
      `/api/v1/presentation-plans/${planId}/ppt-jobs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme_id: themeId }),
      },
    );
    onProgress?.(created);
    const finished = await api.waitForPptJob(created.id, onProgress);
    return request<PPTArtifact>(`/api/v1/ppt-jobs/${finished.id}/artifact`);
  },
  listPresentationPlanLibrary() {
    return request<PresentationPlanLibrarySummary[]>("/api/v1/presentation-plan-library");
  },
  getPresentationPlan(planId: string) {
    return request<PresentationPlan>(`/api/v1/presentation-plans/${planId}`);
  },
  getPptArtifact(artifactId: string) {
    return request<PPTArtifact>(`/api/v1/ppt-artifacts/${artifactId}`);
  },
  listClassroomPlanLibrary() {
    return request<ClassroomPlanLibrarySummary[]>("/api/v1/classroom-plan-library");
  },
  getPptThemes() {
    return request<PPTThemeOption[]>("/api/v1/ppt-themes");
  },
  getPptJob(jobId: string) {
    return request<PPTGenerationJob>(`/api/v1/ppt-jobs/${jobId}`);
  },
  listPptJobs() {
    return request<PPTGenerationJob[]>("/api/v1/ppt-jobs");
  },
  pausePptJob(jobId: string) {
    return request<PPTGenerationJob>(`/api/v1/ppt-jobs/${jobId}/pause`, { method: "POST" });
  },
  resumePptJob(jobId: string) {
    return request<PPTGenerationJob>(`/api/v1/ppt-jobs/${jobId}/resume`, { method: "POST" });
  },
  discardPptJob(jobId: string) {
    return request<void>(`/api/v1/ppt-jobs/${jobId}`, { method: "DELETE" });
  },
  async waitForPptJob(
    jobId: string,
    onProgress?: (job: PPTGenerationJob) => void,
  ) {
    for (;;) {
      const job = await api.getPptJob(jobId);
      onProgress?.(job);
      if (job.status === "finished" && job.artifact_id) return job;
      if (job.status === "failed") {
        throw new Error(job.error ?? "PPT 生成失败");
      }
      if (job.status === "paused") throw new Error("任务已暂停，进度已经保存");
      await wait(1500);
    }
  },
  getPresentationPlanJob(jobId: string) {
    return request<PresentationPlanJob>(`/api/v1/presentation-plan-jobs/${jobId}`);
  },
  listPresentationPlanJobs() {
    return request<PresentationPlanJob[]>("/api/v1/presentation-plan-jobs");
  },
  getPresentationPlanJobResult(jobId: string) {
    return request<PresentationPlan>(`/api/v1/presentation-plan-jobs/${jobId}/result`);
  },
  pausePresentationPlanJob(jobId: string) {
    return request<PresentationPlanJob>(`/api/v1/presentation-plan-jobs/${jobId}/pause`, { method: "POST" });
  },
  resumePresentationPlanJob(jobId: string) {
    return request<PresentationPlanJob>(`/api/v1/presentation-plan-jobs/${jobId}/resume`, { method: "POST" });
  },
  discardPresentationPlanJob(jobId: string) {
    return request<void>(`/api/v1/presentation-plan-jobs/${jobId}`, { method: "DELETE" });
  },
  async waitForPresentationPlanJob(
    jobId: string,
    onProgress?: (job: PresentationPlanJob) => void,
  ) {
    for (;;) {
      const job = await api.getPresentationPlanJob(jobId);
      onProgress?.(job);
      if (job.status === "succeeded" && job.plan_id) return job;
      if (job.status === "failed") {
        throw new Error(job.error ?? "PPT 规划生成任务失败");
      }
      if (job.status === "paused") throw new Error("任务已暂停，进度已经保存");
      await wait(1500);
    }
  },
  getInteractionPlanningJobForPlan(planId: string) {
    return request<InteractionPlanningJob>(`/api/v1/presentation-plans/${planId}/interaction-planning-job`);
  },
  getInteractionPlanningJob(jobId: string) {
    return request<InteractionPlanningJob>(`/api/v1/interaction-planning-jobs/${jobId}`);
  },
  retryInteractionPlanningJob(jobId: string) {
    return request<InteractionPlanningJob>(`/api/v1/interaction-planning-jobs/${jobId}/retry`, { method: "POST" });
  },
  async waitForInteractionPlanningJob(jobId: string, onProgress?: (job: InteractionPlanningJob) => void) {
    for (;;) {
      const job = await api.getInteractionPlanningJob(jobId);
      onProgress?.(job);
      if (["succeeded", "failed", "paused"].includes(job.status)) return job;
      await wait(1000);
    }
  },
  next(sessionId: string, expectedVersion: number) {
    return request<ControllerResult>(`/api/v1/classroom-sessions/${sessionId}/next`, {
      method: "POST",
      headers: classroomCommandHeaders(expectedVersion),
    });
  },
  navigate(sessionId: string, direction: "previous" | "next") {
    return request<ClassroomNavigationResult>(
      `/api/v1/classroom-sessions/${sessionId}/navigation/${direction}`,
      { method: "POST" },
    );
  },
  autoStep(sessionId: string, expectedVersion: number) {
    return request<AutoClassroomStep>(`/api/v1/classroom-sessions/${sessionId}/auto-step`, {
      method: "POST",
      headers: classroomCommandHeaders(expectedVersion),
    });
  },
  answer(sessionId: string, selectedIndex: number, expectedVersion: number) {
    return request<ControllerResult>(`/api/v1/classroom-sessions/${sessionId}/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...classroomCommandHeaders(expectedVersion) },
      body: JSON.stringify({ selected_index: selectedIndex }),
    });
  },
  ask(sessionId: string, question: string, expectedVersion: number) {
    return request<ControllerResult>(`/api/v1/classroom-sessions/${sessionId}/questions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...classroomCommandHeaders(expectedVersion) },
      body: JSON.stringify({ question }),
    });
  },
  createTTSArtifact(payload: TTSArtifactRequest, timeoutMs = 5_000) {
    return request<TTSArtifact>("/api/v1/tts-artifacts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeoutMs,
    });
  },
  ttsAudio(audioUrl: string) {
    if (/^https?:\/\//i.test(audioUrl)) return audioUrl;
    return `${API_BASE}${audioUrl}`;
  },
  async createVideo(
    contentId: string,
    presentationArtifactId?: string,
    presentationPlanId?: string,
  ) {
    const query = new URLSearchParams();
    if (presentationArtifactId) query.set("presentation_artifact_id", presentationArtifactId);
    if (presentationPlanId) query.set("presentation_plan_id", presentationPlanId);
    const job = await request<VideoJob>(
      `/api/v1/learning-contents/${contentId}/videos?${query}`,
      { method: "POST" },
    );
    const finished = await poll(
      () => request<VideoJob>(`/api/v1/video-jobs/${job.id}`),
      (value) => value.status === "finished" && Boolean(value.result_id),
      (value) => (value.status === "failed" ? value.error || "视频任务失败" : undefined),
    );
    return request<VideoResult>(`/api/v1/video-jobs/${finished.id}/result`);
  },
  pageImage(materialId: string, pageNumber: number) {
    return `${API_BASE}/api/v1/materials/${materialId}/pages/${pageNumber}/image`;
  },
  materialDownload(materialId: string) {
    return `${API_BASE}/api/v1/materials/${materialId}/download`;
  },
  pptSlideImage(artifactId: string, slideNumber: number) {
    return `${API_BASE}/api/v1/ppt-artifacts/${artifactId}/slides/${slideNumber}/image`;
  },
  pptDownload(artifactId: string) {
    return `${API_BASE}/api/v1/ppt-artifacts/${artifactId}/download`;
  },
  videoDownload(resultId: string) {
    return `${API_BASE}/api/v1/videos/${resultId}/download`;
  },
};
