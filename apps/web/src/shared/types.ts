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
  error?: string;
  source?: string;
  source_role?: "uploaded" | "paper_source" | "presentation_deck";
  parent_material_id?: string;
  derivation_key?: string;
  created_at?: string;
  updated_at?: string;
};

export type PageMetadata = {
  id: string;
  material_id: string;
  page_no: number;
  title: string;
  raw_text?: string;
  image_path?: string;
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
  created_at?: string;
  updated_at?: string;
};

export type MaterialProcessingJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "paused" | "canceled";
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
  formulas?: Array<Record<string, unknown>>;
  examples?: Array<Record<string, unknown>>;
  misconceptions?: Array<Record<string, unknown>>;
  source_refs: SourceRef[];
  page_refs: PageRef[];
  source_unit_ids: string[];
  relations: KnowledgeRelation[];
  importance?: string;
  confidence: number;
};

export type CourseKnowledgeTreeNode = {
  id: string;
  title: string;
  role: string;
  summary?: string;
  parent_id?: string;
  knowledge_unit_ids: string[];
  order: number;
  prerequisite_node_ids: string[];
  node_type?: "section" | "segment" | "knowledge_unit";
  ref_id?: string;
  page_refs?: PageRef[];
};

export type CourseKnowledgeTree = {
  id: string;
  title: string;
  nodes: CourseKnowledgeTreeNode[];
  root_node_ids: string[];
  teaching_sequence: string[];
  orphan_unit_ids: string[];
  warnings: string[];
};

export type TeachingSegment = {
  id: string;
  title: string;
  role: string;
  teaching_goal?: string;
  summary?: string;
  page_refs: PageRef[];
  knowledge_unit_ids: string[];
  prerequisite_segment_ids: string[];
  transition_to_next?: string;
  suggested_delivery?: string;
  interaction_opportunities?: Array<Record<string, unknown>>;
  order: number;
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
  tree_node_ids?: string[];
  quiz_items: QuizItem[];
  segments?: TeachingSegment[];
};

export type LearningContent = {
  id: string;
  material_id?: string;
  material_ids?: string[];
  collection_id?: string;
  organization_mode?: "knowledge" | "source_deck";
  title: string;
  subtitle?: string;
  audience?: Record<string, unknown>;
  teaching_intent?: Record<string, unknown>;
  material_overview?: Record<string, unknown>;
  knowledge_units?: KnowledgeUnit[];
  knowledge_tree?: CourseKnowledgeTree;
  objectives: string[];
  sections: LearningSection[];
  generation_guidance?: Record<string, unknown>;
  quality?: Record<string, unknown>;
};

export type MaterialLearningContentSummary = {
  content_id: string;
  material_ids: string[];
  title: string;
  subtitle?: string;
  updated_at?: string;
};

export type LearningContentDiagnostics = {
  content_id: string;
  knowledge_units: KnowledgeUnit[];
  knowledge_tree?: CourseKnowledgeTree;
  quality: Record<string, unknown>;
};

export type ContentGenerationJob = {
  id: string;
  material_id?: string;
  collection_id?: string;
  organization_mode?: "knowledge" | "source_deck";
  status: "queued" | "running" | "paused" | "succeeded" | "failed";
  progress: number;
  step: string;
  message: string;
  content_id?: string;
  error?: string;
  created_at?: string;
  updated_at?: string;
};

export type LearningMode = "lecture" | "interactive";
export type InteractionIntensity = "none" | "light" | "standard" | "rich";

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

type ActionBase = { id: string; actor: "system" | "teacher" | "student" | "evaluator" };

export type TeachingAction =
  | (ActionBase & {
      type: "SHOW_PAGE";
      payload: { source_ref: SourceRef; slide_no?: number };
    })
  | (ActionBase & {
      type: "SHOW_SLIDE";
      payload: {
        presentation_resource_id: string;
        slide_id: string;
        slide_no: number;
      };
    })
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
      type: "STUDENT_QUESTION";
      payload: {
        qa_id: string;
        preferred_agent_type: StudentAgentType;
        fallback_agent_types: StudentAgentType[];
      };
    })
  | (ActionBase & {
      type: "TEACHER_QA_RESPONSE";
      payload: { qa_id: string };
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
  plan_id: string;
  mode: LearningMode;
  status: "running" | "completed";
  version: number;
  waiting_for: "quiz_answer" | "free_answer" | null;
  student_states: StudentAgentState[];
  mastery: Mastery[];
};

export type ClassroomPlanJob = {
  id: string;
  content_id: string;
  presentation_plan_id?: string;
  status: "queued" | "running" | "paused" | "succeeded" | "failed";
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

export type ClassroomNavigationResult = ControllerResult & {
  page_action: Extract<TeachingAction, { type: "SHOW_PAGE" | "SHOW_SLIDE" }> | null;
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
  mode?: "generated" | "source_deck" | "paper_deck";
  source_material_id?: string;
  presentation_resource_id?: string;
  slides: Array<{
    id: string;
    order: number;
    source_section_ids: string[];
    source_page_no?: number;
    source_kind?: "source" | "generated";
    title: string;
    key_points: string[];
    speaker_script: string;
    suggested_visual: string;
    layout: string;
    visual_payload: string[];
    background: string;
    elements: Array<{
      type: "text" | "shape" | "line" | "image" | "table" | "chart";
      contract_role:
        | "content"
        | "plan_copy"
        | "visual_module"
        | "visual_asset"
        | "visual_placeholder";
      object_id?: string | null;
      semantic_ref?: string | null;
      x: number;
      y: number;
      w: number;
      h: number;
      z: number;
      text?: string;
      items: string[];
      shape: "rectangle" | "rounded_rectangle" | "oval" | "chevron";
      image_path?: string;
      image_fit?: "cover" | "contain";
      table_rows: string[][];
      chart_type: "bar" | "line" | "pie" | "doughnut";
      chart_categories: string[];
      chart_series: number[][];
      chart_series_names: string[];
      style: {
        font_size: number;
        font_role: "sans" | "serif" | "handwritten" | "display" | "mono";
        text_margin_x: number;
        text_margin_y: number;
        bold: boolean;
        color: string;
        fill?: string;
        line_color?: string;
        line_width: number;
        align: "left" | "center" | "right";
        valign: "top" | "middle" | "bottom";
        opacity: number;
      };
    }>;
  }>;
};

export type PaperWorkflowStatus =
  | "queued" | "running" | "paused" | "waiting_for_input"
  | "waiting_for_review" | "succeeded" | "failed" | "canceled";

export type PaperWorkflowJob = {
  id: string;
  source_material_id: string;
  strategy_requested: "auto" | "composed_skills" | "native_paper_deck" | "nature_paper2ppt";
  strategy_selected?: "auto" | "composed_skills" | "native_paper_deck" | "nature_paper2ppt";
  status: PaperWorkflowStatus;
  stage: string;
  progress: number;
  provider_attempts: string[];
  checkpoint_version: number;
  artifact_bundle_id?: string;
  derived_material_id?: string;
  fallback_reason?: string;
  error?: string;
};

export type PaperSourceReference = {
  page_no: number;
  block_id?: string;
  asset_id?: string;
  quote?: string;
};

export type PaperOutline = {
  title: string;
  subtitle?: string;
  paper_type: string;
  narrative_arc: string;
  objectives: string[];
  structure_summary: string;
  sections: Array<{ id: string; title: string; role: string; content_goal: string; slide_ids: string[] }>;
  slides: Array<{
    id: string; order: number; title: string; purpose: string; key_points: string[];
    asset_ids: string[]; speaker_note: string; layout_intent: string;
  }>;
};

export type PaperSlideEvidence = {
  slides: Array<{
    slide_id: string;
    page_no?: number;
    claim_ids: string[];
    source_refs: PaperSourceReference[];
    asset_ids: string[];
    evidence_strength: "direct" | "derived" | "contextual";
  }>;
};

export type PaperDeckCourseResult = {
  paper_job_id: string;
  derived_material_id: string;
  source_paper_material_id: string;
  artifact_bundle_id: string;
  content_id: string;
  presentation_plan_id: string;
};

export type PresentationPlanLibrarySummary = {
  id: string;
  content_id: string;
  title: string;
  slide_count: number;
  artifact_id?: string;
  created_at: string;
};

export type ClassroomPlanLibrarySummary = {
  id: string;
  content_id: string;
  presentation_plan_id?: string;
  scene_count: number;
  action_count: number;
};

export type ClassroomQA = {
  id: string;
  presentation_plan_id: string;
  content_id: string;
  slide_id: string;
  slide_order: number;
  agent_type: StudentAgentType;
  student_profile_id: string;
  knowledge_point: string;
  canonical_question: string;
  student_question: string;
  canonical_answer: string;
  teacher_answer: string;
  moment: "before_explanation" | "during_explanation" | "after_explanation" | "before_next_slide";
  placement_reason: string;
  source_refs: SourceRef[];
  status: "approved" | "rejected";
  generation_id: string;
  archived: boolean;
  created_at: string;
};

export type QuestionBank = {
  presentation_plan_id: string;
  content_id: string;
  generation_id: string;
  items: ClassroomQA[];
};

export type PresentationPlanJob = {
  id: string;
  content_id: string;
  prepare_question_bank: boolean;
  interaction_intensity: InteractionIntensity;
  mode?: "generated" | "source_deck";
  source_material_id?: string;
  status: "queued" | "running" | "paused" | "succeeded" | "failed";
  progress: number;
  step: string;
  message: string;
  plan_id?: string;
  error?: string;
};

export type InteractionPlanningJob = {
  id: string;
  presentation_plan_id: string;
  content_id: string;
  intensity: InteractionIntensity;
  status: "queued" | "running" | "paused" | "succeeded" | "failed";
  progress: number;
  step: string;
  message: string;
  selected_node_count: number;
  completed_node_count: number;
  error?: string;
};

export type PresentationResource = {
  id: string;
  presentation_plan_id: string;
  kind: "source_deck" | "paper_deck" | "generated_artifact";
  source_material_id?: string;
  artifact_id?: string;
  source_file_hash?: string;
  source_page_count?: number;
  is_stale: boolean;
  stale_reason?: string;
  slides: Array<{
    slide_id: string;
    order: number;
    kind: "source" | "generated";
    source_page_no?: number;
    artifact_slide_no?: number;
    image_url?: string;
  }>;
};

export type PresentationSlideDisplay = {
  src: string;
  kind: "source" | "generated";
};

export type PPTThemeOption = {
  id: string;
  name: string;
  description: string;
  style_direction: string;
  colors: {
    cover: string;
    background: string;
    text: string;
    accent: string;
    soft: string;
    secondary: string;
  };
};

export type PPTGenerationJob = {
  id: string;
  presentation_plan_id: string;
  theme_id: string;
  status: "queued" | "running" | "waiting_for_skill" | "paused" | "finished" | "failed";
  progress: number;
  artifact_id?: string;
  error?: string;
  updated_at?: string;
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
