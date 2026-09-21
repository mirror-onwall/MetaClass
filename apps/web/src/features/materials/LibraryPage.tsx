import { useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api";
import { defaultStudentAgentTypes, studentAgentChoices } from "../../shared/studentAgents";
import type {
  Material,
  MaterialCollection,
  MaterialLearningContentSummary,
  ClassroomPlanLibrarySummary,
  ClassroomSession,
  ContentGenerationJob,
  LearningContent,
  PageMetadata,
  PPTArtifact,
  PresentationPlan,
  PresentationPlanLibrarySummary,
  PresentationPlanJob,
  PPTGenerationJob,
  PaperWorkflowJob,
  MaterialProcessingJob,
  StudentAgentType,
  ClassroomQA,
  QuestionBank,
} from "../../shared/types";

type LibraryPageProps = {
  onBack: () => void;
  onUseMaterial: (material: Material, pages: PageMetadata[]) => void;
  onOpenAsset: (asset: {
    material: Material;
    pages: PageMetadata[];
    content: LearningContent;
    presentationPlan?: PresentationPlan;
    presentationArtifact?: PPTArtifact;
    session?: ClassroomSession;
    preferFreshClassroomPlan?: boolean;
  }) => void;
};

type FileFilter = "all" | "pdf" | "pptx";
type LibraryView = "projects" | "raw" | "parsed" | "content" | "presentation" | "classroom";

const libraryViews: Array<{ id: LibraryView; label: string; short: string }> = [
  { id: "projects", label: "全部项目", short: "PROJECTS" },
  { id: "raw", label: "原始材料", short: "SOURCE" },
  { id: "parsed", label: "已解析", short: "PARSED" },
  { id: "content", label: "LearningContent", short: "CONTENT" },
  { id: "presentation", label: "PresentationPlan", short: "PLAN" },
  { id: "classroom", label: "课堂剧本", short: "SCRIPT" },
];

const statusCopy: Record<Material["status"], string> = {
  uploaded: "等待解析",
  parsing: "解析中",
  parsed: "已解析",
  failed: "解析失败",
};

const qaMomentCopy: Record<ClassroomQA["moment"], string> = {
  before_explanation: "讲解前",
  during_explanation: "讲解中",
  after_explanation: "讲解后",
  before_next_slide: "翻页前",
};

function displayDate(value?: string) {
  if (!value) return "历史资料";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

function displayDateTime(value?: string) {
  if (!value) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function LibraryPage({ onBack, onUseMaterial, onOpenAsset }: LibraryPageProps) {
  const [materials, setMaterials] = useState<Material[]>([]);
  const [collections, setCollections] = useState<MaterialCollection[]>([]);
  const [contentSummaries, setContentSummaries] = useState<MaterialLearningContentSummary[]>([]);
  const [presentationPlans, setPresentationPlans] = useState<PresentationPlanLibrarySummary[]>([]);
  const [classroomPlans, setClassroomPlans] = useState<ClassroomPlanLibrarySummary[]>([]);
  const [materialJobs, setMaterialJobs] = useState<MaterialProcessingJob[]>([]);
  const [contentJobs, setContentJobs] = useState<ContentGenerationJob[]>([]);
  const [planJobs, setPlanJobs] = useState<PresentationPlanJob[]>([]);
  const [pptJobs, setPptJobs] = useState<PPTGenerationJob[]>([]);
  const [paperWorkflowJobs, setPaperWorkflowJobs] = useState<PaperWorkflowJob[]>([]);
  const [selected, setSelected] = useState<Material | null>(null);
  const [pages, setPages] = useState<PageMetadata[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FileFilter>("all");
  const [activeView, setActiveView] = useState<LibraryView>("projects");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [assetBusy, setAssetBusy] = useState<string | null>(null);
  const [viewerPage, setViewerPage] = useState<number | null>(null);
  const [viewerZoom, setViewerZoom] = useState(1);
  const [classroomDraft, setClassroomDraft] = useState<{
    material: Material;
    contentId: string;
    plan: PresentationPlanLibrarySummary;
  } | null>(null);
  const [draftStudentTypes, setDraftStudentTypes] = useState<StudentAgentType[]>(defaultStudentAgentTypes);
  const [qaViewer, setQaViewer] = useState<{
    plan: PresentationPlanLibrarySummary;
    items: ClassroomQA[];
    versions: QuestionBank[];
    generationId?: string;
    loading: boolean;
    error?: string;
  } | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.listMaterials(),
      api.listMaterialCollections(),
      api.listMaterialLearningContentSummaries(),
      api.listPresentationPlanLibrary(),
      api.listClassroomPlanLibrary(),
      api.listMaterialProcessingJobs(),
      api.listContentGenerationJobs(),
      api.listPresentationPlanJobs(),
      api.listPptJobs(),
      api.listPaperWorkflows(),
    ])
      .then(([materialItems, collectionItems, summaryItems, planItems, classroomItems, materialJobItems, contentJobItems, planJobItems, pptJobItems, workflowItems]) => {
        if (!active) return;
        setMaterials(materialItems);
        setCollections(collectionItems);
        setContentSummaries(summaryItems);
        setPresentationPlans(planItems);
        setClassroomPlans(classroomItems);
        setMaterialJobs(materialJobItems);
        setContentJobs(contentJobItems);
        setPlanJobs(planJobItems);
        setPptJobs(pptJobItems);
        setPaperWorkflowJobs(workflowItems);
      })
      .catch((reason: Error) => active && setError(reason.message))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (viewerPage === null) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setViewerPage(null);
      if (event.key === "ArrowLeft") {
        setViewerPage((current) => current === null ? null : Math.max(0, current - 1));
        setViewerZoom(1);
      }
      if (event.key === "ArrowRight") {
        setViewerPage((current) => current === null ? null : Math.min(pages.length - 1, current + 1));
        setViewerZoom(1);
      }
      if (event.key === "+" || event.key === "=") setViewerZoom((current) => Math.min(3, current + .25));
      if (event.key === "-") setViewerZoom((current) => Math.max(.5, current - .25));
    };
    document.body.classList.add("material-viewer-open");
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.classList.remove("material-viewer-open");
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [pages.length, viewerPage]);

  const contentSummaryByMaterialId = useMemo(() => {
    const result = new Map<string, MaterialLearningContentSummary>();
    contentSummaries.forEach((summary) => {
      summary.material_ids.forEach((materialId) => result.set(materialId, summary));
    });
    return result;
  }, [contentSummaries]);

  const parentByMaterialId = useMemo(() => {
    const result = new Map<string, string>();
    materials.forEach((material) => {
      if (material.parent_material_id) result.set(material.id, material.parent_material_id);
    });
    // Workflows created before parent_material_id was introduced still contain
    // the authoritative source/derived relationship.
    paperWorkflowJobs.forEach((job) => {
      if (job.derived_material_id && !result.has(job.derived_material_id)) {
        result.set(job.derived_material_id, job.source_material_id);
      }
    });
    return result;
  }, [materials, paperWorkflowJobs]);

  const derivedMaterialsByParent = useMemo(() => {
    const result = new Map<string, Material[]>();
    materials.forEach((material) => {
      const parentId = parentByMaterialId.get(material.id);
      if (!parentId) return;
      result.set(parentId, [...(result.get(parentId) ?? []), material]);
    });
    return result;
  }, [materials, parentByMaterialId]);

  const projectSummaryByMaterialId = useMemo(() => {
    const result = new Map(contentSummaryByMaterialId);
    derivedMaterialsByParent.forEach((children, parentId) => {
      const summary = children.map((child) => contentSummaryByMaterialId.get(child.id)).find(Boolean);
      if (summary && !result.has(parentId)) result.set(parentId, summary);
    });
    return result;
  }, [contentSummaryByMaterialId, derivedMaterialsByParent]);

  const visibleMaterials = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return materials.filter((item) => {
      if (parentByMaterialId.has(item.id)) return false;
      const summary = projectSummaryByMaterialId.get(item.id);
      const contentPlans = summary
        ? presentationPlans.filter((plan) => plan.content_id === summary.content_id)
        : [];
      const hasClassroom = summary
        ? classroomPlans.some((plan) => plan.content_id === summary.content_id)
        : false;
      const matchesType = filter === "all" || item.file_type === filter;
      const matchesQuery = !keyword || [item.filename, summary?.title, summary?.subtitle]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase().includes(keyword));
      const matchesView = activeView === "projects" || activeView === "raw"
        ? true
        : activeView === "parsed"
          ? item.status === "parsed"
          : activeView === "content"
            ? Boolean(summary)
            : activeView === "presentation"
              ? contentPlans.length > 0
              : hasClassroom;
      return matchesType && matchesQuery && matchesView;
    });
  }, [activeView, classroomPlans, filter, materials, parentByMaterialId, presentationPlans, projectSummaryByMaterialId, query]);

  const rootMaterials = materials.filter((item) => !parentByMaterialId.has(item.id));
  const parsedCount = rootMaterials.filter((item) => item.status === "parsed").length;
  const viewCounts: Record<LibraryView, number> = {
    projects: rootMaterials.length,
    raw: rootMaterials.length,
    parsed: parsedCount,
    content: rootMaterials.filter((item) => projectSummaryByMaterialId.has(item.id)).length,
    presentation: new Set(presentationPlans.map((plan) => plan.content_id)).size,
    classroom: new Set(classroomPlans.map((plan) => plan.content_id)).size,
  };

  function projectMaterialIds(materialId: string) {
    const parentId = parentByMaterialId.get(materialId) ?? materialId;
    return [parentId, ...(derivedMaterialsByParent.get(parentId) ?? []).map((item) => item.id)];
  }

  function projectAssetMaterial(material: Material) {
    const summary = projectSummaryByMaterialId.get(material.id);
    if (!summary) return material;
    return materials.find((item) => summary.material_ids.includes(item.id)) ?? material;
  }

  async function inspect(material: Material) {
    setSelected(material);
    setPages([]);
    setViewerPage(null);
    setDetailLoading(true);
    setError(null);
    try {
      setPages(await api.getMaterialPages(material.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取材料详情");
    } finally {
      setDetailLoading(false);
    }
  }

  async function deleteProject(material: Material) {
    const rootId = parentByMaterialId.get(material.id) ?? material.id;
    const rootMaterial = materials.find((item) => item.id === rootId) ?? material;
    const confirmed = window.confirm(
      `确定删除“${projectSummaryByMaterialId.get(rootId)?.title ?? rootMaterial.filename}”吗？\n\n原文件、解析页面、LearningContent、演示稿和课堂记录都会一并删除，此操作无法撤销。`,
    );
    if (!confirmed) return;
    setAssetBusy("正在删除资料项目");
    setError(null);
    try {
      const contentId = projectSummaryByMaterialId.get(rootId)?.content_id;
      const projectIds = projectMaterialIds(rootId);
      const derivedIds = projectIds.filter((id) => id !== rootId);
      for (const derivedId of derivedIds) await api.deleteMaterial(derivedId);
      await api.deleteMaterial(rootId);
      setMaterials((current) => current.filter((item) => !projectIds.includes(item.id)));
      setCollections((current) => current
        .map((collection) => ({
          ...collection,
          material_ids: collection.material_ids.filter((id) => !projectIds.includes(id)),
          primary_material_id: collection.primary_material_id && projectIds.includes(collection.primary_material_id)
            ? collection.material_ids.find((id) => !projectIds.includes(id))
            : collection.primary_material_id,
        }))
        .filter((collection) => collection.material_ids.length > 0));
      if (contentId) {
        setContentSummaries((current) => current.filter((item) => item.content_id !== contentId));
        setPresentationPlans((current) => current.filter((item) => item.content_id !== contentId));
        setClassroomPlans((current) => current.filter((item) => item.content_id !== contentId));
      }
      setSelected(null);
      setPages([]);
      setViewerPage(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资料项目删除失败");
    } finally {
      setAssetBusy(null);
    }
  }

  async function buildStoredContent(material: Material) {
    setAssetBusy("正在从已解析资料构建 LearningContent");
    setError(null);
    try {
      const content = await api.buildContent(material.id);
      const summary: MaterialLearningContentSummary = {
        content_id: content.id,
        material_ids: content.material_ids?.length ? content.material_ids : [material.id],
        title: content.title,
        subtitle: content.subtitle,
      };
      setContentSummaries((current) => [summary, ...current]);
      onOpenAsset({ material, pages, content });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "学习内容构建失败");
    } finally {
      setAssetBusy(null);
    }
  }

  async function loadContentAsset(material: Material, contentId: string) {
    setAssetBusy("正在载入 LearningContent");
    setError(null);
    try {
      const content = await api.getLearningContent(contentId);
      onOpenAsset({ material, pages, content });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "LearningContent 载入失败");
    } finally {
      setAssetBusy(null);
    }
  }

  async function loadPresentationPlanAsset(
    material: Material,
    contentId: string,
    planSummary: PresentationPlanLibrarySummary,
    preferFreshClassroomPlan = true,
  ) {
    setAssetBusy("正在载入 PresentationPlan 与上游内容");
    setError(null);
    try {
      const [content, plan, artifact] = await Promise.all([
        api.getLearningContent(contentId),
        api.getPresentationPlan(planSummary.id),
        planSummary.artifact_id
          ? api.getPptArtifact(planSummary.artifact_id)
          : Promise.resolve(undefined),
      ]);
      onOpenAsset({
        material,
        pages,
        content,
        presentationPlan: plan,
        presentationArtifact: artifact,
        preferFreshClassroomPlan,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "PresentationPlan 载入失败");
    } finally {
      setAssetBusy(null);
    }
  }

  async function loadProjectWorkspace(material: Material) {
    const summary = projectSummaryByMaterialId.get(material.id);
    if (!summary) {
      onUseMaterial(material, pages);
      return;
    }
    const assetMaterial = projectAssetMaterial(material);
    const plans = presentationPlans
      .filter((plan) => plan.content_id === summary.content_id)
      .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
    if (plans[0]) {
      await loadPresentationPlanAsset(assetMaterial, summary.content_id, plans[0]);
      return;
    }
    await loadContentAsset(assetMaterial, summary.content_id);
  }

  async function createStoredPresentationPlan(material: Material, contentId: string) {
    setAssetBusy("正在生成 PresentationPlan");
    setError(null);
    try {
      const plan = await api.createPresentationPlan(contentId, true);
      setPresentationPlans((current) => [{
        id: plan.id,
        content_id: plan.content_id,
        title: plan.title,
        slide_count: plan.slides.length,
        created_at: new Date().toISOString(),
      }, ...current]);
      const content = await api.getLearningContent(contentId);
      onOpenAsset({ material, pages, content, presentationPlan: plan });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "PresentationPlan 生成失败");
    } finally {
      setAssetBusy(null);
    }
  }

  async function resumeLibraryJob(kind: "material" | "content" | "plan" | "ppt", id: string) {
    setError(null);
    setAssetBusy("正在从已保存进度继续");
    try {
      if (kind === "material") {
        await api.resumeMaterialProcessingJob(id);
        const result = await api.waitForMaterialProcessingJob(id, (job) => setMaterialJobs((items) => items.map((item) => item.id === id ? job : item)));
        const item = result.items.find((entry) => entry.material.id === selected?.id) ?? result.items[0];
        if (item) onUseMaterial(item.material, item.pages);
      } else if (kind === "content" && selected) {
        const resumed = await api.resumeContentGenerationJob(id);
        setContentJobs((items) => items.map((item) => item.id === id ? resumed : item));
        const content = await api.waitForContentGenerationJob(id, (job) => setContentJobs((items) => items.map((item) => item.id === id ? job : item)));
        onOpenAsset({ material: selected, pages, content });
      } else if (kind === "plan" && selected) {
        await api.resumePresentationPlanJob(id);
        const finished = await api.waitForPresentationPlanJob(id, (job) => setPlanJobs((items) => items.map((item) => item.id === id ? job : item)));
        const plan = await api.getPresentationPlanJobResult(finished.id);
        const content = await api.getLearningContent(plan.content_id);
        onOpenAsset({ material: selected, pages, content, presentationPlan: plan });
      } else if (kind === "ppt" && selected) {
        await api.resumePptJob(id);
        const finished = await api.waitForPptJob(id, (job) => setPptJobs((items) => items.map((item) => item.id === id ? job : item)));
        const artifact = await api.getPptArtifact(finished.artifact_id!);
        const plan = await api.getPresentationPlan(artifact.presentation_plan_id);
        const content = await api.getLearningContent(plan.content_id);
        onOpenAsset({ material: selected, pages, content, presentationPlan: plan, presentationArtifact: artifact });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "恢复任务失败");
    } finally {
      setAssetBusy(null);
    }
  }

  async function discardLibraryJob(kind: "material" | "content" | "plan" | "ppt", id: string) {
    if (!window.confirm("确定放弃并删除这项临时进度吗？")) return;
    if (kind === "material") { await api.discardMaterialProcessingJob(id); setMaterialJobs((items) => items.filter((item) => item.id !== id)); }
    if (kind === "content") { await api.discardContentGenerationJob(id); setContentJobs((items) => items.filter((item) => item.id !== id)); }
    if (kind === "plan") { await api.discardPresentationPlanJob(id); setPlanJobs((items) => items.filter((item) => item.id !== id)); }
    if (kind === "ppt") { await api.discardPptJob(id); setPptJobs((items) => items.filter((item) => item.id !== id)); }
  }

  async function pauseLibraryJob(kind: "material" | "content" | "plan" | "ppt", id: string) {
    if (kind === "material") { const job = await api.pauseMaterialProcessingJob(id); setMaterialJobs((items) => items.map((item) => item.id === id ? job : item)); }
    if (kind === "content") { const job = await api.pauseContentGenerationJob(id); setContentJobs((items) => items.map((item) => item.id === id ? job : item)); }
    if (kind === "plan") { const job = await api.pausePresentationPlanJob(id); setPlanJobs((items) => items.map((item) => item.id === id ? job : item)); }
    if (kind === "ppt") { const job = await api.pausePptJob(id); setPptJobs((items) => items.map((item) => item.id === id ? job : item)); }
  }

  async function openClassroomAsset(
    material: Material,
    contentId: string,
    planSummary: PresentationPlanLibrarySummary,
    savedClassroomPlanId?: string,
    selectedStudentTypes?: StudentAgentType[],
  ) {
    setAssetBusy(savedClassroomPlanId ? "正在载入并播放课堂剧本" : "正在创建互动课堂剧本");
    setError(null);
    try {
      const [content, plan, artifact] = await Promise.all([
        api.getLearningContent(contentId),
        api.getPresentationPlan(planSummary.id),
        planSummary.artifact_id
          ? api.getPptArtifact(planSummary.artifact_id)
          : Promise.resolve(undefined),
      ]);
      const session = savedClassroomPlanId
        ? await api.createSessionForPlan(savedClassroomPlanId, "interactive")
        : await api.createSession(contentId, plan.id, "interactive", selectedStudentTypes ?? defaultStudentAgentTypes);
      onOpenAsset({
        material,
        pages,
        content,
        presentationPlan: plan,
        presentationArtifact: artifact,
        session,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "互动课堂创建失败");
    } finally {
      setAssetBusy(null);
    }
  }

  function prepareNewClassroom(
    material: Material,
    contentId: string,
    plan: PresentationPlanLibrarySummary,
  ) {
    setDraftStudentTypes(defaultStudentAgentTypes);
    setClassroomDraft({ material, contentId, plan });
  }

  function toggleDraftStudent(type: StudentAgentType) {
    setDraftStudentTypes((current) =>
      current.includes(type)
        ? current.filter((item) => item !== type)
        : [...current, type],
    );
  }

  async function confirmNewClassroom() {
    if (!classroomDraft || !draftStudentTypes.length) return;
    const draft = classroomDraft;
    setClassroomDraft(null);
    await openClassroomAsset(draft.material, draft.contentId, draft.plan, undefined, draftStudentTypes);
  }

  async function viewQuestionBank(plan: PresentationPlanLibrarySummary) {
    setQaViewer({ plan, items: [], versions: [], loading: true });
    try {
      const versions = await api.listQuestionBankVersions(plan.id);
      const bank = versions[0];
      setQaViewer((current) => current?.plan.id === plan.id
        ? {
            ...current,
            versions,
            generationId: bank?.generation_id,
            items: bank?.items ?? [],
            loading: false,
          }
        : current);
    } catch (reason) {
      setQaViewer((current) => current?.plan.id === plan.id
        ? {
            ...current,
            loading: false,
            error: reason instanceof Error ? reason.message : "QA 问答对读取失败",
          }
        : current);
    }
  }

  async function regenerateInteractiveClassroom(plan: PresentationPlanLibrarySummary) {
    const confirmed = window.confirm(
      "确定生成一个新的互动课堂版本吗？\n\n将新增一套 QA 问答和对应课堂剧本；原问答、原课堂、PPT、知识树和逐页讲稿都会保留。",
    );
    if (!confirmed) return;
    setAssetBusy("正在生成新版问答与互动课堂");
    setError(null);
    setQaViewer((current) => current?.plan.id === plan.id
      ? { ...current, loading: true, error: undefined }
      : current);
    try {
      const bank = await api.regenerateQuestionBank(plan.id);
      const job = await api.createClassroomPlanJob(plan.content_id, plan.id);
      await api.waitForClassroomPlanJob(job.id);
      const [versions, scripts] = await Promise.all([
        api.listQuestionBankVersions(plan.id),
        api.listClassroomPlanLibrary(),
      ]);
      setClassroomPlans(scripts);
      setQaViewer({
        plan,
        items: bank.items,
        versions,
        generationId: bank.generation_id,
        loading: false,
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "新版互动课堂生成失败";
      setError(`${message}；原问答库和原课堂均已保留。`);
      setQaViewer((current) => current?.plan.id === plan.id
        ? { ...current, loading: false, error: `${message}；历史版本未被覆盖。` }
        : current);
    } finally {
      setAssetBusy(null);
    }
  }

  async function archiveQuestionBankVersion(plan: PresentationPlanLibrarySummary, generationId: string) {
    if (!window.confirm("从资料库隐藏这一版问答吗？已保存的旧课堂仍可继续播放。")) return;
    setAssetBusy("正在归档问答版本");
    try {
      await api.archiveQuestionBankVersion(plan.id, generationId);
      const versions = await api.listQuestionBankVersions(plan.id);
      const next = versions[0];
      setQaViewer({
        plan,
        versions,
        generationId: next?.generation_id,
        items: next?.items ?? [],
        loading: false,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "问答版本归档失败");
    } finally {
      setAssetBusy(null);
    }
  }

  return (
    <main className="library-page">
      <header className="library-shell-header">
        <div className="library-shell-brand">
          <button className="library-back" type="button" onClick={onBack}>← 返回课堂</button>
          <span className="brand-seal">M</span>
          <div><b>教学资料库</b><small>METACLASS · ASSET LIBRARY</small></div>
        </div>
        <div className="library-shell-summary">
          <span><small>资料项目</small><b>{materials.length}</b></span>
          <span><small>已整理内容</small><b>{contentSummaryByMaterialId.size}</b></span>
          <span><small>课堂剧本</small><b>{classroomPlans.length}</b></span>
          <i>● LOCAL ARCHIVE</i>
        </div>
      </header>

      <nav className="library-view-tabs" aria-label="资料资产分类">
        {libraryViews.map((view, index) => (
          <button type="button" className={activeView === view.id ? "active" : ""} onClick={() => setActiveView(view.id)} key={view.id}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <b>{view.label}</b>
            <small>{view.short}</small>
            <em>{viewCounts[view.id]}</em>
          </button>
        ))}
      </nav>

      <section className="library-toolbar library-control-strip">
        <div>
          <b>{libraryViews.find((view) => view.id === activeView)?.label}</b>
          <small>同一资料项目在处理完成后自动进入对应分类，无需手动搬运。</small>
        </div>
        <label><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索主题名称或原始文件名" /></label>
        <div className="library-filters" role="group" aria-label="按文件类型筛选">
          {(["all", "pdf", "pptx"] as FileFilter[]).map((item) => (
            <button className={filter === item ? "active" : ""} onClick={() => setFilter(item)} key={item} type="button">
              {item === "all" ? "全部格式" : item.toUpperCase()}
            </button>
          ))}
        </div>
      </section>

      {error && <div className="library-error"><b>资料库读取提示</b><span>{error}</span><button onClick={() => setError(null)}>×</button></div>}

      <section className="library-content library-workbench">
        <div className="library-shelf">
          <div className="library-section-title"><span>资料项目</span><small>{visibleMaterials.length} PROJECTS · 按最近入库排序</small></div>
          {loading ? (
            <div className="library-empty"><i>⌛</i><b>正在整理资料架</b><span>读取历史材料记录…</span></div>
          ) : visibleMaterials.length ? (
            <div className="library-grid">
              {visibleMaterials.map((item, index) => {
                const summary = projectSummaryByMaterialId.get(item.id);
                const sourceName = item.filename.replace(/\.(pdf|pptx)$/i, "");
                const plans = summary
                  ? presentationPlans.filter((plan) => plan.content_id === summary.content_id)
                  : [];
                const scripts = summary
                  ? classroomPlans.filter((plan) => plan.content_id === summary.content_id)
                  : [];
                return (
                <article className={`library-card ${selected?.id === item.id ? "selected" : ""}`} key={item.id}>
                  <button className="library-card-main" type="button" onClick={() => inspect(item)}>
                    <div className={`library-file-cover ${item.file_type}`}>
                      <span>{item.file_type.toUpperCase()}</span>
                      <strong>{String(index + 1).padStart(2, "0")}</strong>
                      <small>PROJECT</small>
                      <i>{String(index + 1).padStart(3, "0")}</i>
                    </div>
                    <div className="library-card-copy">
                      <div className="library-card-status-line">
                        <span className={`library-status ${item.status}`}>● {statusCopy[item.status]}</span>
                        {summary && <span className="library-ai-title">主题已整理</span>}
                      </div>
                      <h2 title={summary?.title ?? sourceName}>{summary?.title ?? sourceName}</h2>
                      <p className="library-source-name" title={item.filename}>原文件 · {item.filename}</p>
                      {summary?.subtitle && <p className="library-topic-subtitle">{summary.subtitle}</p>}
                      <div className="library-project-progress" aria-label="资料处理进度">
                        <span className="done"><i />原始材料</span>
                        <span className={item.status === "parsed" ? "done" : ""}><i />已解析</span>
                        <span className={summary ? "done" : ""}><i />内容</span>
                        <span className={plans.length ? "done" : ""}><i />演示</span>
                        <span className={scripts.length ? "done" : ""}><i />课堂</span>
                      </div>
                      <footer><span>{item.page_count} 页 · {displayDate(item.created_at)}</span><b>管理项目 →</b></footer>
                    </div>
                  </button>
                </article>
              )})}
            </div>
          ) : (
            <div className="library-empty"><i>□</i><b>没有找到匹配资料</b><span>尝试更换关键词或文件类型。</span></div>
          )}

          {activeView === "projects" && collections.length > 0 && (
            <div className="collection-strip">
              <div className="library-section-title"><span>资料组合</span><small>{collections.length} COLLECTIONS</small></div>
              <div>{collections.map((item) => <span key={item.id}><b>{item.title}</b><small>{item.material_ids.length} 份材料</small></span>)}</div>
            </div>
          )}
        </div>

        <aside className={`library-inspector ${selected ? "open" : ""}`}>
          {selected ? <>
            <header>
              <div>
                <small>{projectSummaryByMaterialId.has(selected.id) ? "ORGANIZED TOPIC" : "FILE INSPECTOR"}</small>
                <h2>{projectSummaryByMaterialId.get(selected.id)?.title ?? selected.filename}</h2>
                {projectSummaryByMaterialId.has(selected.id) && <p>原文件：{selected.filename}</p>}
              </div>
              <button type="button" aria-label="关闭详情" onClick={() => setSelected(null)}>×</button>
            </header>
            <div className="inspector-meta">
              <span><small>格式</small><b>{selected.file_type.toUpperCase()}</b></span>
              <span><small>页数</small><b>{selected.page_count}</b></span>
              <span><small>状态</small><b>{statusCopy[selected.status]}</b></span>
            </div>
            <button className="use-library-material" disabled={detailLoading || selected.status !== "parsed" || !!assetBusy} onClick={() => loadProjectWorkspace(selected)}>
              <span>{projectSummaryByMaterialId.has(selected.id) ? "载入已保存备课链" : "载入当前备课"}</span><b>→</b>
            </button>
            {(derivedMaterialsByParent.get(selected.id) ?? []).length > 0 && (
              <div className="library-derived-assets">
                <div className="library-section-title"><span>生成的演示稿</span><small>DERIVED ARTIFACTS</small></div>
                {(derivedMaterialsByParent.get(selected.id) ?? []).map((artifact) => (
                  <article key={artifact.id}>
                    <span>{artifact.file_type.toUpperCase()}</span>
                    <div><b>{artifact.filename}</b><small>{artifact.page_count} 页 · 由原论文生成</small></div>
                    <div className="library-derived-actions">
                      <button type="button" onClick={() => inspect(artifact)}>查看</button>
                      <a href={api.materialDownload(artifact.id)} download>下载 {artifact.file_type.toUpperCase()}</a>
                    </div>
                  </article>
                ))}
              </div>
            )}
            <button
              className="delete-library-project"
              disabled={!!assetBusy}
              onClick={() => deleteProject(selected)}
              type="button"
            >
              删除整个资料项目
            </button>
            <div className="library-asset-pipeline">
              <div className="library-section-title"><span>可复用备课资产</span><small>ASSET PIPELINE</small></div>
              {(() => {
                const summary = projectSummaryByMaterialId.get(selected.id);
                const relatedPlanIds = new Set(presentationPlans.filter((plan) => plan.content_id === summary?.content_id).map((plan) => plan.id));
                const relevantContentJobs = contentJobs.filter((job) => job.material_id && projectMaterialIds(selected.id).includes(job.material_id));
                const latestFailedByMode = new Map<string, string>();
                relevantContentJobs
                  .filter((job) => job.status === "failed")
                  .sort((left, right) => Date.parse(right.updated_at ?? right.created_at ?? "") - Date.parse(left.updated_at ?? left.created_at ?? ""))
                  .forEach((job) => {
                    const mode = job.organization_mode ?? "knowledge";
                    if (!latestFailedByMode.has(mode)) latestFailedByMode.set(mode, job.id);
                  });
                const active = [
                  ...materialJobs.filter((job) => ["queued", "running", "paused"].includes(job.status) && job.material_ids.some((id) => projectMaterialIds(selected.id).includes(id))).map((job) => ({ kind: "material" as const, id: job.id, status: job.status, title: "材料解析", detail: `${job.message} · ${job.progress}%`, historicalFailure: false })),
                  ...relevantContentJobs.filter((job) => ["queued", "running", "paused", "failed"].includes(job.status)).map((job) => {
                    const mode = job.organization_mode ?? "knowledge";
                    const latestFailure = job.status === "failed" && latestFailedByMode.get(mode) === job.id;
                    const historicalFailure = job.status === "failed" && !latestFailure;
                    const baseTitle = mode === "source_deck" ? "原稿 LearningContent" : "LearningContent";
                    return {
                      kind: "content" as const,
                      id: job.id,
                      status: job.status,
                      title: `${latestFailure ? "【最近失败】" : historicalFailure ? "【历史失败】" : ""}${baseTitle}`,
                      detail: job.status === "failed"
                        ? `${displayDateTime(job.updated_at)} · ${latestFailure ? "可从 checkpoint 重试" : "仅作历史记录"}`
                        : `${job.message} · ${job.progress}%`,
                      historicalFailure,
                    };
                  }),
                  ...planJobs.filter((job) => ["queued", "running", "paused"].includes(job.status) && job.content_id === summary?.content_id).map((job) => ({ kind: "plan" as const, id: job.id, status: job.status, title: job.mode === "source_deck" ? "原稿讲稿 / PresentationPlan" : "PresentationPlan / 题库", detail: `${job.message} · ${job.progress}%`, historicalFailure: false })),
                  ...pptJobs.filter((job) => ["queued", "running", "waiting_for_skill", "paused"].includes(job.status) && relatedPlanIds.has(job.presentation_plan_id)).map((job) => ({ kind: "ppt" as const, id: job.id, status: job.status, title: "PPT 生成", detail: `PPT 产物进度 ${Math.round(job.progress * 100)}%`, historicalFailure: false })),
                ].sort((left, right) => Number(Boolean(left.historicalFailure)) - Number(Boolean(right.historicalFailure)));
                if (!active.length) return null;
                return <div className="library-in-progress-assets">
                  <div className="in-progress-heading"><span>Ⅱ</span><div><b>进行中的备课</b><small>{active.length} 项进度已安全保存</small></div></div>
                  {active.map((job) => <article key={`${job.kind}:${job.id}`}>
                    <div><b>{job.title}</b><p>{job.detail}</p></div>
                    <button disabled={!!assetBusy || Boolean(job.historicalFailure)} onClick={() => ["paused", "failed"].includes(job.status) ? resumeLibraryJob(job.kind, job.id) : pauseLibraryJob(job.kind, job.id)}>{job.historicalFailure ? "历史记录" : job.status === "failed" ? "从失败处重试" : job.status === "paused" ? "从中断处继续" : "停止并保存"}</button>
                    <button className="discard" disabled={!!assetBusy || !["paused", "failed"].includes(job.status)} onClick={() => discardLibraryJob(job.kind, job.id)}>放弃</button>
                  </article>)}
                </div>;
              })()}
              {(() => {
                const contentSummary = projectSummaryByMaterialId.get(selected.id);
                const assetMaterial = projectAssetMaterial(selected);
                if (!contentSummary) return (
                  <div className="asset-stage pending">
                    <span>01</span><div><b>LearningContent</b><p>原资料已解析，可以直接整理整篇主题与知识结构。</p></div>
                    <button disabled={!!assetBusy || detailLoading} onClick={() => buildStoredContent(selected)}>构建内容</button>
                  </div>
                );
                const plans = presentationPlans.filter((plan) => plan.content_id === contentSummary.content_id);
                return <>
                  <div className="asset-stage ready">
                    <span>01</span><div><b>{contentSummary.title}</b><p>LearningContent · 已保存</p></div>
                    <button disabled={!!assetBusy} onClick={() => loadContentAsset(assetMaterial, contentSummary.content_id)}>载入</button>
                  </div>
                  <div className={`asset-stage ${plans.length ? "ready" : "pending"}`}>
                    <span>02</span><div><b>PresentationPlan</b><p>{plans.length ? `${plans.length} 个演示规划可复用` : "尚未生成演示结构"}</p></div>
                    <button disabled={!!assetBusy} onClick={() => createStoredPresentationPlan(selected, contentSummary.content_id)}>{plans.length ? "新建" : "生成"}</button>
                  </div>
                  {plans.map((plan) => {
                    const scripts = classroomPlans.filter((script) =>
                      script.presentation_plan_id === plan.id
                      || (!script.presentation_plan_id && script.content_id === contentSummary.content_id)
                    );
                    return <div className="asset-plan-group" key={plan.id}>
                      <div className="asset-plan-heading"><span>PLAN</span><b>{plan.title}</b><small>{plan.slide_count} 页{plan.artifact_id ? " · PPT 已就绪" : ""}</small></div>
                      <div className="asset-plan-actions">
                        <button className="asset-plan-load" disabled={!!assetBusy} onClick={() => loadPresentationPlanAsset(assetMaterial, contentSummary.content_id, plan, true)}>
                          <span>载入后手动创建课堂</span><small>含内容 / 讲稿 / PPT</small>
                        </button>
                        <button className="asset-primary-action" disabled={!!assetBusy} onClick={() => prepareNewClassroom(assetMaterial, contentSummary.content_id, plan)}>资料库内创建课堂</button>
                        <button className="asset-qa-action" disabled={!!assetBusy} onClick={() => viewQuestionBank(plan)}>
                          <span>查看 QA 问答对</span><small>Q / A</small>
                        </button>
                        <button className="asset-qa-regenerate" disabled={!!assetBusy} onClick={() => regenerateInteractiveClassroom(plan)}>
                          <span>生成新版互动课堂</span><small>新增 QA + 新课堂 · 保留历史</small>
                        </button>
                      </div>
                      {scripts.map((script) => <button className="asset-script-action" disabled={!!assetBusy} key={script.id} onClick={() => openClassroomAsset(assetMaterial, contentSummary.content_id, plan, script.id)}>
                        <span>▶ 播放已保存剧本</span><small>{script.scene_count} 场景 · {script.action_count} 动作</small>
                      </button>)}
                    </div>;
                  })}
                </>;
              })()}
              {assetBusy && <div className="asset-busy"><i />{assetBusy}</div>}
            </div>
            <div className="inspector-pages">
              <div className="library-section-title"><span>原材料逐页查看</span><small>{detailLoading ? "LOADING" : `${pages.length} PAGES · 点击放大`}</small></div>
              {detailLoading ? <div className="inspector-loading">正在调取解析档案…</div> : pages.map((page) => (
                <article key={page.id}>
                  <button
                    className="inspector-page-preview"
                    type="button"
                    onClick={() => {
                      setViewerPage(pages.indexOf(page));
                      setViewerZoom(1);
                    }}
                    aria-label={`放大查看第 ${page.page_no} 页`}
                  >
                    <img src={api.pageImage(selected.id, page.page_no)} alt={`第 ${page.page_no} 页`} loading="lazy" />
                    <span>放大查看</span>
                  </button>
                  <div><span>PAGE {String(page.page_no).padStart(2, "0")}</span><b>{page.title || `第 ${page.page_no} 页`}</b><p>{page.raw_text || "该页面没有提取到可显示的文本。"}</p></div>
                </article>
              ))}
            </div>
          </> : <div className="inspector-placeholder"><span>⌁</span><b>选择一份历史材料</b><p>这里会展示文件状态、解析页面与继续备课入口。</p></div>}
        </aside>
      </section>
      {classroomDraft && (
        <div className="library-roster-modal" role="dialog" aria-modal="true" aria-labelledby="library-roster-title">
          <div className="library-roster-dialog">
            <header>
              <div>
                <small>CLASSROOM ROSTER</small>
                <h2 id="library-roster-title">选择本堂课的学生</h2>
                <p>默认已选择全部 8 位。学生类型会影响课堂中的提问、回应和互动侧重点。</p>
              </div>
              <button type="button" aria-label="关闭学生选择" onClick={() => setClassroomDraft(null)}>×</button>
            </header>
            <div className="library-roster-toolbar">
              <span>已选择 <b>{draftStudentTypes.length}</b> / {studentAgentChoices.length}</span>
              <button type="button" onClick={() => setDraftStudentTypes(defaultStudentAgentTypes)}>全选</button>
              <button type="button" onClick={() => setDraftStudentTypes([])}>清空</button>
            </div>
            <div className="library-roster-grid">
              {studentAgentChoices.map((agent) => {
                const selectedAgent = draftStudentTypes.includes(agent.type);
                return (
                  <button
                    type="button"
                    className={selectedAgent ? "selected" : ""}
                    aria-pressed={selectedAgent}
                    key={agent.type}
                    onClick={() => toggleDraftStudent(agent.type)}
                  >
                    <img src={agent.avatar} alt="" />
                    <span><b>{agent.studentName} · {agent.name}</b><small>{agent.description}</small></span>
                    <i>{selectedAgent ? "✓" : "+"}</i>
                  </button>
                );
              })}
            </div>
            <footer>
              <button type="button" onClick={() => setClassroomDraft(null)}>取消</button>
              <button type="button" className="confirm" disabled={!draftStudentTypes.length} onClick={confirmNewClassroom}>
                用 {draftStudentTypes.length} 位学生创建课堂
              </button>
            </footer>
          </div>
        </div>
      )}
      {qaViewer && (
        <div className="library-qa-modal" role="dialog" aria-modal="true" aria-labelledby="library-qa-title">
          <div className="library-qa-dialog">
            <header>
              <div>
                <small>PREPARED CLASSROOM DIALOGUE</small>
                <h2 id="library-qa-title">QA 问答对</h2>
                <p>{qaViewer.plan.title} · {qaViewer.plan.slide_count} 页演示规划</p>
              </div>
              <div className="library-qa-count"><b>{qaViewer.items.length}</b><span>PAIRS</span></div>
              <button
                className="library-qa-regenerate"
                type="button"
                disabled={!!assetBusy || qaViewer.loading}
                onClick={() => regenerateInteractiveClassroom(qaViewer.plan)}
              >{qaViewer.loading ? "生成中…" : "＋ 新版互动课堂"}</button>
              <button type="button" aria-label="关闭 QA 问答对" onClick={() => setQaViewer(null)}>×</button>
            </header>
            {qaViewer.versions.length > 0 && (
              <nav className="library-qa-versions" aria-label="问答库历史版本">
                {qaViewer.versions.map((version, index) => (
                  <span className={qaViewer.generationId === version.generation_id ? "active" : ""} key={version.generation_id}>
                    <button type="button" onClick={() => setQaViewer((current) => current ? {
                      ...current,
                      generationId: version.generation_id,
                      items: version.items,
                      error: undefined,
                    } : current)}>
                      版本 {qaViewer.versions.length - index} · {version.items.length} 对
                    </button>
                    <button type="button" aria-label="删除这一版问答" disabled={!!assetBusy} onClick={() => archiveQuestionBankVersion(qaViewer.plan, version.generation_id)}>×</button>
                  </span>
                ))}
              </nav>
            )}
            <div className="library-qa-body">
              {qaViewer.loading ? (
                <div className="library-qa-empty loading"><i />正在调取课堂问答档案…</div>
              ) : qaViewer.error ? (
                <div className="library-qa-empty"><b>读取失败</b><p>{qaViewer.error}</p></div>
              ) : qaViewer.items.length ? (
                qaViewer.items.map((item, index) => {
                  const agent = studentAgentChoices.find((choice) => choice.type === item.agent_type);
                  return <article className="library-qa-card" key={item.id}>
                    <div className="library-qa-index">{String(index + 1).padStart(2, "0")}</div>
                    <div className="library-qa-meta">
                      <span>SLIDE {String(item.slide_order).padStart(2, "0")}</span>
                      <span>{qaMomentCopy[item.moment]}</span>
                      <span className={item.status}>{item.status === "approved" ? "已采用" : "未采用"}</span>
                    </div>
                    <div className="library-qa-exchange">
                      <section className="student">
                        {agent && <img src={agent.avatar} alt="" />}
                        <div><small>{agent ? `${agent.studentName} · ${agent.name}` : item.student_profile_id}</small><p>{item.student_question}</p></div>
                      </section>
                      <section className="teacher">
                        <span>师</span>
                        <div><small>芊芊老师 · PREPARED ANSWER</small><p>{item.teacher_answer}</p></div>
                      </section>
                    </div>
                    <footer><b>{item.knowledge_point}</b><span>{item.placement_reason}</span></footer>
                  </article>;
                })
              ) : (
                <div className="library-qa-empty">
                  <span>Q / A</span><b>这个演示规划还没有 QA 问答对</b>
                  <p>创建演示规划时未生成题库，或题库中没有可用的已保存问答。</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {selected && viewerPage !== null && pages[viewerPage] && (
        <div className="material-page-viewer" role="dialog" aria-modal="true" aria-label={`${selected.filename} 原材料查看器`}>
          <header>
            <div>
              <small>ORIGINAL MATERIAL</small>
              <b>{selected.filename}</b>
            </div>
            <div className="material-viewer-pagination">
              <button type="button" disabled={viewerPage === 0} onClick={() => { setViewerPage(viewerPage - 1); setViewerZoom(1); }}>← 上一页</button>
              <span><b>{viewerPage + 1}</b> / {pages.length}</span>
              <button type="button" disabled={viewerPage === pages.length - 1} onClick={() => { setViewerPage(viewerPage + 1); setViewerZoom(1); }}>下一页 →</button>
            </div>
            <div className="material-viewer-tools">
              <button type="button" aria-label="缩小" disabled={viewerZoom <= .5} onClick={() => setViewerZoom((current) => Math.max(.5, current - .25))}>−</button>
              <button type="button" className="zoom-value" onClick={() => setViewerZoom(1)}>{Math.round(viewerZoom * 100)}%</button>
              <button type="button" aria-label="放大" disabled={viewerZoom >= 3} onClick={() => setViewerZoom((current) => Math.min(3, current + .25))}>＋</button>
              <button type="button" className="material-viewer-close" aria-label="关闭原材料查看器" onClick={() => setViewerPage(null)}>×</button>
            </div>
          </header>
          <div className="material-viewer-canvas">
            <img
              src={api.pageImage(selected.id, pages[viewerPage].page_no)}
              alt={`${selected.filename} 第 ${pages[viewerPage].page_no} 页`}
              style={{ width: `${viewerZoom * 100}%` }}
            />
          </div>
          <footer>
            <span>PAGE {String(pages[viewerPage].page_no).padStart(2, "0")}</span>
            <b>{pages[viewerPage].title || `第 ${pages[viewerPage].page_no} 页`}</b>
            <small>方向键翻页 · ＋/－ 缩放 · Esc 关闭</small>
          </footer>
        </div>
      )}
    </main>
  );
}
