import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ActionView } from "./features/classroom/ActionView";
import { LibraryPage } from "./features/materials/LibraryPage";
import { SlideNarrationPlayer } from "./features/video/SlideNarrationPlayer";
import {
  type NarrationCue,
  useTTSNarration,
} from "./features/video/useTTSNarration";
import teacherQianqianAvatar from "./assets/agents/teacher-qianqian.png";
import { api } from "./shared/api";
import { formatBytes } from "./shared/format";
import { defaultStudentAgentTypes, studentAgentChoices } from "./shared/studentAgents";
import type {
  ClassroomSession,
  ClassroomPlanJob,
  ContentGenerationJob,
  CourseKnowledgeTreeNode,
  KnowledgeUnit,
  DirectedAgentTurn,
  LearningContent,
  LearningSection,
  LearningMode,
  Material,
  MaterialCollection,
  MaterialProcessingJob,
  PageMetadata,
  PPTArtifact,
  PPTGenerationJob,
  PPTThemeOption,
  PresentationPlan,
  PresentationResource,
  PresentationSlideDisplay,
  PresentationPlanJob,
  StudentAgentType,
  TeachingAction,
  VideoResult,
} from "./shared/types";

const stages = ["导入材料", "页面解析", "组织内容", "互动课堂", "讲解视频"];
const answeringUserQuestionLabel = "老师正在组织回答";

const defaultPptThemeId = "academic_blue";
const fallbackPptThemes: PPTThemeOption[] = [
  {
    id: "academic_blue",
    name: "学术蓝白",
    description: "清晰、克制，适合课程讲解与研究汇报",
    style_direction: "restrained academic editorial with crisp blue hierarchy",
    colors: {
      cover: "12365A",
      background: "FFFFFF",
      text: "17324D",
      accent: "2E75B6",
      soft: "DCEBFA",
      secondary: "4F8FCB",
    },
  },
  {
    id: "scholar_green",
    name: "书院青绿",
    description: "沉静自然，适合人文、地理与通识课程",
    style_direction: "quiet scholarly field notes with botanical green structure",
    colors: {
      cover: "244B3D",
      background: "FFFDF8",
      text: "21392F",
      accent: "B5863B",
      soft: "EAF1E7",
      secondary: "5F8F79",
    },
  },
  {
    id: "warm_classroom",
    name: "暖调课堂",
    description: "亲切、有温度，适合教学活动与案例分享",
    style_direction: "warm classroom editorial with terracotta and parchment cues",
    colors: {
      cover: "713C2D",
      background: "FFFBF5",
      text: "4A2D25",
      accent: "D48743",
      soft: "F6E5D2",
      secondary: "C46C4A",
    },
  },
  {
    id: "editorial_ink",
    name: "墨色编辑",
    description: "理性、极简，适合论文答辩与正式陈述",
    style_direction: "precise monochrome editorial with one cobalt signal color",
    colors: {
      cover: "23272D",
      background: "FFFFFF",
      text: "22262B",
      accent: "315E9E",
      soft: "E7EBF0",
      secondary: "5F7896",
    },
  },
  {
    id: "deep_technology",
    name: "深海科技",
    description: "冷静、前沿，适合技术原理与工程方案",
    style_direction: "deep technical atmosphere with cyan signal paths and precise geometry",
    colors: {
      cover: "0C2836",
      background: "F7FBFD",
      text: "12313F",
      accent: "168FB5",
      soft: "DDF2F7",
      secondary: "48A8B7",
    },
  },
  {
    id: "creative_coral",
    name: "珊瑚创意",
    description: "鲜明、有活力，适合创意表达与成果展示",
    style_direction: "confident creative editorial with coral gestures and deep plum anchors",
    colors: {
      cover: "563141",
      background: "FFFAF8",
      text: "452B35",
      accent: "E36F5C",
      soft: "FBE5DF",
      secondary: "C98A76",
    },
  },
];

const contentStepLabels: Record<string, string> = {
  queued: "等待生成任务",
  building: "准备组织学习内容",
  preparing: "正在准备材料",
  understanding_pages: "正在逐页理解材料",
  extracting_knowledge_units: "正在提取知识单元",
  canonicalizing: "正在融合跨文档知识",
  building_knowledge_tree: "正在构建课程知识树",
  organizing: "正在组织学习内容",
  saving: "正在保存生成结果",
  completed: "学习内容组织完成",
};

function contentProgressLabel(job: ContentGenerationJob): string {
  const label = contentStepLabels[job.step] ?? "正在组织学习内容";
  const pageCounts = job.message.match(/\((\d+)\/(\d+)\)/);
  return pageCounts ? `${label} · ${pageCounts[1]}/${pageCounts[2]} 页` : label;
}

const presentationStepLabels: Record<string, string> = {
  queued: "等待生成 PPT 规划",
  starting: "正在启动 PPT 规划",
  planning_content: "正在规划页面内容",
  planning_scenes: "正在设计页面版式",
  saving: "正在保存 PPT 规划",
  completed: "PPT 规划完成",
  failed: "PPT 规划失败",
};

function presentationProgressLabel(job: PresentationPlanJob): string {
  const label = presentationStepLabels[job.step] ?? "正在生成 PPT 规划";
  const slideCounts = job.message.match(/\((\d+)\/(\d+)\)/);
  return slideCounts ? `${label} · ${slideCounts[1]}/${slideCounts[2]} 页` : label;
}

const classroomPlanStepLabels: Record<string, string> = {
  queued: "等待生成课堂计划",
  planning: "正在生成课堂计划",
  persisting: "正在保存课堂计划",
  completed: "课堂计划完成",
  failed: "课堂计划失败",
};

function classroomPlanProgressLabel(job: ClassroomPlanJob): string {
  return classroomPlanStepLabels[job.step] ?? "正在生成课堂计划";
}

const knowledgeTreeRoleLabels: Record<string, string> = {
  foundation: "基础概念",
  concept: "核心概念",
  method: "方法步骤",
  process: "过程机制",
  mechanism: "作用机制",
  derivation: "推导过程",
  comparison: "对比辨析",
  motivation: "学习动机",
  orientation: "章节导入",
  application: "应用场景",
  example: "案例说明",
  formula: "公式原理",
  case: "综合案例",
  practice: "练习实践",
  summary: "总结回顾",
  reference: "参考资料",
  assessment: "检测评价",
  extension: "拓展关联",
  "导入与概览": "导入与概览",
  "概念讲解": "概念讲解",
  "方法基础": "方法基础",
  "分类框架": "分类框架",
  "核心方法": "核心方法",
  "总结与拓展": "总结与拓展",
};

function formatKnowledgeTreeRole(role: string) {
  return knowledgeTreeRoleLabels[role] ?? role;
}

function formatPageRange(refs: Array<{ page_no: number }> | undefined) {
  const pages = [...new Set((refs ?? []).map((ref) => ref.page_no))].sort((a, b) => a - b);
  if (!pages.length) return "未绑定页面";
  return pages.length === 1 ? `第 ${pages[0]} 页` : `第 ${pages[0]}–${pages.at(-1)} 页`;
}

function sectionPageRefs(section: LearningSection) {
  return section.page_refs?.length ? section.page_refs : section.source_refs;
}

function OutlineSection({ section, index, full = false }: {
  section: LearningSection;
  index: number;
  full?: boolean;
}) {
  const segments = section.segments ?? [];
  return (
    <article className={full ? "outline-section-card" : "outline-section-compact"}>
      <span>{full ? String(index + 1).padStart(2, "0") : index + 1}</span>
      <div>
        <small>{formatPageRange(sectionPageRefs(section))} · {formatKnowledgeTreeRole(section.role ?? "concept")}</small>
        <h3>{section.title}</h3>
        {section.content_goal && <p><strong>学习目标</strong>{section.content_goal}</p>}
        {section.summary && <p>{section.summary}</p>}
        {segments.length > 0 && (
          <ol className="teaching-segment-list">
            {segments.map((segment, segmentIndex) => (
              <li key={segment.id}>
                <span>{index + 1}.{segmentIndex + 1}</span>
                <div>
                  <b>{segment.title}</b>
                  <small>{formatPageRange(segment.page_refs)} · {segment.knowledge_unit_ids.length} 个核心知识单元</small>
                  {segment.teaching_goal && <p>{segment.teaching_goal}</p>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </article>
  );
}

type KnowledgeTreeBranchProps = {
  node: CourseKnowledgeTreeNode;
  indexPath: string;
  depth: number;
  childrenByParent: Map<string, CourseKnowledgeTreeNode[]>;
  nodeById: Map<string, CourseKnowledgeTreeNode>;
  knowledgeUnitsById: Map<string, KnowledgeUnit>;
};

function KnowledgeTreeBranch({
  node,
  indexPath,
  depth,
  childrenByParent,
  nodeById,
  knowledgeUnitsById,
}: KnowledgeTreeBranchProps) {
  const childNodes = childrenByParent.get(node.id) ?? [];
  const prerequisites = node.prerequisite_node_ids
    .map((nodeId) => nodeById.get(nodeId)?.title)
    .filter((title): title is string => Boolean(title));
  const knowledgeUnit = node.node_type === "knowledge_unit" && node.ref_id
    ? knowledgeUnitsById.get(node.ref_id)
    : undefined;
  const relatedUnits = (knowledgeUnit?.relations ?? [])
    .map((relation) => knowledgeUnitsById.get(relation.target_unit_id)?.title)
    .filter((title): title is string => Boolean(title));

  return (
    <li className={`knowledge-tree-item depth-${Math.min(depth, 3)}`}>
      <div className="knowledge-tree-node">
        <span className="knowledge-tree-index">{indexPath}</span>
        <div className="knowledge-tree-content">
          <div className="knowledge-tree-heading">
            <b>{node.title}</b>
            <small>{formatKnowledgeTreeRole(node.role)}</small>
          </div>
          {node.summary && <p>{node.summary}</p>}
          <div className="knowledge-tree-meta">
            {node.node_type === "section" && <span>{childNodes.length} 个教学段</span>}
            {node.node_type === "segment" && <span>{childNodes.length} 个知识单元</span>}
            {!node.node_type && <span>{node.knowledge_unit_ids.length} 个知识单元</span>}
            {!!node.page_refs?.length && <span>来源：{formatPageRange(node.page_refs)}</span>}
            {prerequisites.length > 0 && <span>前置：{prerequisites.join(" / ")}</span>}
            {relatedUnits.length > 0 && <span>关联：{relatedUnits.join(" / ")}</span>}
            {knowledgeUnit && (
              <span>
                公式 {knowledgeUnit.formulas?.length ?? 0} · 案例 {knowledgeUnit.examples?.length ?? 0} · 误区 {knowledgeUnit.misconceptions?.length ?? 0}
              </span>
            )}
          </div>
        </div>
      </div>
      {childNodes.length > 0 && (
        <ol className="knowledge-tree-children">
          {childNodes.map((childNode, childIndex) => (
            <KnowledgeTreeBranch
              key={childNode.id}
              node={childNode}
              indexPath={`${indexPath}.${childIndex + 1}`}
              depth={depth + 1}
              childrenByParent={childrenByParent}
              nodeById={nodeById}
              knowledgeUnitsById={knowledgeUnitsById}
            />
          ))}
        </ol>
      )}
    </li>
  );
}

const runtimeWorkspaceKey = "metaclass-runtime-workspace-v1";
const completedWorkspacesKey = "metaclass-completed-workspaces-v1";

type RuntimeWorkspace = {
  material: Material | null;
  materials: Material[];
  materialCollection: MaterialCollection | null;
  materialProcessingJob: MaterialProcessingJob | null;
  pages: PageMetadata[];
  content: LearningContent | null;
  contentJob: ContentGenerationJob | null;
  presentationPlan: PresentationPlan | null;
  presentationPlanJob: PresentationPlanJob | null;
  pptJob: PPTGenerationJob | null;
  presentationArtifact: PPTArtifact | null;
  presentationSlideImages: Record<number, PresentationSlideDisplay>;
  session: ClassroomSession | null;
  classroomPlanJob: ClassroomPlanJob | null;
  classroomPlanIds: Partial<Record<LearningMode, string>>;
  action: TeachingAction | null;
  currentSlide: { src: string; pageNo: number; generated: boolean } | null;
  learningMode: LearningMode;
  studentAgentTypes: StudentAgentType[];
  agentTurn: DirectedAgentTurn | null;
  feedback: string;
  video: VideoResult | null;
};

type CompletedWorkspace = RuntimeWorkspace & {
  id: string;
  title: string;
  completedAt: string;
};

function loadRuntimeWorkspace(): Partial<RuntimeWorkspace> {
  try {
    const saved = window.sessionStorage.getItem(runtimeWorkspaceKey);
    return saved ? JSON.parse(saved) as RuntimeWorkspace : {};
  } catch {
    window.sessionStorage.removeItem(runtimeWorkspaceKey);
    return {};
  }
}

function loadCompletedWorkspaces(): CompletedWorkspace[] {
  try {
    const saved = window.sessionStorage.getItem(completedWorkspacesKey);
    return saved ? JSON.parse(saved) as CompletedWorkspace[] : [];
  } catch {
    return [];
  }
}

function actionNarrationCue(
  action: TeachingAction,
  plan: PresentationPlan | null,
  currentSlide: { pageNo: number } | null,
): NarrationCue | null {
  const teacher = {
    speaker: "芊芊老师",
    role: "teacher" as const,
    voice: "teacher",
  };
  if (
    action.type === "SHOW_PAGE"
    || action.type === "SHOW_SLIDE"
    || action.type === "END"
    || action.type === "GIVE_FEEDBACK"
    || action.type === "STUDENT_QUESTION"
    || action.type === "TEACHER_QA_RESPONSE"
  ) return null;
  if (action.type === "EXPLAIN") {
    const slide = plan?.slides.find((item) => item.order === currentSlide?.pageNo);
    return {
      id: `action:${action.id}`,
      text: slide?.speaker_script || action.payload.text,
      scope: slide ? "slide_script" : "teacher_action",
      refId: slide?.id ?? action.id,
      ...teacher,
    };
  }
  if (action.type === "ASK_QUIZ") {
    return {
      id: `action:${action.id}`,
      text: `下面我们来进行一个随堂小测验，检验一下大家有没有好好听讲。${action.payload.quiz.question}`,
      scope: "quiz_prompt",
      refId: action.id,
      ...teacher,
    };
  }
  if (action.type === "PROBE") {
    return {
      id: `action:${action.id}`,
      text: `那么现在，我想问大家一个问题，哪位同学能来回答一下呢？${action.payload.question}`,
      scope: "teacher_turn",
      refId: action.id,
      ...teacher,
    };
  }
  if (action.type === "WAIT_STUDENT") {
    return {
      id: `action:${action.id}`,
      text: action.payload.prompt,
      scope: "teacher_turn",
      refId: action.id,
      ...teacher,
    };
  }
  return {
    id: `action:${action.id}`,
    text: action.payload.text,
    scope: action.type === "REMEDIATE" ? "quiz_feedback" : "teacher_action",
    refId: action.id,
    ...teacher,
  };
}

function sourceSlideImages(
  plan: PresentationPlan,
  learningContent: LearningContent,
  sourceMaterial: Material,
  sourcePages: PageMetadata[],
) {
  return Object.fromEntries(plan.slides.map((slide, index) => {
    const section = learningContent.sections.find((item) =>
      slide.source_section_ids.includes(item.id)
    );
    const pageNo = slide.source_page_no
      ?? section?.source_refs[0]?.page_no
      ?? sourcePages[Math.min(index, Math.max(0, sourcePages.length - 1))]?.page_no
      ?? 1;
    return [slide.order, {
      src: api.pageImage(sourceMaterial.id, pageNo),
      kind: "source" as const,
    }];
  }));
}

function presentationResourceSlideImages(resource: PresentationResource) {
  return Object.fromEntries(
    resource.slides
      .filter((slide) => slide.image_url)
      .map((slide) => [
        slide.order,
        {
          src: api.presentationResourceImage(slide.image_url!),
          kind: slide.kind,
        },
      ]),
  );
}

function normalizePresentationSlideImages(
  images: unknown,
  mode?: PresentationPlan["mode"],
): Record<number, PresentationSlideDisplay> {
  if (!images || typeof images !== "object") return {};
  return Object.fromEntries(
    Object.entries(images).flatMap(([pageNo, image]) => {
      if (typeof image === "string") {
        return [[pageNo, {
          src: image,
          kind: mode === "source_deck" ? "source" as const : "generated" as const,
        }]];
      }
      if (
        image
        && typeof image === "object"
        && "src" in image
        && typeof image.src === "string"
        && "kind" in image
        && (image.kind === "source" || image.kind === "generated")
      ) {
        return [[pageNo, image as PresentationSlideDisplay]];
      }
      return [];
    }),
  );
}

function App() {
  const runtimeWorkspace = useRef(loadRuntimeWorkspace()).current;
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const savedTheme = window.localStorage.getItem("metaclass-theme");
    if (savedTheme === "dark" || savedTheme === "light") return savedTheme;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
  const [activePage, setActivePage] = useState<"classroom" | "library">("classroom");
  const [files, setFiles] = useState<File[]>([]);
  const [material, setMaterial] = useState<Material | null>(runtimeWorkspace.material ?? null);
  const [materials, setMaterials] = useState<Material[]>(runtimeWorkspace.materials ?? []);
  const [materialCollection, setMaterialCollection] = useState<MaterialCollection | null>(runtimeWorkspace.materialCollection ?? null);
  const [materialProcessingJob, setMaterialProcessingJob] = useState<MaterialProcessingJob | null>(runtimeWorkspace.materialProcessingJob ?? null);
  const [pages, setPages] = useState<PageMetadata[]>(runtimeWorkspace.pages ?? []);
  const [content, setContent] = useState<LearningContent | null>(runtimeWorkspace.content ?? null);
  const [contentJob, setContentJob] = useState<ContentGenerationJob | null>(runtimeWorkspace.contentJob ?? null);
  const [contentView, setContentView] = useState<"outline" | "tree" | "quality">("outline");
  const [fullPageView, setFullPageView] = useState<"outline" | "tree" | "scripts" | null>(null);
  const [selectedKnowledgeTreeNodeId, setSelectedKnowledgeTreeNodeId] = useState<string | null>(null);
  const [presentationPlan, setPresentationPlan] = useState<PresentationPlan | null>(runtimeWorkspace.presentationPlan ?? null);
  const [presentationMode, setPresentationMode] = useState<"generated" | "source_deck">(
    runtimeWorkspace.presentationPlan?.mode
      ?? (runtimeWorkspace.content ? "source_deck" : "generated"),
  );
  const [editingSlideId, setEditingSlideId] = useState<string | null>(null);
  const [editingScript, setEditingScript] = useState("");
  const [presentationPlanJob, setPresentationPlanJob] = useState<PresentationPlanJob | null>(runtimeWorkspace.presentationPlanJob ?? null);
  const [pptJob, setPptJob] = useState<PPTGenerationJob | null>(runtimeWorkspace.pptJob ?? null);
  const [pptThemes, setPptThemes] = useState<PPTThemeOption[]>(fallbackPptThemes);
  const [pptThemeId, setPptThemeId] = useState(() =>
    window.localStorage.getItem("metaclass-ppt-theme") || defaultPptThemeId,
  );
  const [classroomPlanJob, setClassroomPlanJob] = useState<ClassroomPlanJob | null>(
    runtimeWorkspace.classroomPlanJob ?? null,
  );
  const [presentationArtifact, setPresentationArtifact] = useState<PPTArtifact | null>(runtimeWorkspace.presentationArtifact ?? null);
  const [presentationSlideImages, setPresentationSlideImages] = useState<Record<number, PresentationSlideDisplay>>(
    normalizePresentationSlideImages(
      runtimeWorkspace.presentationSlideImages,
      runtimeWorkspace.presentationPlan?.mode,
    ),
  );
  const [session, setSession] = useState<ClassroomSession | null>(
    runtimeWorkspace.session
      ? { ...runtimeWorkspace.session, version: runtimeWorkspace.session.version ?? 0 }
      : null,
  );
  const [classroomPlanIds, setClassroomPlanIds] = useState<Partial<Record<LearningMode, string>>>(
    runtimeWorkspace.classroomPlanIds ?? (
      runtimeWorkspace.session
        ? { [runtimeWorkspace.session.mode]: runtimeWorkspace.session.plan_id }
        : {}
    ),
  );
  const [action, setAction] = useState<TeachingAction | null>(runtimeWorkspace.action ?? null);
  const [quizResult, setQuizResult] = useState<{
    actionId: string;
    selectedIndex: number;
    correct: boolean;
  } | null>(null);
  const [currentSlide, setCurrentSlide] = useState<{
    src: string;
    pageNo: number;
    generated: boolean;
  } | null>(runtimeWorkspace.currentSlide ?? null);
  const [learningMode, setLearningMode] = useState<LearningMode>(runtimeWorkspace.learningMode ?? "lecture");
  const [studentAgentTypes, setStudentAgentTypes] = useState<StudentAgentType[]>(
    runtimeWorkspace.studentAgentTypes ?? defaultStudentAgentTypes,
  );
  const [hoveredStudentAgentType, setHoveredStudentAgentType] = useState<StudentAgentType | null>(null);
  const [agentTurn, setAgentTurn] = useState<DirectedAgentTurn | null>(runtimeWorkspace.agentTurn ?? null);
  const [feedback, setFeedback] = useState(runtimeWorkspace.feedback ?? "");
  const [question, setQuestion] = useState("");
  const [video, setVideo] = useState<VideoResult | null>(runtimeWorkspace.video ?? null);
  const [autoPlaying, setAutoPlaying] = useState(false);
  const [playbackTrigger, setPlaybackTrigger] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [completedWorkspaces, setCompletedWorkspaces] = useState<CompletedWorkspace[]>(
    loadCompletedWorkspaces,
  );
  const autoStepInFlight = useRef(false);
  const autoPlayingRef = useRef(false);
  const playbackVersionRef = useRef(0);
  const narratedStepRef = useRef<string | null>(null);
  const interruptedTeacherCueRef = useRef<NarrationCue | null>(null);
  const narration = useTTSNarration();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("metaclass-theme", theme);
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    api.getPptThemes()
      .then((options) => {
        if (cancelled || !options.length) return;
        setPptThemes(options);
        setPptThemeId((current) =>
          options.some((option) => option.id === current) ? current : options[0].id,
        );
      })
      .catch(() => {
        // Keep the default theme available while the API is restarting.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    window.localStorage.setItem("metaclass-ppt-theme", pptThemeId);
  }, [pptThemeId]);

  useEffect(() => {
    let cancelled = false;

    async function restoreBackendState() {
      if (runtimeWorkspace.session?.id) {
        api.getClassroomSession(runtimeWorkspace.session.id)
          .then((latest) => { if (!cancelled) setSession(latest); })
          .catch(() => undefined);
      }

      const savedMaterialJob = runtimeWorkspace.materialProcessingJob;
      if (savedMaterialJob?.id) {
        try {
          const latest = await api.getMaterialProcessingJob(savedMaterialJob.id);
          if (cancelled) return;
          setMaterialProcessingJob(latest);
          if (["queued", "running"].includes(latest.status)) {
            const result = await api.waitForMaterialProcessingJob(latest.id, (job) => {
              if (!cancelled) setMaterialProcessingJob(job);
            });
            if (!cancelled) {
              setMaterials(result.items.map((item) => item.material));
              setMaterialCollection(result.collection ?? null);
              setMaterial(result.items[0]?.material ?? null);
              setPages(result.items[0]?.pages ?? []);
            }
          } else if (latest.status === "succeeded") {
            const result = await api.getMaterialProcessingJobResult(latest.id);
            if (!cancelled) {
              setMaterials(result.items.map((item) => item.material));
              setMaterialCollection(result.collection ?? null);
              setMaterial(result.items[0]?.material ?? null);
              setPages(result.items[0]?.pages ?? []);
            }
          }
        } catch {
          // The latest job state remains visible for explicit retry or discard.
        }
      }

      const savedContentJob = runtimeWorkspace.contentJob;
      if (savedContentJob?.id) {
        try {
          const latest = await api.getContentGenerationJob(savedContentJob.id);
          if (cancelled) return;
          setContentJob(latest);
          if (["queued", "running"].includes(latest.status)) {
            const result = await api.waitForContentGenerationJob(latest.id, (job) => {
              if (!cancelled) setContentJob(job);
            });
            if (!cancelled) setContent(result);
          } else if (latest.status === "succeeded") {
            const result = await api.getContentGenerationJobResult(latest.id);
            if (!cancelled) setContent(result);
          }
        } catch {
          // A paused/failed task is kept in the workspace for the user's decision.
        }
      }

      const savedPlanJob = runtimeWorkspace.presentationPlanJob;
      if (savedPlanJob?.id) {
        try {
          let latest = await api.getPresentationPlanJob(savedPlanJob.id);
          if (cancelled) return;
          setPresentationPlanJob(latest);
          if (["queued", "running"].includes(latest.status)) {
            latest = await api.waitForPresentationPlanJob(latest.id, (job) => {
              if (!cancelled) setPresentationPlanJob(job);
            });
          }
          if (latest.status === "succeeded") {
            const plan = await api.getPresentationPlanJobResult(latest.id);
            if (!cancelled) {
              setPresentationPlan(plan);
              const resource = await api.getPresentationResource(plan.id);
              if (!cancelled) setPresentationSlideImages(presentationResourceSlideImages(resource));
            }
          }
        } catch {
          // A paused/failed task is kept in the workspace for the user's decision.
        }
      }

      const savedPptJob = runtimeWorkspace.pptJob;
      if (savedPptJob?.id) {
        try {
          let latest = await api.getPptJob(savedPptJob.id);
          if (cancelled) return;
          setPptJob(latest);
          if (["queued", "running", "waiting_for_skill"].includes(latest.status)) {
            latest = await api.waitForPptJob(latest.id, (job) => {
              if (!cancelled) setPptJob(job);
            });
          }
          if (latest.status === "finished" && latest.artifact_id) {
            const artifact = await api.getPptArtifact(latest.artifact_id);
            if (!cancelled) setPresentationArtifact(artifact);
          }
        } catch {
          // A paused/failed task is kept in the workspace for the user's decision.
        }
      }
    }

    void restoreBackendState();
    return () => { cancelled = true; };
  }, [runtimeWorkspace]);

  useEffect(() => {
    if (!content || !presentationPlan || session || classroomPlanJob) return;
    let cancelled = false;
    api.getLatestClassroomPlanJob(content.id, presentationPlan.id)
      .then((job) => {
        if (!cancelled && job.status !== "failed") setClassroomPlanJob(job);
      })
      .catch(() => {
        // No prior classroom task exists for this content and presentation plan.
      });
    return () => {
      cancelled = true;
    };
  }, [classroomPlanJob, content, presentationPlan, session]);

  useEffect(() => {
    const snapshot: RuntimeWorkspace = {
      material,
      materials,
      materialCollection,
      materialProcessingJob,
      pages,
      content,
      contentJob,
      presentationPlan,
      presentationPlanJob,
      pptJob,
      presentationArtifact,
      presentationSlideImages,
      session,
      classroomPlanJob,
      classroomPlanIds,
      action,
      currentSlide,
      learningMode,
      studentAgentTypes,
      agentTurn,
      feedback,
      video,
    };
    try {
      window.sessionStorage.setItem(runtimeWorkspaceKey, JSON.stringify(snapshot));
    } catch {
      // A very large source document may exceed the browser's per-tab storage quota.
      // Keep the live workspace usable even when a snapshot cannot be written.
    }
  }, [
    action,
    agentTurn,
    content,
    contentJob,
    currentSlide,
    feedback,
    learningMode,
    material,
    materialCollection,
    materialProcessingJob,
    materials,
    pages,
    presentationArtifact,
    presentationPlan,
    presentationPlanJob,
    pptJob,
    presentationSlideImages,
    session,
    classroomPlanIds,
    classroomPlanJob,
    studentAgentTypes,
    video,
  ]);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(completedWorkspacesKey, JSON.stringify(completedWorkspaces));
    } catch {
      // Completed projects remain persisted by the backend even if browser storage is full.
    }
  }, [completedWorkspaces]);

  const activeStage = useMemo(() => {
    if (video) return 4;
    if (session) return 3;
    if (content) return 2;
    if (pages.length) return 1;
    return 0;
  }, [content, pages.length, session, video]);
  const hoveredStudentAgent = studentAgentChoices.find(
    (agent) => agent.type === hoveredStudentAgentType,
  );
  const selectedFilesSize = useMemo(
    () => files.reduce((total, selectedFile) => total + selectedFile.size, 0),
    [files],
  );
  const selectedFilesLabel = files.length > 1
    ? `${files[0].name} +${files.length - 1}`
    : files[0]?.name;
  const knowledgeTreeModel = useMemo(() => {
    const nodeById = new Map<string, CourseKnowledgeTreeNode>();
    const childrenByParent = new Map<string, CourseKnowledgeTreeNode[]>();
    const knowledgeUnitsById = new Map(
      (content?.knowledge_units ?? []).map((unit) => [unit.id, unit]),
    );
    if (!content?.knowledge_tree) {
      return { rootNodes: [], nodeById, childrenByParent, knowledgeUnitsById };
    }

    content.knowledge_tree.nodes.forEach((node) => {
      nodeById.set(node.id, node);
      if (node.parent_id) {
        const siblings = childrenByParent.get(node.parent_id) ?? [];
        siblings.push(node);
        childrenByParent.set(node.parent_id, siblings);
      }
    });
    childrenByParent.forEach((nodes) => nodes.sort((left, right) => left.order - right.order));

    const rootNodes = content.knowledge_tree.root_node_ids
      .map((nodeId) => nodeById.get(nodeId))
      .filter((node): node is CourseKnowledgeTreeNode => Boolean(node))
      .sort((left, right) => left.order - right.order);
    return { rootNodes, nodeById, childrenByParent, knowledgeUnitsById };
  }, [content]);
  const selectedKnowledgeTreeNode = selectedKnowledgeTreeNodeId
    ? knowledgeTreeModel.nodeById.get(selectedKnowledgeTreeNodeId) ?? null
    : null;
  const selectedKnowledgeTreeIndex = selectedKnowledgeTreeNode
    ? knowledgeTreeModel.rootNodes.findIndex((node) => node.id === selectedKnowledgeTreeNode.id)
    : -1;
  const qualityWarnings = Array.isArray(content?.quality?.warnings)
    ? content.quality.warnings.filter((warning): warning is string => typeof warning === "string")
    : [];
  const coverageScore = typeof content?.quality?.coverage_score === "number"
    ? Math.round(content.quality.coverage_score * 100)
    : null;
  const activeAgentSpeech = agentTurn?.turns.at(-1);
  const userQuestionBlockedByAgentExchange = Boolean(
    activeAgentSpeech?.role === "student"
    || (
      activeAgentSpeech?.role === "teacher"
      && ["scripted_qa_answer", "teacher_reply_to_student"].includes(activeAgentSpeech.intent)
    ),
  );
  function displayAgentName(agentId: string, role: DirectedAgentTurn["turns"][number]["role"]) {
    if (role === "teacher") return "芊芊老师";
    const studentState = session?.student_states.find((student) => student.id === agentId);
    const studentAgent = studentAgentChoices.find(
      (agent) => agent.type === studentState?.agent_type,
    );
    return studentAgent ? `${studentAgent.studentName} · ${studentAgent.name}` : agentId;
  }
  const captionTurn = narration.cue?.agentId
    ? agentTurn?.turns.find((turn) => turn.agent_id === narration.cue?.agentId)
    : feedback
      ? agentTurn?.turns.find((turn) => turn.speech === feedback)
      : undefined;
  const captionText = narration.cue?.text ?? feedback;
  const captionSpeaker = narration.cue?.speaker
    ?? (captionTurn ? displayAgentName(captionTurn.agent_id, captionTurn.role) : "芊芊老师");
  const captionStudentState = (narration.cue?.role ?? captionTurn?.role) === "student"
    ? session?.student_states.find(
        (student) => student.id === (narration.cue?.agentId ?? captionTurn?.agent_id),
      )
    : undefined;
  const captionStudentAgent = studentAgentChoices.find(
    (agent) => agent.type === captionStudentState?.agent_type,
  );
  const isUserNarration = narration.cue?.agentId === "user";
  const captionAvatar = captionStudentAgent?.avatar ?? teacherQianqianAvatar;
  const currentLessonProgress = useMemo(() => {
    if (!currentSlide || !presentationPlan?.slides.length || !content) return null;
    const slide = presentationPlan.slides.find((item) => item.order === currentSlide.pageNo);
    if (!slide) return null;
    const sourceSections = slide.source_section_ids
      .map((sectionId) => content.sections.find((section) => section.id === sectionId))
      .filter((section): section is NonNullable<typeof section> => Boolean(section));
    const knowledgePoints = sourceSections
      .flatMap((section) => section.knowledge_points)
      .filter((point, index, points) => Boolean(point) && points.indexOf(point) === index);
    const isCoverSlide = slide.order === 1;
    const isSummarySlide = slide.order === presentationPlan.slides.length;
    return {
      current: slide.order,
      total: presentationPlan.slides.length,
      knowledgePoint: isCoverSlide
        ? slide.title
        : isSummarySlide
          ? "总结"
          : knowledgePoints.slice(0, 2).join(" · ")
            || sourceSections.map((section) => section.title).join(" · ")
            || slide.title,
    };
  }, [content, currentSlide, presentationPlan]);
  const speechTextRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    const speech = speechTextRef.current;
    if (!speech || !captionText || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    speech.scrollTop = 0;
    let direction = 1;
    let restingTicks = 28;
    const timer = window.setInterval(() => {
      if (speech.matches(":hover, :focus")) return;
      if (restingTicks > 0) {
        restingTicks -= 1;
        return;
      }
      const maxScroll = speech.scrollHeight - speech.clientHeight;
      if (maxScroll <= 2) return;
      speech.scrollTop += direction;
      if (speech.scrollTop >= maxScroll - 1) {
        direction = -1;
        restingTicks = 36;
      } else if (speech.scrollTop <= 1 && direction < 0) {
        direction = 1;
        restingTicks = 36;
      }
    }, 48);

    return () => window.clearInterval(timer);
  }, [captionText]);

  useEffect(() => {
    autoPlayingRef.current = autoPlaying;
  }, [autoPlaying]);

  useEffect(() => {
    if (!session || session.status === "completed" || !autoPlayingRef.current) return;
    const stepKey = agentTurn?.turns.length
      ? `turn:${agentTurn.turns.map((turn) => `${turn.agent_id}:${turn.intent}:${turn.speech}`).join("|")}`
      : action
        ? `action:${action.id}`
        : autoPlaying
          ? `idle:${session.id}`
          : null;
    if (!stepKey || narratedStepRef.current === stepKey) return;
    narratedStepRef.current = stepKey;
    const playbackVersion = playbackVersionRef.current;
    let cancelled = false;

    async function playCurrentBeat() {
      if (!session) return;
      const cues: NarrationCue[] = agentTurn?.turns.length
        ? agentTurn.turns.map((turn, index) => {
            const studentState = session.student_states.find(
              (student) => student.id === turn.agent_id,
            );
            return {
              id: `${stepKey}:${index}`,
              text: turn.speech,
              scope: turn.role === "student" ? "student_turn" : "teacher_turn",
              refId: `${session.id}:${turn.agent_id}:${turn.intent}:${index}`,
              voice: turn.role === "student"
                ? `student_${studentState?.agent_type ?? turn.agent_id}`
                : "teacher",
              speaker: displayAgentName(turn.agent_id, turn.role),
              agentId: turn.agent_id,
              role: turn.role,
            };
          })
        : action
          ? [actionNarrationCue(action, presentationPlan, currentSlide)].filter(
              (cue): cue is NarrationCue => Boolean(cue),
            )
          : [];

      if (!cues.length) {
        await new Promise((resolve) => window.setTimeout(resolve, 300));
      } else {
        setFeedback("");
      }
      void narration.prepareAll(cues);
      for (const cue of cues) {
        const result = await narration.play(cue);
        if (cancelled || result === "cancelled") return;
        if (result === "blocked") {
          playbackVersionRef.current += 1;
          autoPlayingRef.current = false;
          setAutoPlaying(false);
          setError("浏览器尚未启用声音，请点击开始自动课堂重试");
          narratedStepRef.current = null;
          return;
        }
        if (result === "failed") {
          setError("语音暂时不可用，课堂已切换为无声模式并继续推进");
        }
      }
      if (
        !cancelled
        && autoPlayingRef.current
        && playbackVersion === playbackVersionRef.current
      ) {
        await autoStep(playbackVersion);
      } else if (!cancelled) {
        narration.clearCue();
      }
    }

    void playCurrentBeat();
    return () => {
      cancelled = true;
    };
  // currentSlide is updated as a consequence of SHOW_SLIDE/SHOW_PAGE. Depending on it here
  // cancels the short slide beat before autoStep can advance, while the same
  // action has already been marked narrated. Keep slide rendering independent
  // from the classroom playback state machine.
  }, [action, agentTurn, playbackTrigger, presentationPlan, session]);

  useEffect(() => {
    if (!feedback) return;
    const timer = window.setTimeout(() => setFeedback(""), 5800);
    return () => window.clearTimeout(timer);
  }, [feedback]);

  useEffect(() => {
    if (!action || (action.type !== "SHOW_PAGE" && action.type !== "SHOW_SLIDE")) return;
    displayPage(action);
  }, [action, material, presentationSlideImages]);

  function displayPage(
    pageAction: Extract<TeachingAction, { type: "SHOW_PAGE" | "SHOW_SLIDE" }>,
  ) {
    const pageNo = pageAction.type === "SHOW_SLIDE"
      ? pageAction.payload.slide_no
      : pageAction.payload.slide_no ?? pageAction.payload.source_ref.page_no;
    const slideImage = presentationSlideImages[pageNo];
    const availablePages = Object.keys(presentationSlideImages)
      .map(Number)
      .sort((left, right) => left - right);
    if (availablePages.length) {
      if (slideImage) {
        setCurrentSlide({
          src: slideImage.src,
          pageNo,
          generated: slideImage.kind === "generated",
        });
      } else {
        setCurrentSlide((previous) => {
          if (previous) return previous;
          const fallbackPage = availablePages.at(-1)!;
          const fallbackImage = presentationSlideImages[fallbackPage];
          return {
            src: fallbackImage.src,
            pageNo: fallbackPage,
            generated: fallbackImage.kind === "generated",
          };
        });
      }
      return;
    }
    const fallbackImage = material ? api.pageImage(material.id, pageNo) : "";
    setCurrentSlide({
      src: fallbackImage,
      pageNo,
      generated: false,
    });
  }

  async function run<T>(label: string, task: () => Promise<T>): Promise<T | undefined> {
    setBusy(label);
    setError(null);
    try {
      return await task();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "发生未知错误";
      if (message !== "材料处理已取消" && message !== "任务已暂停，进度已经保存") setError(message);
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  function chooseFiles(nextFiles: File[]) {
    if (!nextFiles.length) return;
    const invalidFile = nextFiles.find((nextFile) => {
      const extension = nextFile.name.split(".").pop()?.toLowerCase();
      return extension !== "pdf" && extension !== "pptx";
    });
    if (invalidFile) {
      setError("请选择 PDF 或 PPTX 文件");
      return;
    }
    reset(false);
    setFiles(nextFiles);
  }

  function reset(clearFile = true) {
    playbackVersionRef.current += 1;
    autoPlayingRef.current = false;
    if (clearFile) setFiles([]);
    setMaterial(null);
    setMaterials([]);
    setMaterialCollection(null);
    setMaterialProcessingJob(null);
    setPages([]);
    setContent(null);
    setContentJob(null);
    setContentView("outline");
    setFullPageView(null);
    setPresentationPlan(null);
    setPresentationMode("generated");
    setEditingSlideId(null);
    setEditingScript("");
    setPresentationPlanJob(null);
    setPptJob(null);
    setClassroomPlanJob(null);
    setPresentationArtifact(null);
    setPresentationSlideImages({});
    setSession(null);
    setStudentAgentTypes(defaultStudentAgentTypes);
    setClassroomPlanIds({});
    setAction(null);
    setCurrentSlide(null);
    setAgentTurn(null);
    setFeedback("");
    setAutoPlaying(false);
    setVideo(null);
    setError(null);
    narratedStepRef.current = null;
    narration.stop();
    window.sessionStorage.removeItem(runtimeWorkspaceKey);
  }

  function useLibraryMaterial(nextMaterial: Material, nextPages: PageMetadata[]) {
    reset();
    setMaterial(nextMaterial);
    setMaterials([nextMaterial]);
    setPages(nextPages);
    setActivePage("classroom");
  }

  async function openLibraryAsset(asset: {
    material: Material;
    pages: PageMetadata[];
    content: LearningContent;
    presentationPlan?: PresentationPlan;
    presentationArtifact?: PPTArtifact;
    session?: ClassroomSession;
  }) {
    reset();
    setMaterial(asset.material);
    setMaterials([asset.material]);
    setPages(asset.pages);
    setContent(asset.content);
    setPresentationPlan(asset.presentationPlan ?? null);
    setPresentationMode(asset.presentationPlan?.mode ?? "generated");
    setPresentationArtifact(asset.presentationArtifact ?? null);
    if (asset.presentationPlan) {
      const resource = await api.getPresentationResource(asset.presentationPlan.id);
      const resourceImages = presentationResourceSlideImages(resource);
      const images = Object.keys(resourceImages).length
        ? resourceImages
        : asset.presentationArtifact
          ? Object.fromEntries(asset.presentationArtifact.slide_images.map((slide) => [
              slide.slide_no,
              {
                src: api.pptSlideImage(asset.presentationArtifact!.id, slide.slide_no),
                kind: "generated" as const,
              },
            ]))
          : sourceSlideImages(asset.presentationPlan, asset.content, asset.material, asset.pages);
      setPresentationSlideImages(images);
    }
    setSession(asset.session ?? null);
    if (asset.session) {
      setStudentAgentTypes(asset.session.student_states.map((student) => student.agent_type));
      setLearningMode(asset.session.mode);
      setClassroomPlanIds({ [asset.session.mode]: asset.session.plan_id });
      setAutoPlaying(true);
      autoPlayingRef.current = true;
      setFeedback("已从资料库载入课堂剧本，自动播放已开始。");
    }
    setActivePage("classroom");
  }

  async function upload() {
    if (!files.length) return;
    setMaterialProcessingJob(null);
    const result = await run("正在上传并解析材料", () =>
      api.uploadMany(files, setMaterialProcessingJob),
    );
    if (result) {
      const processed = "items" in result ? result.items : [result];
      if (!processed.length) return;
      setMaterials(processed.map((item) => item.material));
      setMaterialCollection("collection" in result ? result.collection ?? null : null);
      setMaterial(processed[0].material);
      setPages(processed[0].pages);
      setMaterialProcessingJob(null);
    }
  }

  async function pauseMaterialProcessing() {
    const job = materialProcessingJob;
    if (!job || job.status === "succeeded" || job.status === "failed" || job.status === "paused" || job.status === "canceled") return;
    try {
      const paused = await api.pauseMaterialProcessingJob(job.id);
      setMaterialProcessingJob(paused);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "暂停材料处理失败");
      return;
    }
    setBusy(null);
    setFeedback("已停止等待；后端将在当前解析操作结束后保存并暂停。之后可以从中断处继续。");
  }

  function terminateCurrentMaterial() {
    const hasPreparedAssets = Boolean(content || presentationPlan || session);
    if (
      hasPreparedAssets
      && !window.confirm("确定终止当前材料并返回上传入口吗？已经保存到资料库的内容不会被删除。")
    ) return;
    reset();
  }

  async function parse() {
    if (!material) return;
    const result = await run("正在拆解页面", () => api.parse(material.id));
    if (result) {
      setPages(result);
      setMaterial({ ...material, status: "parsed", page_count: result.length });
    }
  }

  async function buildContent() {
    if (!material) return;
    setContentJob(null);
    const result = await run("正在组织学习内容", () =>
      presentationMode === "source_deck"
        ? api.buildSourceDeckContent(material.id, setContentJob)
        : materialCollection
        ? api.buildCollectionContent(materialCollection.id, setContentJob)
        : api.buildContent(material.id, setContentJob),
    );
    if (result) {
      setContent(result);
      setFeedback(
        presentationMode === "source_deck"
          ? "原稿讲解路线的 LearningContent 已完成，下一步生成逐页讲稿。"
          : "重新生成 PPT 路线的 LearningContent 已完成，下一步生成 PresentationPlan。",
      );
    }
  }

  async function pauseCurrentContentWork() {
    try {
      if (contentJob && ["queued", "running"].includes(contentJob.status)) {
        setContentJob(await api.pauseContentGenerationJob(contentJob.id));
      } else if (presentationPlanJob && ["queued", "running"].includes(presentationPlanJob.status)) {
        setPresentationPlanJob(await api.pausePresentationPlanJob(presentationPlanJob.id));
      } else if (pptJob && ["queued", "running", "waiting_for_skill"].includes(pptJob.status)) {
        setPptJob(await api.pausePptJob(pptJob.id));
      } else return;
      setBusy(null);
      setFeedback("已停止等待；当前模型请求返回并保存 checkpoint 后，任务会停在这里。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "暂停任务失败");
    }
  }

  async function resumePausedContentWork() {
    if (materialProcessingJob?.status === "paused") {
      const result = await run("正在从已保存进度继续解析材料", async () => {
        await api.resumeMaterialProcessingJob(materialProcessingJob.id);
        return api.waitForMaterialProcessingJob(materialProcessingJob.id, setMaterialProcessingJob);
      });
      if (result) {
        setMaterials(result.items.map((item) => item.material));
        setMaterialCollection(result.collection ?? null);
        setMaterial(result.items[0]?.material ?? null);
        setPages(result.items[0]?.pages ?? []);
      }
      return;
    }
    if (contentJob?.status === "paused") {
      const result = await run("正在从已保存进度继续组织学习内容", async () => {
        await api.resumeContentGenerationJob(contentJob.id);
        return api.waitForContentGenerationJob(contentJob.id, setContentJob);
      });
      if (result) setContent(result);
      return;
    }
    if (presentationPlanJob?.status === "paused") {
      const plan = await run("正在从已保存 Segment 继续生成讲稿", async () => {
        await api.resumePresentationPlanJob(presentationPlanJob.id);
        const finished = await api.waitForPresentationPlanJob(presentationPlanJob.id, setPresentationPlanJob);
        return api.getPresentationPlanJobResult(finished.id);
      });
      if (!plan) return;
      setPresentationPlan(plan);
      const resource = await api.getPresentationResource(plan.id);
      setPresentationSlideImages(presentationResourceSlideImages(resource));
      return;
    }
    if (pptJob?.status === "paused") {
      const artifact = await run("正在恢复 PPT 生成", async () => {
        await api.resumePptJob(pptJob.id);
        const finished = await api.waitForPptJob(pptJob.id, setPptJob);
        return api.getPptArtifact(finished.artifact_id!);
      });
      if (artifact) setPresentationArtifact(artifact);
    }
  }

  async function discardPausedContentWork() {
    if (!window.confirm("确定放弃并删除这次未完成的临时进度吗？已经正式完成的资料不会删除。")) return;
    if (materialProcessingJob?.status === "paused") {
      await api.discardMaterialProcessingJob(materialProcessingJob.id);
      setMaterialProcessingJob(null);
      setFiles([]);
    } else if (contentJob?.status === "paused") {
      await api.discardContentGenerationJob(contentJob.id);
      setContentJob(null);
    } else if (presentationPlanJob?.status === "paused") {
      await api.discardPresentationPlanJob(presentationPlanJob.id);
      setPresentationPlanJob(null);
    } else if (pptJob?.status === "paused") {
      await api.discardPptJob(pptJob.id);
      setPptJob(null);
    }
    setFeedback("未完成的临时进度已删除。");
  }

  async function preparePresentationPlan() {
    if (!content || !material) return;
    setPresentationPlanJob(null);
    const useSourceDeck = presentationMode === "source_deck";
    const plan = await run(
      useSourceDeck ? "正在分析原 PPT 并生成逐页讲稿" : "正在生成 PresentationPlan",
      () => useSourceDeck
        ? api.createSourceDeckPresentationPlan(
            content.id,
            material.id,
            true,
            setPresentationPlanJob,
          )
        : api.createPresentationPlan(
            content.id,
            true,
            setPresentationPlanJob,
          ),
    );
    if (!plan) return;
    setPresentationPlan(plan);
    setPresentationArtifact(null);
    const resource = await api.getPresentationResource(plan.id);
    const resourceImages = presentationResourceSlideImages(resource);
    setPresentationSlideImages(
      Object.keys(resourceImages).length
        ? resourceImages
        : sourceSlideImages(plan, content, material, pages),
    );
    setFeedback(
      plan.mode === "source_deck"
        ? "原 PPT 逐页讲稿已保存，可以直接创建课堂。"
        : "PresentationPlan 已保存。现在可以单独生成 PPT，或直接创建课堂剧本。",
    );
  }

  async function generatePresentationArtifact() {
    if (!presentationPlan) return;
    setPptJob(null);
    const artifact = await run("正在生成 PPT", () =>
      api.generatePptForPlan(presentationPlan.id, pptThemeId, setPptJob),
    );
    if (!artifact) return;
    setPresentationArtifact(artifact);
    const resource = await api.getPresentationResource(presentationPlan.id);
    setPresentationSlideImages(presentationResourceSlideImages(resource));
    setFeedback("PPT 已生成并保存，可以继续创建课堂。");
  }

  function chooseAnotherPresentationRoute() {
    if (session) return;
    setContent(null);
    setContentJob(null);
    setPresentationPlan(null);
    setPresentationPlanJob(null);
    setPresentationArtifact(null);
    setPresentationSlideImages({});
    setPptJob(null);
    setEditingSlideId(null);
    setEditingScript("");
    setFeedback("请从 LearningContent 构建前重新选择路线；原来保存的内容和计划不会被删除。");
  }

  async function saveSpeakerScript() {
    if (!presentationPlan || !editingSlideId || !editingScript.trim()) return;
    const updated = await run("正在保存逐页讲稿", () =>
      api.updateSlideSpeakerScript(
        presentationPlan.id,
        editingSlideId,
        editingScript.trim(),
      ),
    );
    if (!updated) return;
    setPresentationPlan(updated);
    setFeedback("逐页讲稿已保存；之后创建的课堂会使用新讲稿。");
  }

  async function startClassroom() {
    if (!content || !presentationPlan) return;
    if (presentationPlan.mode === "generated" && !presentationArtifact) {
      setError("重新生成 PPT 模式需要先完成 PPT 生成，才能创建课堂。");
      return;
    }
    const resource = await run("正在校验演示资源", () =>
      api.getPresentationResource(presentationPlan.id),
    );
    if (!resource) return;
    if (resource.is_stale) {
      setError(`原 PPT 已发生变化，请重新生成演示计划（${resource.stale_reason ?? "资源失效"}）`);
      return;
    }
    narration.unlock();
    const result = await run(
      classroomPlanJob && classroomPlanJob.status !== "failed"
        ? "正在接回课堂创建任务"
        : learningMode === "interactive"
          ? "正在创建互动课堂剧本"
          : "正在创建连续讲解剧本",
      async () => {
      let reusableJob = classroomPlanJob;
      if (
        !reusableJob
        || reusableJob.status === "failed"
        || reusableJob.content_id !== content.id
        || reusableJob.presentation_plan_id !== presentationPlan.id
      ) {
        reusableJob = await api.getLatestClassroomPlanJob(
          content.id,
          presentationPlan.id,
        ).catch(() => null);
      }
      if (!reusableJob || reusableJob.status === "failed") {
        reusableJob = await api.createClassroomPlanJob(
          content.id,
          presentationPlan.id,
          setClassroomPlanJob,
        );
      }
      const finishedJob = await api.waitForClassroomPlanJob(
        reusableJob.id,
        setClassroomPlanJob,
      );
      return api.createSessionForPlan(
        finishedJob.plan_id,
        learningMode,
        studentAgentTypes,
      );
    });
    if (result) {
      playbackVersionRef.current += 1;
      autoPlayingRef.current = true;
      setSession(result);
      setClassroomPlanIds((current) => ({
        ...current,
        [learningMode]: result.plan_id,
      }));
      setAction(null);
      setAgentTurn(null);
      setAutoPlaying(true);
      setFeedback("课堂剧本已保存，自动播放已开始。你可以随时输入问题打断。");
      narratedStepRef.current = null;
    }
  }

  async function switchClassroomMode(nextMode: LearningMode) {
    if (nextMode === learningMode && session?.mode === nextMode) return;
    if (!session) {
      setLearningMode(nextMode);
      return;
    }
    if (!content || !presentationPlan) return;
    narration.stop();
    const nextSession = await run(
      nextMode === "interactive" ? "正在切换到互动课堂" : "正在切换到连续课堂",
      async () => {
        return api.switchClassroomMode(
          session.id,
          nextMode,
          session.version,
          studentAgentTypes,
        );
      },
    );
    if (!nextSession) return;
    playbackVersionRef.current += 1;
    autoPlayingRef.current = false;
    setLearningMode(nextMode);
    setSession(nextSession);
    setClassroomPlanIds((current) => ({ ...current, [nextMode]: nextSession.plan_id }));
    setAgentTurn(null);
    setAutoPlaying(false);
    setFeedback(nextMode === "interactive" ? "已启用题库和课堂互动。" : "已切换为连续讲解，题库和互动动作不会执行。");
    narratedStepRef.current = null;
  }

  async function replayClassroom() {
    if (!session) return;
    narration.unlock();
    const replay = await run("正在重新准备课堂", () => api.replaySession(session.id));
    if (!replay) return;
    playbackVersionRef.current += 1;
    autoPlayingRef.current = true;
    setSession(replay);
    setAction(null);
    setAgentTurn(null);
    setCurrentSlide(null);
    setAutoPlaying(true);
    setFeedback("课堂已回到开头，正在重新播放。");
    narratedStepRef.current = null;
  }

  async function completeWorkspace() {
    if (!content || !presentationPlan) return;
    const completed: CompletedWorkspace = {
      id: `${content.id}:${Date.now()}`,
      title: content.title,
      completedAt: new Date().toISOString(),
      material,
      materials,
      materialCollection,
      materialProcessingJob,
      pages,
      content,
      contentJob,
      presentationPlan,
      presentationPlanJob,
      pptJob,
      presentationArtifact,
      presentationSlideImages,
      session,
      classroomPlanJob,
      classroomPlanIds,
      action,
      currentSlide,
      learningMode,
      studentAgentTypes,
      agentTurn,
      feedback,
      video,
    };
    setCompletedWorkspaces((current) => [completed, ...current.filter((item) => item.content?.id !== content.id)]);
    reset();
    setFeedback("当前课堂项目已归档；已有内容、题库、讲稿、课件和课堂进度均已保留，可以上传下一组材料。");
  }

  function restoreWorkspace(saved: CompletedWorkspace) {
    reset();
    setMaterial(saved.material);
    setMaterials(saved.materials);
    setMaterialCollection(saved.materialCollection);
    setMaterialProcessingJob(saved.materialProcessingJob ?? null);
    setPages(saved.pages);
    setContent(saved.content);
    setContentJob(saved.contentJob ?? null);
    setPresentationPlan(saved.presentationPlan);
    setPresentationPlanJob(saved.presentationPlanJob ?? null);
    setPptJob(saved.pptJob ?? null);
    setPresentationArtifact(saved.presentationArtifact);
    setPresentationSlideImages(normalizePresentationSlideImages(
      saved.presentationSlideImages,
      saved.presentationPlan?.mode,
    ));
    setSession(saved.session ? { ...saved.session, version: saved.session.version ?? 0 } : null);
    setClassroomPlanJob(saved.classroomPlanJob ?? null);
    setClassroomPlanIds(
      saved.classroomPlanIds ?? (
        saved.session ? { [saved.session.mode]: saved.session.plan_id } : {}
      ),
    );
    setAction(saved.action);
    setCurrentSlide(saved.currentSlide);
    setLearningMode(saved.learningMode);
    setStudentAgentTypes(saved.studentAgentTypes);
    setAgentTurn(saved.agentTurn);
    setFeedback(saved.feedback || "已重新打开保存的课堂项目。");
    setVideo(saved.video);
  }

  function toggleStudentAgent(type: StudentAgentType) {
    setStudentAgentTypes((current) =>
      current.includes(type) ? current.filter((item) => item !== type) : [...current, type],
    );
  }

  async function toggleAutoPlaying() {
    if (autoPlaying) {
      autoPlayingRef.current = false;
      if (narration.status !== "playing" && narration.status !== "loading") {
        playbackVersionRef.current += 1;
      }
      narration.pause();
      setAutoPlaying(false);
      return;
    }

    autoPlayingRef.current = true;
    setAutoPlaying(true);
    const interruptedCue = interruptedTeacherCueRef.current;
    if (interruptedCue) {
      interruptedTeacherCueRef.current = null;
      narration.unlock();
      playbackVersionRef.current += 1;
      const playbackVersion = playbackVersionRef.current;
      narratedStepRef.current = agentTurn?.turns.length
        ? `turn:${agentTurn.turns.map((turn) => `${turn.agent_id}:${turn.intent}:${turn.speech}`).join("|")}`
        : action
          ? `action:${action.id}`
          : interruptedCue.id;
      const transitionResult = await narration.play({
        id: `resume-after-user-question:${session?.id ?? "classroom"}:${Date.now()}`,
        text: "好的，我们现在重新回到刚刚没讲完的地方。",
        scope: "teacher_resume_after_user_question",
        refId: `${session?.id ?? "classroom"}:resume-after-user-question`,
        voice: "teacher",
        speaker: "芊芊老师",
        agentId: "teacher",
        role: "teacher",
      });
      if (transitionResult === "blocked" || transitionResult === "cancelled") return;
      const replayResult = await narration.play({
        ...interruptedCue,
        id: `${interruptedCue.id}:replay:${Date.now()}`,
      });
      if (replayResult === "blocked" || replayResult === "cancelled") return;
      if (replayResult === "failed") {
        setError("被打断的讲解语音暂时无法重播，课堂将继续推进");
      }
      if (
        session
        && autoPlayingRef.current
        && playbackVersion === playbackVersionRef.current
      ) {
        await autoStep(playbackVersion);
      }
      return;
    }
    if (narration.status === "paused") {
      await narration.resume();
      return;
    }
    narration.unlock();
    playbackVersionRef.current += 1;
    narratedStepRef.current = null;
    setPlaybackTrigger((value) => value + 1);
  }

  async function autoStep(playbackVersion = playbackVersionRef.current) {
    if (
      !session
      || autoStepInFlight.current
      || playbackVersion !== playbackVersionRef.current
    ) return;
    autoStepInFlight.current = true;
    setError(null);
    try {
      const result = await api.autoStep(session.id, session.version);
      setSession(result.session);
      setAction((currentAction) => {
        if (result.action) return result.action;
        if (result.status === "waiting" && result.session.waiting_for === "quiz_answer") {
          return currentAction;
        }
        return null;
      });
      setAgentTurn(result.directed_turn);
      if (result.directed_turn?.turns.length) {
        setFeedback("");
      } else if (result.feedback) {
        setFeedback(result.feedback);
      }
      if (result.status === "waiting" && result.session.waiting_for === "quiz_answer") {
        playbackVersionRef.current += 1;
        autoPlayingRef.current = false;
        setAutoPlaying(false);
        setFeedback("请先完成当前小测，提交后课堂会继续。");
      }
      if (result.status === "completed") {
        playbackVersionRef.current += 1;
        autoPlayingRef.current = false;
        setAutoPlaying(false);
        setFeedback(result.feedback ?? "本次课堂已经完成。");
      }
      if (
        result.status === "completed"
        || result.status === "waiting"
        || (!result.action && !result.directed_turn?.turns.length)
      ) {
        narration.clearCue();
      }
    } catch (caught) {
      if (playbackVersion !== playbackVersionRef.current) return;
      playbackVersionRef.current += 1;
      autoPlayingRef.current = false;
      setAutoPlaying(false);
      setError(caught instanceof Error ? caught.message : "自动课堂运行失败");
    } finally {
      autoStepInFlight.current = false;
    }
  }

  async function navigateClassroom(direction: "previous" | "next") {
    if (!session) return;
    playbackVersionRef.current += 1;
    autoPlayingRef.current = false;
    narration.stop();
    setAutoPlaying(false);
    const result = await run(direction === "next" ? "正在跳到下一段" : "正在返回上一段", () =>
      api.navigate(session.id, direction),
    );
    if (!result) return;
    setSession(result.session);
    if (result.page_action) displayPage(result.page_action);
    setAction(result.action);
    if (result.action?.type === "ASK_QUIZ") setQuizResult(null);
    setAgentTurn(null);
    narratedStepRef.current = null;
    setFeedback(result.feedback ?? "");
    if (result.action && result.session.status !== "completed") {
      autoPlayingRef.current = true;
      setAutoPlaying(true);
      setPlaybackTrigger((value) => value + 1);
    }
  }

  async function answer(selectedIndex: number) {
    if (!session || busy || session.waiting_for !== "quiz_answer") return;
    playbackVersionRef.current += 1;
    autoPlayingRef.current = false;
    narration.stop();
    setAutoPlaying(false);
    const result = await run("Evaluator 正在评估", () =>
      api.answer(session.id, selectedIndex, session.version)
    );
    if (!result) return;
    setSession(result.session);
    // Keep the quiz card projected while the teacher reads the evaluation.
    // The following autoStep replaces it only after feedback narration ends.
    setAction((currentAction) => currentAction?.type === "ASK_QUIZ" ? currentAction : null);
    if (action?.type === "ASK_QUIZ" && typeof result.correct === "boolean") {
      setQuizResult({
        actionId: action.id,
        selectedIndex,
        correct: result.correct,
      });
    }
    setAgentTurn(null);
    narratedStepRef.current = null;
    const feedbackText = result.feedback ?? "";
    setFeedback(feedbackText);
    let feedbackNarrationFinished = !feedbackText;
    if (feedbackText) {
      const narrationResult = await narration.play({
        id: `quiz-feedback:${session.id}:${Date.now()}`,
        text: feedbackText,
        scope: "quiz_feedback",
        refId: `${session.id}:quiz-feedback:${Date.now()}`,
        voice: "teacher",
        speaker: "芊芊老师",
        agentId: "teacher",
        role: "teacher",
      });
      if (narrationResult === "failed") {
        setError("小测反馈文字已显示，但老师语音暂时不可用");
      }
      feedbackNarrationFinished = narrationResult === "ended";
    }
    // Never turn the page while quiz feedback is still speaking. If playback is
    // blocked or fails, keep the class stopped so the learner can read the full
    // feedback and explicitly continue instead of silently skipping ahead.
    if (result.session.status !== "completed" && feedbackNarrationFinished) {
      playbackVersionRef.current += 1;
      const playbackVersion = playbackVersionRef.current;
      autoPlayingRef.current = true;
      await autoStep(playbackVersion);
      setAutoPlaying(autoPlayingRef.current);
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const submittedQuestion = question.trim();
    if (!session || !submittedQuestion || userQuestionBlockedByAgentExchange) return;
    playbackVersionRef.current += 1;
    autoPlayingRef.current = false;
    if (
      narration.cue?.role === "teacher"
      && ["playing", "loading", "paused"].includes(narration.status)
    ) {
      interruptedTeacherCueRef.current = narration.cue;
    } else {
      interruptedTeacherCueRef.current = null;
    }
    narration.stop();
    narration.unlock();
    setAutoPlaying(false);
    setQuestion("");
    setFeedback(`你：${submittedQuestion}`);

    // Start generating the teacher's answer immediately, then use that same wait
    // to read the learner's question aloud. This keeps the exchange conversational
    // without adding the question narration to the response latency.
    const answerRequest = run(answeringUserQuestionLabel, () =>
      api.ask(session.id, submittedQuestion, session.version),
    );
    const questionNarration = narration.play({
      id: `user-question:${session.id}:${Date.now()}`,
      text: submittedQuestion,
      scope: "user_question",
      refId: `${session.id}:user-question:${Date.now()}`,
      voice: "student_user",
      speaker: "你",
      agentId: "user",
      role: "student",
    });
    void questionNarration.then((questionNarrationResult) => {
      if (questionNarrationResult === "failed") {
        setError("问题已发送；语音不可用，已使用文字继续");
      }
    });

    const result = await answerRequest;
    if (!result) return;
    narration.stop();
    setSession(result.session);
    setAgentTurn(null);
    const answerText = result.feedback ?? "";
    setFeedback(answerText);
    if (answerText) {
      const narrationResult = await narration.play({
        id: `user-question-answer:${session.id}:${Date.now()}`,
        text: answerText,
        scope: "teacher_user_question_answer",
        refId: `${session.id}:user-question:${Date.now()}`,
        voice: "teacher",
        speaker: "芊芊老师",
        agentId: "teacher",
        role: "teacher",
      });
      if (narrationResult === "failed") {
        setError("老师的文字回答已显示；语音不可用，课堂将继续推进");
      }
    }
    interruptedTeacherCueRef.current = null;
    if (result.session.status !== "completed") {
      playbackVersionRef.current += 1;
      const playbackVersion = playbackVersionRef.current;
      autoPlayingRef.current = true;
      setAutoPlaying(true);
      await autoStep(playbackVersion);
      setAutoPlaying(autoPlayingRef.current);
    }
  }

  async function createVideo() {
    if (!content || !presentationArtifact) return;
    const result = await run("正在逐页合成 PPT 讲解视频", () =>
      api.createVideo(content.id, presentationArtifact.id),
    );
    if (result) setVideo(result);
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    if (material) return;
    chooseFiles(Array.from(event.dataTransfer.files));
  }

  const actionLabel = action?.type.replaceAll("_", " ") ?? "WAITING";
  const loadingProgress = materialProcessingJob && busy === "正在上传并解析材料"
    ? materialProcessingJob.progress
    : contentJob && busy === "正在组织学习内容"
      ? contentJob.progress
    : presentationPlanJob && busy === "正在生成 PPT 并布置课堂"
      ? classroomPlanJob
        ? classroomPlanJob.progress
        : pptJob
          ? 75 + Math.round(pptJob.progress * 25)
          : presentationPlanJob.progress
      : null;
  const loadingLabel = materialProcessingJob && busy === "正在上传并解析材料"
    ? materialProcessingJob.message
    : contentJob && busy === "正在组织学习内容"
      ? contentProgressLabel(contentJob)
    : presentationPlanJob && busy === "正在生成 PPT 并布置课堂"
      ? classroomPlanJob
        ? classroomPlanProgressLabel(classroomPlanJob)
        : pptJob
          ? "正在导出 PPTX"
          : presentationProgressLabel(presentationPlanJob)
      : busy;
  const loadingSteps = busy === "正在上传并解析材料"
    ? ["接收上传文件", "建立材料记录", "解析页面内容", "渲染页面预览", "保存解析结果"]
    : busy === "正在生成 PPT 并布置课堂"
    ? ["理解课程内容", "规划课件页面", "设计课堂版式", "渲染并导出 PPT", "布置互动课堂"]
    : busy === "正在组织学习内容"
      ? ["读取课程材料", "逐页理解内容", "提取知识单元", "组织课堂结构", "保存备课结果"]
      : ["接收课堂任务", "检查课程材料", "执行当前操作", "保存处理结果"];
  const loadingStepIndex = loadingProgress === null
    ? 0
    : Math.min(loadingSteps.length - 1, Math.floor((loadingProgress / 100) * loadingSteps.length));

  if (activePage === "library") {
    return (
      <div className="classroom-app" data-theme={theme}>
        <LibraryPage
          onBack={() => setActivePage("classroom")}
          onUseMaterial={useLibraryMaterial}
          onOpenAsset={openLibraryAsset}
        />
      </div>
    );
  }

  return (
    <div className="classroom-app" data-theme={theme}>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="MetaClass 首页">
          <span className="brand-seal">M</span>
          <span><b>MetaClass</b><small>AI CLASSROOM STUDIO</small></span>
        </a>
        <div className="room-title">
          <div className="room-title-copy">
            <span>ROOM 01</span>
            <b title={content?.title ?? "新课堂准备室"}>{content?.title ?? "新课堂准备室"}</b>
          </div>
          <div className="course-progress" aria-label={`课程进度：${activeStage + 1}/${stages.length}，${stages[activeStage]}`}>
            <span>{String(activeStage + 1).padStart(2, "0")} / {String(stages.length).padStart(2, "0")}</span>
            <b>{stages[activeStage]}</b>
            <ol aria-hidden="true">
              {stages.map((stage, index) => <li className={index <= activeStage ? "done" : ""} key={stage} />)}
            </ol>
          </div>
        </div>
        <div className="system-live">
          <button className="library-nav-button" type="button" onClick={() => setActivePage("library")}>
            <span className="library-nav-icon" aria-hidden="true">▤</span>
            <span className="library-nav-copy"><b>历史资料库</b><small>OPEN ARCHIVE</small></span>
            <span className="library-nav-arrow" aria-hidden="true">→</span>
          </button>
          <button
            className="theme-toggle"
            type="button"
            role="switch"
            aria-checked={theme === "light"}
            aria-label={`切换到${theme === "dark" ? "浅色" : "深色"}模式`}
            title={`切换到${theme === "dark" ? "浅色" : "深色"}模式`}
            onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
          >
            <span className="theme-icon moon" aria-hidden="true">◐</span>
            <span className="theme-toggle-track"><i /></span>
            <span className="theme-icon sun" aria-hidden="true">☀</span>
          </button>
          <span className="online-status"><i /> LOCAL SYSTEM ONLINE</span>
        </div>
      </header>

      <main id="top" className="classroom-layout">
        <aside className="left-console">
          <section className="material-dock">
            <div className="section-caption"><span>课程材料</span><small>PDF / PPTX</small></div>
            <label
              className={`compact-drop ${dragging ? "dragging" : ""}`}
              onDragEnter={() => setDragging(true)}
              onDragLeave={() => setDragging(false)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={onDrop}
            >
              <input type="file" accept=".pdf,.pptx" multiple disabled={!!material} onChange={(event: ChangeEvent<HTMLInputElement>) => chooseFiles(Array.from(event.target.files ?? []))} />
              <span className="upload-icon">↥</span>
              <div>{files.length ? <><b>{selectedFilesLabel}</b><small>{files.length} 个文件 · {formatBytes(selectedFilesSize)}</small></> : <><b>把课件放到讲台</b><small>拖拽或点击选择文件</small></>}</div>
            </label>
            {!material ? (
              <button className="control-button warm" disabled={!files.length || !!busy} onClick={upload}>上传并解析 <span>→</span></button>
            ) : (
              <div className="material-ticket">
                <div><span>FILE</span><b>{materials.length > 1 ? `${material.filename} 等 ${materials.length} 个文件` : material.filename}</b></div>
                <div><span>STATUS</span><b className="success">● 已解析 · {pages.length} 页</b></div>
              </div>
            )}
            {material && !pages.length && <button className="control-button warm" disabled={!!busy} onClick={parse}>重新解析</button>}
            {(material || files.length > 0) && !busy && (
              <button className="terminate-material-button" type="button" onClick={terminateCurrentMaterial}>
                <span>{material ? "终止当前材料" : "取消当前选择"}</span>
                <small>{material ? "保留资料库记录 · 返回上传入口" : "重新选择其他文件"}</small>
                <b>×</b>
              </button>
            )}
            {completedWorkspaces.length > 0 && (
              <div className="saved-classrooms">
                <span>已完成课堂</span>
                {completedWorkspaces.map((saved) => (
                  <button key={saved.id} disabled={!!busy} onClick={() => restoreWorkspace(saved)}>
                    <b>{saved.title}</b>
                    <small>{new Date(saved.completedAt).toLocaleString()}</small>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="quick-actions">
            <div className="section-caption"><span>备课控制</span><small>ACTIONS</small></div>
            <div className="mode-switch" aria-label="选择学习方式">
              <button
                className={learningMode === "lecture" ? "selected" : ""}
                disabled={!!busy || userQuestionBlockedByAgentExchange}
                onClick={() => switchClassroomMode("lecture")}
              >
                <span>A</span>
                <b>连续讲解</b>
                <small>老师按 PPT 一页页讲，不安排同学插嘴</small>
              </button>
              <button
                className={learningMode === "interactive" ? "selected" : ""}
                disabled={!!busy || userQuestionBlockedByAgentExchange}
                onClick={() => switchClassroomMode("interactive")}
              >
                <span>B</span>
                <b>互动课堂</b>
                <small>允许 agent 同学提问、总结和插话</small>
              </button>
            </div>
            {pages.length > 0 && !content && !presentationPlan && (
              <div className="presentation-route-picker">
                <div className="presentation-route-heading">
                  <span>01 · 先选择课件制作路线</span>
                  <small>这个选择会贯穿 LearningContent、课件计划和课堂创建</small>
                </div>
                <div className="mode-switch" aria-label="选择 PPT 使用方式">
                  <button
                    className={presentationMode === "source_deck" ? "selected" : ""}
                    disabled={!!busy}
                    onClick={() => setPresentationMode("source_deck")}
                  >
                    <span>A</span>
                    <b>使用原稿讲解</b>
                    <small>保留原 PPT / PDF 的每一页，进入原稿讲解链路</small>
                  </button>
                  <button
                    className={presentationMode === "generated" ? "selected" : ""}
                    disabled={!!busy}
                    onClick={() => setPresentationMode("generated")}
                  >
                    <span>B</span>
                    <b>重新生成一套 PPT</b>
                    <small>沿用原链路，根据 LearningContent 重新规划并生成课件</small>
                  </button>
                </div>
              </div>
            )}
            <button disabled={!pages.length || !!content || !!busy} onClick={buildContent}><span>02</span><b>{content ? "内容已构建" : presentationMode === "source_deck" ? "构建原稿讲解内容" : "构建重新生成 PPT 的内容"}</b><i>↗</i></button>
            {content && !presentationPlan && (
              <div className="presentation-route-picker presentation-route-confirmed">
                <div className="presentation-route-heading">
                  <span>当前路线</span>
                  <small>{presentationMode === "source_deck" ? "使用原稿讲解" : "重新生成一套 PPT"}</small>
                </div>
                <button className="route-reselect-button" type="button" disabled={!!busy} onClick={chooseAnotherPresentationRoute}>
                  返回上一步重新选择路线
                </button>
              </div>
            )}
            {content && !presentationPlan && presentationMode !== "source_deck" && <div className="ppt-theme-selector">
              <div className="ppt-theme-heading">
                <span>PPT 主题</span>
                <small>只改变视觉，不改页面内容</small>
              </div>
              <div className="ppt-theme-grid" role="radiogroup" aria-label="选择 PPT 视觉主题">
                {pptThemes.map((option) => (
                  <button
                    type="button"
                    role="radio"
                    aria-checked={pptThemeId === option.id}
                    className={pptThemeId === option.id ? "selected" : ""}
                    disabled={!!busy || !!session}
                    key={option.id}
                    onClick={() => setPptThemeId(option.id)}
                    title={option.description}
                  >
                    <span className="ppt-theme-preview" aria-hidden="true">
                      <i style={{ backgroundColor: `#${option.colors.cover}` }} />
                      <i style={{ backgroundColor: `#${option.colors.background}` }} />
                      <i style={{ backgroundColor: `#${option.colors.accent}` }} />
                    </span>
                    <b>{option.name}</b>
                    <small>{option.description}</small>
                    <em aria-hidden="true">{pptThemeId === option.id ? "✓" : ""}</em>
                  </button>
                ))}
              </div>
            </div>}
            <button disabled={!content || !!presentationPlan || !!busy} onClick={preparePresentationPlan}><span>03</span><b>{presentationPlan ? "课件讲解计划已准备" : presentationMode === "source_deck" ? "直接使用原稿并生成逐页讲稿" : "重新设计并生成 PPT 计划"}</b><i>↗</i></button>
            {presentationPlan && !session && (
              <button disabled={!!busy} onClick={chooseAnotherPresentationRoute}><span>↺</span><b>重新选择课件使用方式</b><i>→</i></button>
            )}
            <button disabled={!presentationPlan || presentationPlan.mode === "source_deck" || !!presentationArtifact || !!busy} onClick={generatePresentationArtifact}><span>03</span><b>{presentationPlan?.mode === "source_deck" ? "使用原 PPT" : presentationArtifact ? "PPT 已生成" : "生成 PPT（创建课堂前必需）"}</b><i>↗</i></button>
            <button disabled={!content || !presentationPlan || (presentationPlan.mode === "generated" && !presentationArtifact) || !!session || !!busy} onClick={startClassroom}><span>04</span><b>{session ? "课堂已创建" : classroomPlanJob && classroomPlanJob.status !== "failed" ? "继续进入已生成课堂" : learningMode === "interactive" ? "创建互动课堂" : "创建连续课堂"}</b><i>↗</i></button>
            <button disabled={!content || !presentationArtifact || !!video || !!busy} onClick={createVideo}><span>05</span><b>{video ? "视频已生成" : "合成讲解视频"}</b><i>↗</i></button>
            <button disabled={!content || !presentationPlan || !!busy} onClick={completeWorkspace}><span>✓</span><b>完成当前材料</b><i>→</i></button>
            {presentationPlan && (
              <details className="speaker-script-editor">
                <summary>编辑逐页讲稿</summary>
                <button
                  className="open-full-page-button"
                  type="button"
                  onClick={() => {
                    const firstSlide = presentationPlan.slides[0];
                    if (!editingSlideId && firstSlide) {
                      setEditingSlideId(firstSlide.id);
                      setEditingScript(firstSlide.speaker_script);
                    }
                    setFullPageView("scripts");
                  }}
                >
                  全屏编辑讲稿 ↗
                </button>
                <select
                  value={editingSlideId ?? ""}
                  onChange={(event) => {
                    const slide = presentationPlan.slides.find(
                      (item) => item.id === event.target.value,
                    );
                    setEditingSlideId(slide?.id ?? null);
                    setEditingScript(slide?.speaker_script ?? "");
                  }}
                >
                  <option value="">选择页面</option>
                  {presentationPlan.slides.map((slide) => (
                    <option value={slide.id} key={slide.id}>
                      {slide.order}. {slide.title}
                    </option>
                  ))}
                </select>
                <textarea
                  value={editingScript}
                  disabled={!editingSlideId}
                  onChange={(event) => setEditingScript(event.target.value)}
                  rows={8}
                />
                <button
                  disabled={!editingSlideId || !editingScript.trim() || !!busy}
                  onClick={saveSpeakerScript}
                >
                  保存本页讲稿
                </button>
              </details>
            )}
          </section>
        </aside>

        <section className="teaching-studio">
          <div className="studio-ceiling"><i /><i /><i /><span>METACLASS · SMART TEACHING WALL</span><i /><i /><i /></div>
          <div className="blackboard">
            <div className="board-meta"><span><i /> {session ? "SESSION LIVE" : "CLASSROOM STANDBY"}</span><b>{actionLabel}</b><small>{session?.id ?? "等待创建课堂"}</small></div>
            <div className={`board-stage ${captionText ? "speaking" : ""}`}>
              <div className="projection-screen">
                {session ? (
                  <ActionView
                    action={action}
                    answerDisabled={!!busy || session.waiting_for !== "quiz_answer"}
                    quizResult={quizResult}
                    presentationSlideImages={presentationSlideImages}
                    currentSlide={currentSlide}
                    slideProgress={currentLessonProgress}
                    onAnswer={answer}
                  />
                ) : pages.length ? (
                  <SlideNarrationPlayer content={content} materialId={material?.id} pages={pages} />
                ) : (
                  <div className="empty-classroom">
                    <div className="room-emblem">M</div>
                    <small>READ · PLAN · RUN</small>
                    <h1>让课件走上讲台，<br />变成一堂真正的课。</h1>
                    <p>从左侧导入 PDF 或 PPTX，页面、讲解、小测与来源引用都会在这里展开。</p>
                  </div>
                )}
              </div>
              {captionText && (
                <aside className={`live-speaker ${isUserNarration ? "user-voice" : ""}`} aria-live="polite">
                  {!isUserNarration && (
                    <div className="live-speaker-avatar"><img src={captionAvatar} alt="" /></div>
                  )}
                  <div className="speech-bubble">
                    <span>
                      {captionSpeaker}
                      {narration.cue && (
                        <em>
                          {narration.cue.scope === "user_question"
                            ? narration.status === "loading"
                              ? "正在准备问题语音"
                              : narration.status === "idle"
                                ? "老师正在组织回答"
                                : "正在朗读你的问题"
                            : narration.status === "loading"
                              ? "正在生成语音"
                              : "语音同步中"}
                        </em>
                      )}
                    </span>
                    <p ref={speechTextRef} tabIndex={0} aria-label={`${captionSpeaker}的发言`}>{captionText}</p>
                    <button
                      onClick={() => {
                        if (narration.cue) {
                          playbackVersionRef.current += 1;
                          autoPlayingRef.current = false;
                          narration.stop();
                          narratedStepRef.current = null;
                          setAutoPlaying(false);
                        }
                        setFeedback("");
                      }}
                      aria-label="关闭发言气泡"
                    >×</button>
                  </div>
                </aside>
              )}
              {!captionText && <div className="live-speaker-slot" aria-hidden="true" />}
            </div>
            <div className="board-tray"><span /><span /><i>MC</i><span /><span /></div>
          </div>

          <div className="teacher-desk">
            <div className="desk-status"><span>{session?.mode === "interactive" ? "AGENT CLASS" : "LECTURE MODE"}</span><b>{session?.status === "completed" ? "课堂已结束" : session ? "课堂进行中" : "等待课堂"}</b></div>
            <div className="desk-actions">
              <button
                className="next-button"
                disabled={!session || !!busy}
                onClick={session?.status === "completed" ? replayClassroom : toggleAutoPlaying}
              >
                {session?.status === "completed" ? "重新播放课堂" : autoPlaying ? "暂停自动课堂" : "开始自动课堂"} <span>{autoPlaying ? "Ⅱ" : "▶"}</span>
              </button>
              <button className="next-button secondary" disabled={!session || !!busy} onClick={() => navigateClassroom("previous")}><span>←</span> 上一页</button>
              <button className="next-button secondary" disabled={!session || !!busy || session.waiting_for === "quiz_answer" || session.status === "completed"} onClick={() => navigateClassroom("next")}>下一页 <span>→</span></button>
            </div>
            <form onSubmit={ask}>
              <label htmlFor="student-question">用户提问</label>
              <input
                id="student-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                disabled={!session || userQuestionBlockedByAgentExchange}
                placeholder={userQuestionBlockedByAgentExchange ? "请等待当前师生问答结束…" : "输入关于当前内容的问题…"}
              />
              <button disabled={!question.trim() || !session || !!busy || userQuestionBlockedByAgentExchange}>发送</button>
            </form>
          </div>

          {learningMode === "interactive" && !session && (
            <section
              className="student-agent-selector student-agent-bench"
              onMouseLeave={() => setHoveredStudentAgentType(null)}
            >
              <div className="section-caption">
                <span>课堂同学</span>
                <small>SELECTED {studentAgentTypes.length} / 8</small>
              </div>
              <div className="student-agent-options">
                {studentAgentChoices.map((agent) => {
                  const selected = studentAgentTypes.includes(agent.type);
                  return (
                    <button
                      type="button"
                      className={selected ? "selected" : ""}
                      key={agent.type}
                      onClick={() => toggleStudentAgent(agent.type)}
                      onMouseEnter={() => setHoveredStudentAgentType(agent.type)}
                      onFocus={() => setHoveredStudentAgentType(agent.type)}
                      onBlur={() => setHoveredStudentAgentType(null)}
                      aria-pressed={selected}
                    >
                      <span className="student-agent-avatar"><img src={agent.avatar} alt="" /></span>
                      <span><b>{agent.name}</b><small>{agent.description}</small></span>
                      <i>{selected ? "✓" : "+"}</i>
                    </button>
                  );
                })}
              </div>
              {hoveredStudentAgent && (
                <aside className="student-agent-profile" aria-live="polite">
                  <div className="student-agent-profile-head"><span>同学档案</span><small>{hoveredStudentAgent.gender}</small></div>
                  <img src={hoveredStudentAgent.avatar} alt={`${hoveredStudentAgent.studentName}的头像`} />
                  <div><b>{hoveredStudentAgent.studentName}</b><small>{hoveredStudentAgent.name}</small></div>
                  <p>{hoveredStudentAgent.profile}</p>
                </aside>
              )}
            </section>
          )}

          {session?.mode === "interactive" && (
            <section className="classroom-roster classroom-roster-bench">
              <div className="section-caption"><span>本堂同学</span><small>{session.student_states.length} AGENTS</small></div>
              <div className="classroom-roster-list">
                {session.student_states.map((student) => {
                  const agent = studentAgentChoices.find((item) => item.type === student.agent_type);
                  return (
                    <div className="classroom-roster-item" key={student.id}>
                      {agent && <img src={agent.avatar} alt="" />}
                      <span><b>{student.display_name}</b><small>{agent?.name ?? "课堂学生智能体"}</small></span>
                      <i>{student.last_intent ? "·" : "○"}</i>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </section>

        <aside className="right-board">
          <section className="lesson-outline">
            <div className="section-caption"><span>今日课表</span><small>LESSON PLAN</small></div>
            {content ? <>
              <h2>{content.title}</h2>
              <div className="objective-tags">{content.objectives.map((item) => <span key={item}>{item}</span>)}</div>
              <div className="content-view-tabs" role="tablist" aria-label="学习内容查看方式">
                <button className={contentView === "outline" ? "active" : ""} onClick={() => setContentView("outline")}>大纲</button>
                <button className={contentView === "tree" ? "active" : ""} onClick={() => setContentView("tree")}>知识树</button>
                <button className={contentView === "quality" ? "active" : ""} onClick={() => setContentView("quality")}>质量</button>
              </div>
              <button
                className="outline-full-page-button"
                type="button"
                onClick={() => setFullPageView(contentView === "tree" ? "tree" : "outline")}
              >
                {contentView === "tree" ? "展开完整知识树" : "全屏查看课程大纲"} ↗
              </button>
              {contentView === "outline" && (
                <div className="outline-section-list">
                  {content.sections.map((section, index) => (
                    <OutlineSection key={section.id} section={section} index={index} />
                  ))}
                </div>
              )}
              {contentView === "tree" && (
                <ol className="knowledge-tree-list">
                  {knowledgeTreeModel.rootNodes.length ? knowledgeTreeModel.rootNodes.map((node, index) => (
                    <KnowledgeTreeBranch
                      key={node.id}
                      node={node}
                      indexPath={String(index + 1)}
                      depth={0}
                      childrenByParent={knowledgeTreeModel.childrenByParent}
                      nodeById={knowledgeTreeModel.nodeById}
                      knowledgeUnitsById={knowledgeTreeModel.knowledgeUnitsById}
                    />
                  )) : <li className="knowledge-tree-empty">暂未生成知识树结构</li>}
                </ol>
              )}
              {contentView === "quality" && (
                <div className="content-quality">
                  <div><span>覆盖率</span><b>{coverageScore === null ? "--" : `${coverageScore}%`}</b></div>
                  <div><span>知识单元</span><b>{content.knowledge_units?.length ?? 0}</b></div>
                  <div><span>教学章节</span><b>{content.sections.length}</b></div>
                  {qualityWarnings.length ? <ul>{qualityWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : <p>未发现需要人工复核的问题。</p>}
                </div>
              )}
            </> : <div className="rail-empty"><span>⌁</span><p>构建 LearningContent 后，这里会出现完整课表。</p></div>}
          </section>

          <section className="mastery-panel">
            <div className="section-caption"><span>课堂观察</span><small>ESTIMATED</small></div>
            <div className="mastery-title"><b>估计掌握度</b><small>基于课堂小测证据</small></div>
            {session?.mastery.length ? session.mastery.map((item) => (
              <div className="mastery-row" key={item.knowledge_point}>
                <div><b>{item.knowledge_point}</b><strong>{Math.round((item.value ?? 0) * 100)}%</strong></div>
                <span><i style={{ width: `${(item.value ?? 0) * 100}%` }} /></span>
              </div>
            )) : <div className="no-evidence"><span>—</span><p>完成小测后显示<br />不代表精确能力测量</p></div>}
          </section>

          <section className={`video-status ${video ? "ready" : ""}`}>
            <span className="video-glyph">▶</span>
            <div><small>LECTURE REPLAY</small><b>{video ? "讲解视频已就绪" : "课后讲解视频"}</b><p>{video ? "MP4 与字幕已保存在本地" : "PPT 逐页画面 + 教师配音 + SRT 字幕"}</p></div>
            {video && <>
              <video controls src={api.videoDownload(video.id)} />
              <a href={api.videoDownload(video.id)}>下载 MP4 ↗</a>
            </>}
          </section>
          {presentationArtifact && (
            <section className="video-status ready">
              <span className="video-glyph">▣</span>
              <div>
                <small>PRESENTATION</small>
                <b>生成 PPT 已就绪</b>
                <p>{presentationArtifact.slide_images.length} 页已渲染为课堂图片</p>
              </div>
              <a href={api.pptDownload(presentationArtifact.id)}>下载 PPTX ↗</a>
            </section>
          )}
        </aside>
      </main>

      {selectedKnowledgeTreeNode && (
        <div
          className="knowledge-tree-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="knowledge-tree-modal-title"
          onClick={() => setSelectedKnowledgeTreeNodeId(null)}
        >
          <section className="knowledge-tree-dialog" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <small>KNOWLEDGE OUTLINE</small>
                <h2 id="knowledge-tree-modal-title">{selectedKnowledgeTreeNode.title}</h2>
              </div>
              <button
                type="button"
                aria-label="关闭知识树详情"
                onClick={() => setSelectedKnowledgeTreeNodeId(null)}
              >
                ×
              </button>
            </header>
            <ol className="knowledge-tree-list knowledge-tree-full">
              <KnowledgeTreeBranch
                node={selectedKnowledgeTreeNode}
                indexPath={String(selectedKnowledgeTreeIndex + 1).padStart(2, "0")}
                depth={0}
                childrenByParent={knowledgeTreeModel.childrenByParent}
                nodeById={knowledgeTreeModel.nodeById}
                knowledgeUnitsById={knowledgeTreeModel.knowledgeUnitsById}
              />
            </ol>
          </section>
        </div>
      )}

      {fullPageView && (
        <div
          className="curriculum-workspace-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="curriculum-workspace-title"
          onClick={() => setFullPageView(null)}
        >
          <section className={`curriculum-workspace curriculum-workspace-${fullPageView}`} onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <small>METACLASS · FULL PAGE VIEW</small>
                <h2 id="curriculum-workspace-title">
                  {fullPageView === "tree" ? "课程知识树" : fullPageView === "outline" ? "课程教学大纲" : "逐页讲稿工作台"}
                </h2>
                <p>{content?.title ?? presentationPlan?.title}</p>
              </div>
              <button type="button" aria-label="关闭全屏视图" onClick={() => setFullPageView(null)}>×</button>
            </header>

            {fullPageView === "outline" && content && (
              <div className="full-outline-grid">
                {content.sections.map((section, index) => (
                  <OutlineSection key={section.id} section={section} index={index} full />
                ))}
              </div>
            )}

            {fullPageView === "tree" && content && (
              <div className="full-tree-canvas">
                <div className="tree-course-root"><small>COURSE ROOT</small><b>{content.title}</b><span>{content.knowledge_units?.length ?? 0} 个知识单元</span></div>
                <div className="tree-forest">
                  {knowledgeTreeModel.rootNodes.length ? knowledgeTreeModel.rootNodes.map((node, index) => (
                    <ol className="knowledge-tree-list knowledge-tree-full" key={node.id}>
                      <KnowledgeTreeBranch
                        node={node}
                        indexPath={String(index + 1).padStart(2, "0")}
                        depth={0}
                        childrenByParent={knowledgeTreeModel.childrenByParent}
                        nodeById={knowledgeTreeModel.nodeById}
                        knowledgeUnitsById={knowledgeTreeModel.knowledgeUnitsById}
                      />
                    </ol>
                  )) : <div className="knowledge-tree-empty">暂未生成知识树结构</div>}
                </div>
              </div>
            )}

            {fullPageView === "scripts" && presentationPlan && (
              <div className="script-workbench">
                <nav aria-label="讲稿页面">
                  {presentationPlan.slides.map((slide) => (
                    <button
                      type="button"
                      className={editingSlideId === slide.id ? "active" : ""}
                      key={slide.id}
                      onClick={() => {
                        setEditingSlideId(slide.id);
                        setEditingScript(slide.speaker_script);
                      }}
                    >
                      <span>{String(slide.order).padStart(2, "0")}</span>
                      <div><b>{slide.title}</b><small>{slide.source_page_no ? `原稿第 ${slide.source_page_no} 页` : "生成页面"}</small></div>
                    </button>
                  ))}
                </nav>
                <main>
                  {presentationPlan.slides.find((slide) => slide.id === editingSlideId) ? <>
                    <div className="script-editor-heading">
                      <div><small>SPEAKER SCRIPT</small><h3>{presentationPlan.slides.find((slide) => slide.id === editingSlideId)?.title}</h3></div>
                      <span>{editingScript.length} 字</span>
                    </div>
                    <textarea
                      value={editingScript}
                      onChange={(event) => setEditingScript(event.target.value)}
                      aria-label="逐页讲稿编辑区"
                    />
                    <footer>
                      <small>保存后，新创建的课堂会使用这版讲稿。</small>
                      <button disabled={!editingScript.trim() || !!busy} onClick={saveSpeakerScript}>保存本页讲稿</button>
                    </footer>
                  </> : <div className="script-empty">请选择一页开始编辑</div>}
                </main>
              </div>
            )}
          </section>
        </div>
      )}

      {(materialProcessingJob?.status === "paused" || contentJob?.status === "paused" || presentationPlanJob?.status === "paused" || pptJob?.status === "paused") && !busy && (
        <div className="paused-job-toast" role="status">
          <span>Ⅱ</span>
          <div><b>进度已保存</b><p>{materialProcessingJob?.status === "paused" ? `${materialProcessingJob.message} · ${materialProcessingJob.progress}%` : contentJob?.status === "paused" ? contentProgressLabel(contentJob) : presentationPlanJob?.status === "paused" ? presentationProgressLabel(presentationPlanJob) : "PPT 生成已暂停"}</p></div>
          <button className="resume" type="button" onClick={resumePausedContentWork}>从中断处继续</button>
          <button className="discard" type="button" onClick={discardPausedContentWork}>放弃并删除临时进度</button>
        </div>
      )}
      {error && <div className="error-toast" role="alert"><span>!</span><div><b>流程暂停</b><p>{error}</p></div><button onClick={() => setError(null)}>×</button></div>}
      {busy && busy !== answeringUserQuestionLabel && (
        <div className="busy-overlay" aria-live="polite">
          <section className="loading-board">
            <header><span>METACLASS · LESSON PREP</span><b>正在准备这堂课</b></header>
            <ol>
              {loadingSteps.map((step, index) => (
                <li className={index < loadingStepIndex ? "done" : index === loadingStepIndex ? "active" : ""} key={step}>
                  <i>{index < loadingStepIndex ? "✓" : index === loadingStepIndex ? "•" : "○"}</i>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
            <div className="loading-current"><span className="writing-mark" /> <b>{loadingLabel}</b></div>
            {((materialProcessingJob && busy === "正在上传并解析材料") || ["queued", "running"].includes(contentJob?.status ?? "") || ["queued", "running"].includes(presentationPlanJob?.status ?? "") || ["queued", "running", "waiting_for_skill"].includes(pptJob?.status ?? "")) && (
              <button className="cancel-processing-button" type="button" onClick={materialProcessingJob && busy === "正在上传并解析材料" ? pauseMaterialProcessing : pauseCurrentContentWork}>
                <span>停止并保存进度</span><b>Ⅱ</b>
              </button>
            )}
            <footer>
              <div
                className={`generation-progress ${loadingProgress === null ? "indeterminate" : ""}`}
                role="progressbar"
                aria-label="课堂准备进度"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={loadingProgress ?? undefined}
              ><i style={loadingProgress === null ? undefined : { width: `${loadingProgress}%` }} /></div>
              <small>{loadingProgress === null ? "正在整理讲台，请不要关闭课堂" : `${loadingProgress}% · 任务可在后台继续运行`}</small>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
