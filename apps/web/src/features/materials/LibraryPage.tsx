import { useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api";
import type {
  Material,
  MaterialCollection,
  MaterialLearningContentSummary,
  ClassroomPlanLibrarySummary,
  ClassroomSession,
  LearningContent,
  PageMetadata,
  PPTArtifact,
  PresentationPlan,
  PresentationPlanLibrarySummary,
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

function displayDate(value?: string) {
  if (!value) return "历史资料";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

export function LibraryPage({ onBack, onUseMaterial, onOpenAsset }: LibraryPageProps) {
  const [materials, setMaterials] = useState<Material[]>([]);
  const [collections, setCollections] = useState<MaterialCollection[]>([]);
  const [contentSummaries, setContentSummaries] = useState<MaterialLearningContentSummary[]>([]);
  const [presentationPlans, setPresentationPlans] = useState<PresentationPlanLibrarySummary[]>([]);
  const [classroomPlans, setClassroomPlans] = useState<ClassroomPlanLibrarySummary[]>([]);
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

  useEffect(() => {
    let active = true;
    Promise.all([
      api.listMaterials(),
      api.listMaterialCollections(),
      api.listMaterialLearningContentSummaries(),
      api.listPresentationPlanLibrary(),
      api.listClassroomPlanLibrary(),
    ])
      .then(([materialItems, collectionItems, summaryItems, planItems, classroomItems]) => {
        if (!active) return;
        setMaterials(materialItems);
        setCollections(collectionItems);
        setContentSummaries(summaryItems);
        setPresentationPlans(planItems);
        setClassroomPlans(classroomItems);
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

  const visibleMaterials = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return materials.filter((item) => {
      const summary = contentSummaryByMaterialId.get(item.id);
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
  }, [activeView, classroomPlans, contentSummaryByMaterialId, filter, materials, presentationPlans, query]);

  const parsedCount = materials.filter((item) => item.status === "parsed").length;
  const viewCounts: Record<LibraryView, number> = {
    projects: materials.length,
    raw: materials.length,
    parsed: parsedCount,
    content: contentSummaryByMaterialId.size,
    presentation: new Set(presentationPlans.map((plan) => plan.content_id)).size,
    classroom: new Set(classroomPlans.map((plan) => plan.content_id)).size,
  };

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

  async function openClassroomAsset(
    material: Material,
    contentId: string,
    planSummary: PresentationPlanLibrarySummary,
    savedClassroomPlanId?: string,
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
        : await api.createSession(contentId, plan.id, "interactive");
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
                const summary = contentSummaryByMaterialId.get(item.id);
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
                <small>{contentSummaryByMaterialId.has(selected.id) ? "ORGANIZED TOPIC" : "FILE INSPECTOR"}</small>
                <h2>{contentSummaryByMaterialId.get(selected.id)?.title ?? selected.filename}</h2>
                {contentSummaryByMaterialId.has(selected.id) && <p>原文件：{selected.filename}</p>}
              </div>
              <button type="button" aria-label="关闭详情" onClick={() => setSelected(null)}>×</button>
            </header>
            <div className="inspector-meta">
              <span><small>格式</small><b>{selected.file_type.toUpperCase()}</b></span>
              <span><small>页数</small><b>{selected.page_count}</b></span>
              <span><small>状态</small><b>{statusCopy[selected.status]}</b></span>
            </div>
            <button className="use-library-material" disabled={detailLoading || selected.status !== "parsed"} onClick={() => onUseMaterial(selected, pages)}>
              <span>载入当前备课</span><b>→</b>
            </button>
            <div className="library-asset-pipeline">
              <div className="library-section-title"><span>可复用备课资产</span><small>ASSET PIPELINE</small></div>
              {(() => {
                const contentSummary = contentSummaryByMaterialId.get(selected.id);
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
                    <button disabled={!!assetBusy} onClick={() => loadContentAsset(selected, contentSummary.content_id)}>载入</button>
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
                      <button className="asset-primary-action" disabled={!!assetBusy} onClick={() => openClassroomAsset(selected, contentSummary.content_id, plan)}>创建互动课堂</button>
                      {scripts.map((script) => <button className="asset-script-action" disabled={!!assetBusy} key={script.id} onClick={() => openClassroomAsset(selected, contentSummary.content_id, plan, script.id)}>
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
