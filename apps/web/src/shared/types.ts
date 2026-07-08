export type SourceRef = {
  material_id: string;
  page_id: string;
  page_no: number;
  text_span?: string;
  image_path?: string;
};

export type Material = {
  id: string;
  filename: string;
  file_type: "pdf" | "pptx";
  status: "uploaded" | "parsing" | "parsed" | "failed";
  page_count: number;
};

export type PageMetadata = {
  id: string;
  material_id: string;
  page_no: number;
  title: string;
  source_refs: SourceRef[];
};

export type QuizItem = {
  id: string;
  question: string;
  options: string[];
  correct_index: number;
  explanation: string;
  knowledge_point: string;
  source_refs: SourceRef[];
};

export type LearningSection = {
  id: string;
  title: string;
  summary: string;
  knowledge_points: string[];
  source_refs: SourceRef[];
  quiz_items: QuizItem[];
};

export type LearningContent = {
  id: string;
  title: string;
  objectives: string[];
  sections: LearningSection[];
};

type ActionBase = { id: string; actor: "system" | "teacher" | "evaluator" };

export type TeachingAction =
  | (ActionBase & { type: "SHOW_PAGE"; payload: { source_ref: SourceRef } })
  | (ActionBase & {
      type: "EXPLAIN";
      payload: { text: string; source_refs: SourceRef[] };
    })
  | (ActionBase & { type: "ASK_QUIZ"; payload: { quiz: QuizItem } })
  | (ActionBase & {
      type: "WAIT_STUDENT";
      payload: { prompt: string; expected_event: "quiz_answer" | "free_answer" };
    })
  | (ActionBase & { type: "GIVE_FEEDBACK"; payload: { quiz_action_id: string } })
  | (ActionBase & {
      type: "REMEDIATE";
      payload: { text: string; source_refs: SourceRef[] };
    })
  | (ActionBase & { type: "END"; payload: { summary: string } });

export type Mastery = { knowledge_point: string; value: number | null; evidence_count: number };

export type ClassroomSession = {
  id: string;
  status: "running" | "completed";
  waiting_for: "quiz_answer" | "free_answer" | null;
  mastery: Mastery[];
};

export type ControllerResult = {
  status: "action" | "waiting" | "completed" | "evaluated" | "answered";
  action: TeachingAction | null;
  feedback?: string;
  correct?: boolean;
  source_refs: SourceRef[];
  session: ClassroomSession;
};

export type VideoJob = {
  id: string;
  content_id: string;
  status: "pending" | "running" | "finished" | "failed";
  progress: number;
  result_id?: string;
  error?: string;
};

export type VideoResult = {
  id: string;
  job_id: string;
  content_id: string;
  video_path: string;
  subtitles_path?: string;
  duration_seconds: number;
};
