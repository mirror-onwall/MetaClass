import type {
  ClassroomSession,
  AutoClassroomStep,
  ControllerResult,
  DirectedAgentTurn,
  LearningContent,
  LearningMode,
  Material,
  PageMetadata,
  VideoJob,
  VideoResult,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: init?.signal ?? AbortSignal.timeout(180_000),
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

export const api = {
  upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<{ material: Material; pages: PageMetadata[] }>("/api/v1/materials/process", {
      method: "POST",
      body: form,
    });
  },
  parse(materialId: string) {
    return request<PageMetadata[]>(`/api/v1/materials/${materialId}/parse`, { method: "POST" });
  },
  buildContent(materialId: string) {
    return request<LearningContent>(`/api/v1/materials/${materialId}/learning-content`, {
      method: "POST",
    });
  },
  async createSession(contentId: string, mode: LearningMode) {
    const plan = await request<{ id: string }>(
      `/api/v1/learning-contents/${contentId}/classroom-plans`,
      { method: "POST" },
    );
    return request<ClassroomSession>(`/api/v1/classroom-plans/${plan.id}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
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
  videoDownload(resultId: string) {
    return `${API_BASE}/api/v1/videos/${resultId}/download`;
  },
};
