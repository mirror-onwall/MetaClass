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
  file_hash?: string;
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

export type MaterialCollection = {
  id: string;
  title: string;
  material_ids: string[];
  primary_material_id?: string;
};

export type MaterialProcessingJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  step: string;
  message: string;
  material_ids: string[];
  collection_id?: string;
  error?: string;
};

export type ProcessedMaterials = {
  items: ProcessedMaterial[];
  collection?: MaterialCollection;
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

export type SourceExcerpt = {
  id?: string;
  text: string;
  type: string;
  reason?: string;
  importance: string;
  usage: string;
  source_refs: SourceRef[];
};

export type PageRef = {
  material_id: string;
  page_no: number;
  reason?: string;
};

export type KnowledgeRelation = {
  target_unit_id: string;
  relation_type: string;
  reason?: string;
  confidence: number;
};

export type KnowledgeUnit = {
  id: string;
  title: string;
  unit_type: string;
  summary?: string;
  aliases: string[];
  keywords: string[];
  source_excerpts: SourceExcerpt[];
  source_refs: SourceRef[];
  page_refs: PageRef[];
  source_unit_ids: string[];
  relations: KnowledgeRelation[];
  importance?: string;
  confidence: number;
};

export type LearningSection = {
  id: string;
  title: string;
  role?: string;
  content_goal?: string;
  summary: string;
  key_points?: string[];
  teaching_narrative?: string;
  knowledge_points: string[];
  source_excerpts?: SourceExcerpt[];
  source_refs: SourceRef[];
  page_refs?: PageRef[];
  quiz_items: QuizItem[];
};

export type LearningContent = {
  id: string;
  material_id?: string;
  material_ids?: string[];
  collection_id?: string;
  title: string;
  subtitle?: string;
  audience?: Record<string, unknown>;
  teaching_intent?: Record<string, unknown>;
  material_overview?: Record<string, unknown>;
  knowledge_units?: KnowledgeUnit[];
  objectives: string[];
  sections: LearningSection[];
  generation_guidance?: Record<string, unknown>;
  quality?: Record<string, unknown>;
};

export type ContentGenerationJob = {
  id: string;
  material_id?: string;
  collection_id?: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  step: string;
  message: string;
  content_id?: string;
  error?: string;
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

export type TTSArtifactRequest = {
  text: string;
  scope: string;
  ref_id?: string;
  voice?: string;
};

export type TTSArtifact = {
  id: string;
  text: string;
  scope: string;
  ref_id?: string;
  voice?: string;
  audio_url: string;
  duration_ms: number;
  duration_seconds: number;
  created_at: string;
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
