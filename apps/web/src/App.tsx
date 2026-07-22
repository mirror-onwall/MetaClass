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
import { SlideNarrationPlayer } from "./features/video/SlideNarrationPlayer";
import {
  type NarrationCue,
  useTTSNarration,
} from "./features/video/useTTSNarration";
import atmosphereAvatar from "./assets/agents/atmosphere-regulator.png";
import conceptConfusedAvatar from "./assets/agents/concept-confused.png";
import deepThinkerAvatar from "./assets/agents/deep-thinker.png";
import foundationWeakAvatar from "./assets/agents/foundation-weak.png";
import noteTakerAvatar from "./assets/agents/note-taker.png";
import practicalApplierAvatar from "./assets/agents/practical-applier.png";
import researcherAvatar from "./assets/agents/researcher.png";
import silentObserverAvatar from "./assets/agents/silent-observer.png";
import teacherQianqianAvatar from "./assets/agents/teacher-qianqian.png";
import { api } from "./shared/api";
import { formatBytes } from "./shared/format";
import type {
  ClassroomSession,
  ClassroomPlanJob,
  ContentGenerationJob,
  CourseKnowledgeTreeNode,
  DirectedAgentTurn,
  LearningContent,
  LearningMode,
  Material,
  MaterialCollection,
  PageMetadata,
  PPTArtifact,
  PPTGenerationJob,
  PPTThemeOption,
  PresentationPlan,
  PresentationPlanJob,
  StudentAgentType,
  TeachingAction,
  VideoResult,
} from "./shared/types";

const stages = ["导入材料", "页面解析", "组织内容", "互动课堂", "讲解视频"];
const answeringUserQuestionLabel = "老师正在组织回答";

const defaultPptTheme: PPTThemeOption = {
  id: "academic_blue",
  name: "学术蓝白",
  description: "清晰、克制，适合课程讲解与研究汇报",
  style_direction: "restrained academic editorial",
  colors: {
    cover: "12365A",
    background: "FFFFFF",
    text: "17324D",
    accent: "2E75B6",
    soft: "DCEBFA",
    secondary: "4F8FCB",
  },
};

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

const studentAgentChoices: Array<{
  type: StudentAgentType;
  name: string;
  description: string;
  studentName: string;
  gender: string;
  profile: string;
  avatar: string;
}> = [
  {
    type: "classroom_atmosphere_regulator",
    name: "课堂气氛调节者",
    description: "活跃氛围，用类比打开话题",
    studentName: "凡凡",
    gender: "男",
    profile: "善于把抽象概念换成生活里的小例子，也会鼓励不敢发言的同学加入讨论。",
    avatar: atmosphereAvatar,
  },
  {
    type: "deep_thinker",
    name: "深度思考者",
    description: "追问原因、边界与反例",
    studentName: "浩浩",
    gender: "男",
    profile: "习惯从前提、条件和反例出发追问，喜欢把一个结论推到更深的边界处。",
    avatar: deepThinkerAvatar,
  },
  {
    type: "note_taker",
    name: "课堂笔记员",
    description: "提炼重点，整理可复习笔记",
    studentName: "婧婧",
    gender: "女",
    profile: "会把讲解整理为定义、例子和易错点三类笔记，擅长在阶段结束时复述重点。",
    avatar: noteTakerAvatar,
  },
  {
    type: "researcher",
    name: "研究型同学",
    description: "连接应用场景与研究方法",
    studentName: "涵涵",
    gender: "女",
    profile: "对研究问题和方法格外敏感，常会把当前知识点连接到实验设计和真实研究场景。",
    avatar: researcherAvatar,
  },
  {
    type: "foundation_weak",
    name: "基础薄弱型同学",
    description: "提出基础问题，帮助发现学习门槛",
    studentName: "琪琪",
    gender: "男",
    profile: "愿意直接说出没听懂的地方，容易卡在前置概念，需要清晰的分步解释和小例子。",
    avatar: foundationWeakAvatar,
  },
  {
    type: "silent_observer",
    name: "沉默观察型同学",
    description: "低频发言，在关键处表达困惑",
    studentName: "跳跳",
    gender: "女",
    profile: "平时安静地观察课堂节奏，通常在被邀请或出现关键困惑时，给出简短但真实的反馈。",
    avatar: silentObserverAvatar,
  },
  {
    type: "concept_confused",
    name: "概念混淆型同学",
    description: "暴露典型误解，触发辨析讲解",
    studentName: "昊昊",
    gender: "男",
    profile: "容易把相近概念放在一起理解，但正好能暴露典型误解，推动老师进行对比辨析。",
    avatar: conceptConfusedAvatar,
  },
  {
    type: "practical_applier",
    name: "实践应用型同学",
    description: "关注怎么用、在哪里用",
    studentName: "包包",
    gender: "女",
    profile: "最关心知识怎样落到真实任务里，会追问具体做法、使用条件和可操作的步骤。",
    avatar: practicalApplierAvatar,
  },
];

const defaultStudentAgentTypes: StudentAgentType[] = studentAgentChoices.map(
  (student) => student.type,
);

const knowledgeTreeRoleLabels: Record<string, string> = {
  foundation: "基础概念",
  concept: "核心概念",
  method: "方法步骤",
  process: "过程机制",
  application: "应用场景",
  example: "案例说明",
  assessment: "检测评价",
  extension: "拓展关联",
};

function formatKnowledgeTreeRole(role: string) {
  return knowledgeTreeRoleLabels[role] ?? role;
}

type KnowledgeTreeBranchProps = {
  node: CourseKnowledgeTreeNode;
  indexPath: string;
  depth: number;
  childrenByParent: Map<string, CourseKnowledgeTreeNode[]>;
  nodeById: Map<string, CourseKnowledgeTreeNode>;
};

function KnowledgeTreeBranch({
  node,
  indexPath,
  depth,
  childrenByParent,
  nodeById,
}: KnowledgeTreeBranchProps) {
  const childNodes = childrenByParent.get(node.id) ?? [];
  const prerequisites = node.prerequisite_node_ids
    .map((nodeId) => nodeById.get(nodeId)?.title)
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
            <span>{node.knowledge_unit_ids.length} 个知识单元</span>
            {prerequisites.length > 0 && <span>前置：{prerequisites.join(" / ")}</span>}
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
            />
          ))}
        </ol>
      )}
    </li>
  );
const runtimeWorkspaceKey = "metaclass-runtime-workspace-v1";
const completedWorkspacesKey = "metaclass-completed-workspaces-v1";

type RuntimeWorkspace = {
  material: Material | null;
  materials: Material[];
  materialCollection: MaterialCollection | null;
  pages: PageMetadata[];
  content: LearningContent | null;
  presentationPlan: PresentationPlan | null;
  presentationArtifact: PPTArtifact | null;
  presentationSlideImages: Record<number, string>;
  session: ClassroomSession | null;
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

function App() {
  const runtimeWorkspace = useRef(loadRuntimeWorkspace()).current;
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const savedTheme = window.localStorage.getItem("metaclass-theme");
    if (savedTheme === "dark" || savedTheme === "light") return savedTheme;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
  const [files, setFiles] = useState<File[]>([]);
  const [material, setMaterial] = useState<Material | null>(runtimeWorkspace.material ?? null);
  const [materials, setMaterials] = useState<Material[]>(runtimeWorkspace.materials ?? []);
  const [materialCollection, setMaterialCollection] = useState<MaterialCollection | null>(runtimeWorkspace.materialCollection ?? null);
  const [pages, setPages] = useState<PageMetadata[]>(runtimeWorkspace.pages ?? []);
  const [content, setContent] = useState<LearningContent | null>(runtimeWorkspace.content ?? null);
  const [contentJob, setContentJob] = useState<ContentGenerationJob | null>(null);
  const [contentView, setContentView] = useState<"outline" | "tree" | "quality">("outline");
  const [selectedKnowledgeTreeNodeId, setSelectedKnowledgeTreeNodeId] = useState<string | null>(null);
  const [presentationPlan, setPresentationPlan] = useState<PresentationPlan | null>(null);
  const [presentationPlan, setPresentationPlan] = useState<PresentationPlan | null>(runtimeWorkspace.presentationPlan ?? null);
  const [presentationPlanJob, setPresentationPlanJob] = useState<PresentationPlanJob | null>(null);
  const [pptJob, setPptJob] = useState<PPTGenerationJob | null>(null);
  const [pptThemes, setPptThemes] = useState<PPTThemeOption[]>([defaultPptTheme]);
  const [pptThemeId, setPptThemeId] = useState(() =>
    window.localStorage.getItem("metaclass-ppt-theme") || defaultPptTheme.id,
  );
  const [classroomPlanJob, setClassroomPlanJob] = useState<ClassroomPlanJob | null>(null);
  const [presentationArtifact, setPresentationArtifact] = useState<PPTArtifact | null>(runtimeWorkspace.presentationArtifact ?? null);
  const [presentationSlideImages, setPresentationSlideImages] = useState<Record<number, string>>(runtimeWorkspace.presentationSlideImages ?? {});
  const [session, setSession] = useState<ClassroomSession | null>(runtimeWorkspace.session ?? null);
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
    const snapshot: RuntimeWorkspace = {
      material,
      materials,
      materialCollection,
      pages,
      content,
      presentationPlan,
      presentationArtifact,
      presentationSlideImages,
      session,
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
    currentSlide,
    feedback,
    learningMode,
    material,
    materialCollection,
    materials,
    pages,
    presentationArtifact,
    presentationPlan,
    presentationSlideImages,
    session,
    classroomPlanIds,
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
    if (!content?.knowledge_tree) return { rootNodes: [], nodeById, childrenByParent };

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
    return { rootNodes, nodeById, childrenByParent };
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
  // currentSlide is updated as a consequence of SHOW_PAGE. Depending on it here
  // cancels the short SHOW_PAGE beat before autoStep can advance, while the same
  // action has already been marked narrated. Keep slide rendering independent
  // from the classroom playback state machine.
  }, [action, agentTurn, playbackTrigger, presentationPlan, session]);

  useEffect(() => {
    if (!feedback) return;
    const timer = window.setTimeout(() => setFeedback(""), 5800);
    return () => window.clearTimeout(timer);
  }, [feedback]);

  useEffect(() => {
    if (!action || action.type !== "SHOW_PAGE") return;
    displayPage(action);
  }, [action, material, presentationSlideImages]);

  function displayPage(pageAction: Extract<TeachingAction, { type: "SHOW_PAGE" }>) {
    const pageNo = pageAction.payload.slide_no ?? pageAction.payload.source_ref.page_no;
    const generatedImage = presentationSlideImages[pageNo];
    const generatedPages = Object.keys(presentationSlideImages)
      .map(Number)
      .sort((left, right) => left - right);
    if (generatedPages.length) {
      if (generatedImage) {
        setCurrentSlide({ src: generatedImage, pageNo, generated: true });
      } else {
        setCurrentSlide((previous) => {
          if (previous?.generated) return previous;
          const fallbackPage = generatedPages.at(-1)!;
          return {
            src: presentationSlideImages[fallbackPage],
            pageNo: fallbackPage,
            generated: true,
          };
        });
      }
      return;
    }
    const fallbackImage = material ? api.pageImage(material.id, pageNo) : "";
    setCurrentSlide({
      src: generatedImage ?? fallbackImage,
      pageNo,
      generated: Boolean(generatedImage),
    });
  }

  async function run<T>(label: string, task: () => Promise<T>): Promise<T | undefined> {
    setBusy(label);
    setError(null);
    try {
      return await task();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "发生未知错误");
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
    setPages([]);
    setContent(null);
    setContentJob(null);
    setContentView("outline");
    setPresentationPlan(null);
    setPresentationPlanJob(null);
    setPptJob(null);
    setClassroomPlanJob(null);
    setPresentationArtifact(null);
    setPresentationSlideImages({});
    setSession(null);
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

  async function upload() {
    if (!files.length) return;
    const result = files.length === 1
      ? await run("正在上传并解析材料", () => api.upload(files[0]))
      : await run("正在上传并解析材料", () => api.uploadMany(files));
    if (result) {
      const processed = "items" in result ? result.items : [result];
      if (!processed.length) return;
      setMaterials(processed.map((item) => item.material));
      setMaterialCollection("collection" in result ? result.collection ?? null : null);
      setMaterial(processed[0].material);
      setPages(processed[0].pages);
    }
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
      materialCollection
        ? api.buildCollectionContent(materialCollection.id, setContentJob)
        : api.buildContent(material.id, setContentJob),
    );
    if (result) setContent(result);
  }

  async function startClassroom() {
    if (!content) return;
    narration.unlock();
    setPresentationPlanJob(null);
    setPptJob(null);
    setClassroomPlanJob(null);
    const result = await run("正在生成 PPT 并布置课堂", async () => {
      const deck = await api.createPresentationDeck(
        content.id,
        learningMode === "interactive",
        pptThemeId,
        setPresentationPlanJob,
        setPptJob,
      );
      const artifact = deck.artifact;
      const slideImages = Object.fromEntries(
        artifact.slide_images.map((slide) => [
          slide.slide_no,
          api.pptSlideImage(artifact.id, slide.slide_no),
        ]),
      );
      let classroomSession = await api.createSession(
        content.id,
        deck.plan.id,
        learningMode,
        learningMode === "interactive" ? studentAgentTypes : [],
        setClassroomPlanJob,
      );
      if (learningMode === "lecture") {
        const lecturePlan = await api.createLectureVariant(classroomSession.plan_id);
        classroomSession = await api.createSessionForPlan(lecturePlan.id, "lecture", []);
      }
      return { artifact, classroomSession, plan: deck.plan, slideImages };
    });
    if (result) {
      playbackVersionRef.current += 1;
      autoPlayingRef.current = true;
      setPresentationArtifact(result.artifact);
      setPresentationPlan(result.plan);
      setPresentationSlideImages(result.slideImages);
      setSession(result.classroomSession);
      setClassroomPlanIds((current) => ({
        ...current,
        [learningMode]: result.classroomSession.plan_id,
      }));
      setAction(null);
      setAgentTurn(null);
      setAutoPlaying(true);
      setFeedback("PPT 已生成，课堂已就绪，自动播放已开始。你可以随时输入问题打断。");
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
      nextMode === "interactive" ? "正在增加互动课堂规划" : "正在提取连续讲解课堂",
      async () => {
        const savedPlanId = classroomPlanIds[nextMode];
        if (savedPlanId) {
          return api.createSessionForPlan(
            savedPlanId,
            nextMode,
            nextMode === "interactive" ? studentAgentTypes : [],
          );
        }
        if (nextMode === "interactive") {
          await api.generateQuestionBank(presentationPlan.id);
          return api.createSession(
            content.id,
            presentationPlan.id,
            "interactive",
            studentAgentTypes,
            setClassroomPlanJob,
          );
        }
        const lecturePlan = await api.createLectureVariant(session.plan_id);
        return api.createSessionForPlan(lecturePlan.id, "lecture", []);
      },
    );
    if (!nextSession) return;
    playbackVersionRef.current += 1;
    autoPlayingRef.current = false;
    setLearningMode(nextMode);
    setSession(nextSession);
    setClassroomPlanIds((current) => ({ ...current, [nextMode]: nextSession.plan_id }));
    setAction(null);
    setAgentTurn(null);
    setCurrentSlide(null);
    setAutoPlaying(false);
    setFeedback(nextMode === "interactive" ? "互动规划已增加，可以开始互动课堂。" : "已提取展示与讲解动作，可以开始连续课堂。");
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
    const prepared = await run("正在完成并保存课堂", async () => {
      const existing = await api.getQuestionBank(presentationPlan.id);
      return existing.items.length ? existing : api.generateQuestionBank(presentationPlan.id);
    });
    if (!prepared) return;
    const completed: CompletedWorkspace = {
      id: `${content.id}:${Date.now()}`,
      title: content.title,
      completedAt: new Date().toISOString(),
      material,
      materials,
      materialCollection,
      pages,
      content,
      presentationPlan,
      presentationArtifact,
      presentationSlideImages,
      session,
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
    setFeedback("当前课堂项目已完成保存，可以上传下一组材料。");
  }

  function restoreWorkspace(saved: CompletedWorkspace) {
    reset();
    setMaterial(saved.material);
    setMaterials(saved.materials);
    setMaterialCollection(saved.materialCollection);
    setPages(saved.pages);
    setContent(saved.content);
    setPresentationPlan(saved.presentationPlan);
    setPresentationArtifact(saved.presentationArtifact);
    setPresentationSlideImages(saved.presentationSlideImages);
    setSession(saved.session);
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
      const result = await api.autoStep(session.id);
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
    const result = await run("Evaluator 正在评估", () => api.answer(session.id, selectedIndex));
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

    // Start generating the teacher's answer immediately, then use that same wait
    // to read the learner's question aloud. This keeps the exchange conversational
    // without adding the question narration to the response latency.
    const answerRequest = run(answeringUserQuestionLabel, () =>
      api.ask(session.id, submittedQuestion),
    );
    const questionNarrationResult = await narration.play({
      id: `user-question:${session.id}:${Date.now()}`,
      text: submittedQuestion,
      scope: "user_question",
      refId: `${session.id}:user-question:${Date.now()}`,
      voice: "student_user",
      speaker: "你",
      agentId: "user",
      role: "student",
    });
    if (questionNarrationResult === "failed") {
      setError("问题已发送，但问题语音暂时无法播放");
    }

    const result = await answerRequest;
    if (!result) return;
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
        setError("老师的文字回答已显示，但语音播放暂时不可用");
      }
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
  const loadingProgress = contentJob && busy === "正在组织学习内容"
    ? contentJob.progress
    : presentationPlanJob && busy === "正在生成 PPT 并布置课堂"
      ? classroomPlanJob
        ? classroomPlanJob.progress
        : pptJob
          ? 75 + Math.round(pptJob.progress * 25)
          : presentationPlanJob.progress
      : null;
  const loadingLabel = contentJob && busy === "正在组织学习内容"
    ? contentProgressLabel(contentJob)
    : presentationPlanJob && busy === "正在生成 PPT 并布置课堂"
      ? classroomPlanJob
        ? classroomPlanProgressLabel(classroomPlanJob)
        : pptJob
          ? "正在导出 PPTX"
          : presentationProgressLabel(presentationPlanJob)
      : busy;
  const loadingSteps = busy === "正在生成 PPT 并布置课堂"
    ? ["理解课程内容", "规划课件页面", "设计课堂版式", "渲染并导出 PPT", "布置互动课堂"]
    : busy === "正在组织学习内容"
      ? ["读取课程材料", "逐页理解内容", "提取知识单元", "组织课堂结构", "保存备课结果"]
      : ["接收课堂任务", "检查课程材料", "执行当前操作", "保存处理结果"];
  const loadingStepIndex = loadingProgress === null
    ? 0
    : Math.min(loadingSteps.length - 1, Math.floor((loadingProgress / 100) * loadingSteps.length));

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
                disabled={!!busy}
                onClick={() => switchClassroomMode("lecture")}
              >
                <span>A</span>
                <b>连续讲解</b>
                <small>老师按 PPT 一页页讲，不安排同学插嘴</small>
              </button>
              <button
                className={learningMode === "interactive" ? "selected" : ""}
                disabled={!!busy}
                onClick={() => switchClassroomMode("interactive")}
              >
                <span>B</span>
                <b>互动课堂</b>
                <small>允许 agent 同学提问、总结和插话</small>
              </button>
            </div>
            <div className="ppt-theme-selector">
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
            </div>
            <button disabled={!pages.length || !!content || !!busy} onClick={buildContent}><span>01</span><b>{content ? "内容已构建" : "构建学习内容"}</b><i>↗</i></button>
            <button disabled={!content || !!session || !!busy} onClick={startClassroom}><span>02</span><b>{session ? "课堂已创建" : learningMode === "interactive" ? "创建互动课堂" : "创建连续课堂"}</b><i>↗</i></button>
            <button disabled={!content || !presentationArtifact || !!video || !!busy} onClick={createVideo}><span>03</span><b>{video ? "视频已生成" : "合成讲解视频"}</b><i>↗</i></button>
            <button disabled={!content || !presentationPlan || !!busy} onClick={completeWorkspace}><span>✓</span><b>完成当前材料</b><i>→</i></button>
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
              {contentView === "outline" && (
                <ol>{content.sections.map((section, index) => <li key={section.id}><span>{String(index + 1).padStart(2, "0")}</span><div><b>{section.title}</b><small>来源 · 第 {section.source_refs[0]?.page_no ?? "?"} 页</small></div></li>)}</ol>
              )}
              {contentView === "tree" && (
                <ol className="knowledge-tree-list">
                  {knowledgeTreeModel.rootNodes.length ? knowledgeTreeModel.rootNodes.map((node, index) => (
                    <li className="knowledge-root-item" key={node.id}>
                      <button type="button" onClick={() => setSelectedKnowledgeTreeNodeId(node.id)}>
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <div>
                          <b>{node.title}</b>
                          <small>
                            {formatKnowledgeTreeRole(node.role)}
                            {" · "}
                            {(knowledgeTreeModel.childrenByParent.get(node.id) ?? []).length} 个子主题
                          </small>
                        </div>
                        <i>查看</i>
                      </button>
                    </li>
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
              />
            </ol>
          </section>
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
