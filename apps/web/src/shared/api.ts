import type {
  ClassroomPlanJob,
  ClassroomSession,
  AutoClassroomStep,
  ContentGenerationJob,
  ControllerResult,
  DirectedAgentTurn,
  LearningContent,
  LearningMode,
  Material,
  MaterialCollection,
  MaterialProcessingJob,
  PageMetadata,
  ProcessedMaterial,
  ProcessedMaterials,
  PPTArtifact,
  PresentationPlan,
  StudentAgentType,
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
      signal: requestInit.signal ?? AbortSignal.timeout(timeoutMs ?? 180_000),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new Error("请求超时，请检查后端服务或缩小材料后重试");
    }
    throw new Error("无法连接 MetaClass API，请确认前后端服务已经启动");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function poll<T>(
  load: () => Promise<T>,
  isDone: (value: T) => boolean,
  isFailed: (value: T) => string | undefined,
): Promise<T> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 180_000) {
    const value = await load();
    const error = isFailed(value);
    if (error) throw new Error(error);
    if (isDone(value)) return value;
    await sleep(1_000);
  }
  throw new Error("任务处理超时，请稍后刷新查看结果");
}

export const api = {
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
  async uploadMany(files: File[]) {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    const job = await request<MaterialProcessingJob>("/api/v1/materials/processing-jobs", {
      method: "POST",
      body: form,
      timeoutMs: 600_000,
    });
    return api.waitForMaterialProcessingJob(job.id);
  },
  getMaterialProcessingJob(jobId: string) {
    return request<MaterialProcessingJob>(`/api/v1/materials/processing-jobs/${jobId}`);
  },
  getMaterialProcessingJobResult(jobId: string) {
    return request<ProcessedMaterials>(`/api/v1/materials/processing-jobs/${jobId}/result`);
  },
  async waitForMaterialProcessingJob(jobId: string) {
    for (;;) {
      const job = await api.getMaterialProcessingJob(jobId);
      if (job.status === "succeeded") return api.getMaterialProcessingJobResult(jobId);
      if (job.status === "failed") {
        throw new Error(job.error ?? "材料解析任务失败");
      }
      await wait(1500);
    }
  },
  listMaterials() {
    return request<Material[]>("/api/v1/materials");
  },
  listMaterialCollections() {
    return request<MaterialCollection[]>("/api/v1/materials/collections");
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
  getContentGenerationJobResult(jobId: string) {
    return request<LearningContent>(`/api/v1/learning-content-jobs/${jobId}/result`);
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
      await wait(1500);
    }
  },
  async createSession(
    contentId: string,
    mode: LearningMode,
    studentAgentTypes?: StudentAgentType[],
  ) {
    const created = await request<ClassroomPlanJob>(
      `/api/v1/learning-contents/${contentId}/classroom-plan-jobs`,
      { method: "POST" },
    );
    const job = await poll(
      () => request<ClassroomPlanJob>(`/api/v1/classroom-plan-jobs/${created.id}`),
      (value) => value.status === "succeeded" && Boolean(value.plan_id),
      (value) => (value.status === "failed" ? value.error || value.message : undefined),
    );
    return request<ClassroomSession>(`/api/v1/classroom-plans/${job.plan_id}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, student_agent_types: studentAgentTypes }),
    });
  },
  async createPresentationDeck(contentId: string) {
    const plan = await request<PresentationPlan>(
      `/api/v1/learning-contents/${contentId}/presentation-plans`,
      { method: "POST" },
    );
    const job = await request<{ id: string; status: string; artifact_id?: string; error?: string }>(
      `/api/v1/presentation-plans/${plan.id}/ppt-jobs`,
      { method: "POST" },
    );
    const finished = await poll(
      () =>
        request<{ id: string; status: string; artifact_id?: string; error?: string }>(
          `/api/v1/ppt-jobs/${job.id}`,
        ),
      (value) => value.status === "finished" && Boolean(value.artifact_id),
      (value) => (value.status === "failed" ? value.error || "PPT 生成失败" : undefined),
    );
    return request<PPTArtifact>(`/api/v1/ppt-jobs/${finished.id}/artifact`);
  },
  nextAgentTurn(sessionId: string) {
    return request<DirectedAgentTurn>(`/api/v1/classroom-sessions/${sessionId}/agent-turns/next`, {
      method: "POST",
    });
  },
  next(sessionId: string) {
    return request<ControllerResult>(`/api/v1/classroom-sessions/${sessionId}/next`, {
      method: "POST",
    });
  },
  autoStep(sessionId: string) {
    return request<AutoClassroomStep>(`/api/v1/classroom-sessions/${sessionId}/auto-step`, {
      method: "POST",
    });
  },
  answer(sessionId: string, selectedIndex: number) {
    return request<ControllerResult>(`/api/v1/classroom-sessions/${sessionId}/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_index: selectedIndex }),
    });
  },
  ask(sessionId: string, question: string) {
    return request<ControllerResult>(`/api/v1/classroom-sessions/${sessionId}/questions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
  },
  async createVideo(contentId: string) {
    const job = await request<VideoJob>(`/api/v1/learning-contents/${contentId}/videos`, {
      method: "POST",
    });
    if (job.status === "failed") throw new Error(job.error ?? "视频任务失败");
    if (job.status !== "finished" || !job.result_id) {
      throw new Error("视频任务尚未完成");
    }
    return request<VideoResult>(`/api/v1/video-jobs/${job.id}/result`);
  },
  pageImage(materialId: string, pageNumber: number) {
    return `${API_BASE}/api/v1/materials/${materialId}/pages/${pageNumber}/image`;
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
