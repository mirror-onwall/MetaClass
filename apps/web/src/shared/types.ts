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

export type ProcessedMaterial = {
  material: Material;
  pages: PageMetadata[];
};

export type ProcessedMaterials = {
  items: ProcessedMaterial[];
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

export type LearningMode = "lecture" | "interactive";

export type StudentAgentType =
  | "classroom_atmosphere_regulator"
  | "deep_thinker"
  | "note_taker"
  | "researcher"
  | "foundation_weak"
  | "silent_observer"
  | "concept_confused"
  | "practical_applier";

export type StudentAgentState = {
  id: string;
  profile_id: string;
  display_name: string;
  agent_type: StudentAgentType;
  energy: number;
  pressure: number;
  engagement: number;
  last_intent?: string;
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
      type: "PROBE";
      payload: { question: string; target_knowledge_point: string; source_refs: SourceRef[] };
    })
  | (ActionBase & {
      type: "WAIT_STUDENT";
      payload: { prompt: string; expected_event: "quiz_answer" | "free_answer" };
    })
  | (ActionBase & { type: "GIVE_FEEDBACK"; payload: { quiz_action_id: string } })
  | (ActionBase & {
      type: "REMEDIATE";
      payload: { text: string; source_refs: SourceRef[] };
    })
  | (ActionBase & {
      type: "SUMMARIZE";
      payload: { text: string; source_refs: SourceRef[] };
    })
  | (ActionBase & {
      type: "REVIEW";
      payload: { text: string; knowledge_points: string[]; source_refs: SourceRef[] };
    })
  | (ActionBase & { type: "END"; payload: { summary: string } });

export type Mastery = { knowledge_point: string; value: number | null; evidence_count: number };

export type ClassroomSession = {
  id: string;
  mode: LearningMode;
  status: "running" | "completed";
  waiting_for: "quiz_answer" | "free_answer" | null;
  student_states: StudentAgentState[];
  mastery: Mastery[];
};

export type ClassroomPlanJob = {
  id: string;
  content_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  step: "queued" | "planning" | "persisting" | "completed" | "failed";
  progress: number;
  message: string;
  plan_id?: string;
  error?: string;
};

export type AgentTurn = {
  agent_id: string;
  role: "teacher" | "student" | "assistant" | "evaluator";
  speech: string;
  actions: string[];
  intent: string;
};

export type ControllerDecision = {
  next_role: "teacher" | "student" | "evaluator" | "end";
  next_agent_id: string | null;
  reason: string;
  prompt: string;
};

export type DirectedAgentTurn = {
  decision: ControllerDecision;
  turns: AgentTurn[];
};

export type ControllerResult = {
  status: "action" | "waiting" | "completed" | "evaluated" | "answered";
  action: TeachingAction | null;
  feedback?: string;
  correct?: boolean;
  source_refs: SourceRef[];
  session: ClassroomSession;
};

export type AutoClassroomStep = {
  status: "action" | "agent_turn" | "quiz_answered" | "waiting" | "completed";
  action: TeachingAction | null;
  directed_turn: DirectedAgentTurn | null;
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

export type PresentationPlan = {
  id: string;
  content_id: string;
  title: string;
  slides: Array<{
    id: string;
    order: number;
    source_section_ids: string[];
    title: string;
    key_points: string[];
    speaker_script: string;
    suggested_visual: string;
  }>;
};

export type PPTGenerationJob = {
  id: string;
  presentation_plan_id: string;
  status: "queued" | "running" | "waiting_for_skill" | "finished" | "failed";
  progress: number;
  artifact_id?: string;
  error?: string;
};

export type PPTSlideImage = {
  slide_id: string;
  slide_no: number;
  image_path: string;
  width: number;
  height: number;
};

export type PPTArtifact = {
  id: string;
  job_id: string;
  presentation_plan_id: string;
  pptx_path?: string;
  skill_request_path: string;
  slide_images: PPTSlideImage[];
};
