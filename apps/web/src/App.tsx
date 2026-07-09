import { type ChangeEvent, type CSSProperties, type DragEvent, type FormEvent, useMemo, useState } from "react";
import { ActionView } from "./features/classroom/ActionView";
import { api } from "./shared/api";
import { formatBytes } from "./shared/format";
import type {
  ClassroomSession,
  DirectedAgentTurn,
  LearningContent,
  LearningMode,
  Material,
  PageMetadata,
  TeachingAction,
  VideoResult,
} from "./shared/types";

const stages = ["导入材料", "页面解析", "组织内容", "互动课堂", "讲解视频"];

function App() {
  const [file, setFile] = useState<File | null>(null);
  const [material, setMaterial] = useState<Material | null>(null);
  const [pages, setPages] = useState<PageMetadata[]>([]);
  const [content, setContent] = useState<LearningContent | null>(null);
  const [session, setSession] = useState<ClassroomSession | null>(null);
  const [action, setAction] = useState<TeachingAction | null>(null);
  const [learningMode, setLearningMode] = useState<LearningMode>("lecture");
  const [agentTurn, setAgentTurn] = useState<DirectedAgentTurn | null>(null);
  const [feedback, setFeedback] = useState("");
  const [question, setQuestion] = useState("");
  const [video, setVideo] = useState<VideoResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const activeStage = useMemo(() => {
    if (video) return 4;
    if (session) return 3;
    if (content) return 2;
    if (pages.length) return 1;
    return 0;
  }, [content, pages.length, session, video]);

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

  function chooseFile(nextFile?: File) {
    if (!nextFile) return;
    const extension = nextFile.name.split(".").pop()?.toLowerCase();
    if (extension !== "pdf" && extension !== "pptx") {
      setError("请选择 PDF 或 PPTX 文件");
      return;
    }
    reset(false);
    setFile(nextFile);
  }

  function reset(clearFile = true) {
    if (clearFile) setFile(null);
    setMaterial(null);
    setPages([]);
    setContent(null);
    setSession(null);
    setAction(null);
    setAgentTurn(null);
    setFeedback("");
    setVideo(null);
    setError(null);
  }

  async function upload() {
    if (!file) return;
    const result = await run("正在上传并解析材料", () => api.upload(file));
    if (result) {
      setMaterial(result.material);
      setPages(result.pages);
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
    const result = await run("正在组织学习内容", () => api.buildContent(material.id));
    if (result) setContent(result);
  }

  async function startClassroom() {
    if (!content) return;
    const result = await run("正在布置课堂", () => api.createSession(content.id, learningMode));
    if (result) {
      setSession(result);
      setAction(null);
      setAgentTurn(null);
      setFeedback("课堂已就绪，点击“执行下一步”开始。");
    }
  }

  async function nextAction() {
    if (!session) return;
    const result = await run("Controller 正在决策", () => api.next(session.id));
    if (!result) return;
    setSession(result.session);
    setAction(result.action);
    setAgentTurn(null);
    setFeedback(result.status === "completed" ? "本次课堂已经完成。" : "");
  }

  async function nextAgentTurn() {
    if (!session || session.mode !== "interactive") return;
    const result = await run("LLM Controller 正在调度智能体", () => api.nextAgentTurn(session.id));
    if (!result) return;
    setAgentTurn(result);
    const firstTurn = result.turns[0];
    setFeedback(firstTurn ? firstTurn.speech : result.decision.reason);
  }

  async function answer(selectedIndex: number) {
    if (!session) return;
    const result = await run("Evaluator 正在评估", () => api.answer(session.id, selectedIndex));
    if (!result) return;
    setSession(result.session);
    setAgentTurn(null);
    setFeedback(result.feedback ?? "");
    setAction(null);
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!session || !question.trim()) return;
    const result = await run("Teacher 正在回答", () => api.ask(session.id, question.trim()));
    if (!result) return;
    setSession(result.session);
    setAgentTurn(null);
    setFeedback(result.feedback ?? "");
    setQuestion("");
  }

  async function createVideo() {
    if (!content) return;
    const result = await run("正在合成视频，这可能需要片刻", () => api.createVideo(content.id));
    if (result) setVideo(result);
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  }

  const actionLabel = action?.type.replaceAll("_", " ") ?? "WAITING";

  return (
    <div className="classroom-app">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="MetaClass 首页">
          <span className="brand-seal">M</span>
          <span><b>MetaClass</b><small>AI CLASSROOM STUDIO</small></span>
        </a>
        <div className="room-title">
          <span>ROOM 01</span>
          <b>{content?.title ?? "新课堂准备室"}</b>
        </div>
        <div className="system-live"><i /> LOCAL SYSTEM ONLINE</div>
      </header>

      <main id="top" className="classroom-layout">
        <aside className="left-console">
          <div className="console-label">LESSON SETUP</div>
          <nav className="lesson-steps" aria-label="课堂生成进度">
            {stages.map((stage, index) => (
              <div className={`${index === activeStage ? "active" : ""} ${index < activeStage ? "done" : ""}`} key={stage}>
                <span>{index < activeStage ? "✓" : String(index + 1).padStart(2, "0")}</span>
                <b>{stage}</b>
                <i />
              </div>
            ))}
          </nav>

          <section className="material-dock">
            <div className="section-caption"><span>课程材料</span><small>PDF / PPTX</small></div>
            <label
              className={`compact-drop ${dragging ? "dragging" : ""}`}
              onDragEnter={() => setDragging(true)}
              onDragLeave={() => setDragging(false)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={onDrop}
            >
              <input type="file" accept=".pdf,.pptx" onChange={(event: ChangeEvent<HTMLInputElement>) => chooseFile(event.target.files?.[0])} />
              <span className="upload-icon">↥</span>
              <div>{file ? <><b>{file.name}</b><small>{formatBytes(file.size)}</small></> : <><b>把课件放到讲台</b><small>拖拽或点击选择文件</small></>}</div>
            </label>
            {!material ? (
              <button className="control-button warm" disabled={!file || !!busy} onClick={upload}>上传并解析 <span>→</span></button>
            ) : (
              <div className="material-ticket">
                <div><span>FILE</span><b>{material.filename}</b></div>
                <div><span>STATUS</span><b className="success">● 已解析 · {pages.length} 页</b></div>
                <button onClick={() => reset()}>更换材料</button>
              </div>
            )}
            {material && !pages.length && <button className="control-button warm" disabled={!!busy} onClick={parse}>重新解析</button>}
          </section>

          <section className="quick-actions">
            <div className="section-caption"><span>备课控制</span><small>ACTIONS</small></div>
            <div className="mode-switch" aria-label="选择学习方式">
              <button
                className={learningMode === "lecture" ? "selected" : ""}
                disabled={!!session}
                onClick={() => setLearningMode("lecture")}
              >
                <span>A</span>
                <b>连续讲解</b>
                <small>老师按 PPT 一页页讲，不安排同学插嘴</small>
              </button>
              <button
                className={learningMode === "interactive" ? "selected" : ""}
                disabled={!!session}
                onClick={() => setLearningMode("interactive")}
              >
                <span>B</span>
                <b>互动课堂</b>
                <small>允许 agent 同学提问、总结和插话</small>
              </button>
            </div>
            <button disabled={!pages.length || !!content || !!busy} onClick={buildContent}><span>01</span><b>{content ? "内容已构建" : "构建学习内容"}</b><i>↗</i></button>
            <button disabled={!content || !!session || !!busy} onClick={startClassroom}><span>02</span><b>{session ? "课堂进行中" : "创建互动课堂"}</b><i>↗</i></button>
            <button disabled={!content || !!video || !!busy} onClick={createVideo}><span>03</span><b>{video ? "视频已生成" : "合成讲解视频"}</b><i>↗</i></button>
          </section>
        </aside>

        <section className="teaching-studio">
          <div className="studio-ceiling"><i /><i /><i /><span>METACLASS · SMART TEACHING WALL</span><i /><i /><i /></div>
          <div className="blackboard">
            <div className="board-meta"><span><i /> {session ? "SESSION LIVE" : "CLASSROOM STANDBY"}</span><b>{actionLabel}</b><small>{session?.id ?? "等待创建课堂"}</small></div>
            <div className="projection-screen">
              {session ? (
                <ActionView action={action} materialId={material?.id} onAnswer={answer} />
              ) : pages.length ? (
                <div className="preview-wall">
                  <div className="preview-copy">
                    <small>COURSE MATERIAL READY</small>
                    <h1>{content?.title ?? pages[0]?.title}</h1>
                    <p>{content ? `${content.sections.length} 个教学章节已组织完毕，可以创建课堂。` : `${pages.length} 个页面已经解析，下一步构建统一学习内容。`}</p>
                  </div>
                  <div className="slide-stack">
                    {pages.slice(0, 3).map((page, index) => <img style={{ "--index": index } as CSSProperties} key={page.id} src={api.pageImage(page.material_id, page.page_no)} alt={`第 ${page.page_no} 页`} />)}
                  </div>
                </div>
              ) : (
                <div className="empty-classroom">
                  <div className="room-emblem">M</div>
                  <small>READ · PLAN · RUN</small>
                  <h1>让课件走上讲台，<br />变成一堂真正的课。</h1>
                  <p>从左侧导入 PDF 或 PPTX，页面、讲解、小测与来源引用都会在这里展开。</p>
                </div>
              )}
            </div>
            <div className="board-tray"><span /><span /><i>MC</i><span /><span /></div>
          </div>

          <div className="teacher-desk">
            <div className="desk-status"><span>{session?.mode === "interactive" ? "AGENT CLASS" : "LECTURE MODE"}</span><b>{session?.status === "completed" ? "课堂已结束" : session ? "课堂进行中" : "等待课堂"}</b></div>
            <button className="next-button" disabled={!session || !!busy || session.waiting_for === "quiz_answer" || session.status === "completed"} onClick={nextAction}>执行下一步 <span>→</span></button>
            {session?.mode === "interactive" && (
              <button className="agent-button" disabled={!!busy || session.status === "completed"} onClick={nextAgentTurn}>
                智能体下一轮 <span>✦</span>
              </button>
            )}
            <form onSubmit={ask}>
              <label htmlFor="student-question">学生提问</label>
              <input id="student-question" value={question} onChange={(event) => setQuestion(event.target.value)} disabled={!session} placeholder="输入关于当前内容的问题…" />
              <button disabled={!question.trim() || !session || !!busy}>发送</button>
            </form>
          </div>
          {agentTurn && (
            <div className="agent-turn-card">
              <div className="agent-turn-head">
                <span>LLM DIRECTOR</span>
                <b>{agentTurn.decision.next_role.toUpperCase()}</b>
              </div>
              <p className="director-reason">{agentTurn.decision.reason}</p>
              {agentTurn.turns.map((turn) => (
                <article className={`agent-speech ${turn.role}`} key={`${turn.agent_id}-${turn.intent}`}>
                  <span>{turn.role === "teacher" ? "Teacher" : turn.agent_id}</span>
                  <p>{turn.speech}</p>
                  {turn.actions.length > 0 && <small>{turn.actions.join(" · ")}</small>}
                </article>
              ))}
            </div>
          )}
          {feedback && <div className="teacher-response"><span>TEACHER</span><p>{feedback}</p><button onClick={() => setFeedback("")}>×</button></div>}
        </section>

        <aside className="right-board">
          <section className="lesson-outline">
            <div className="section-caption"><span>今日课表</span><small>LESSON PLAN</small></div>
            {content ? <>
              <h2>{content.title}</h2>
              <div className="objective-tags">{content.objectives.map((item) => <span key={item}>{item}</span>)}</div>
              <ol>{content.sections.map((section, index) => <li key={section.id}><span>{String(index + 1).padStart(2, "0")}</span><div><b>{section.title}</b><small>来源 · 第 {section.source_refs[0]?.page_no ?? "?"} 页</small></div></li>)}</ol>
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
            <div><small>LECTURE REPLAY</small><b>{video ? "讲解视频已就绪" : "课后讲解视频"}</b><p>{video ? "MP4 与字幕已保存在本地" : "页面图片 + 测试音轨 + SRT 字幕"}</p></div>
            {video && <a href={api.videoDownload(video.id)}>下载 MP4 ↗</a>}
          </section>
        </aside>
      </main>

      {error && <div className="error-toast" role="alert"><span>!</span><div><b>流程暂停</b><p>{error}</p></div><button onClick={() => setError(null)}>×</button></div>}
      {busy && <div className="busy-overlay"><div className="loader"><i /><i /><i /></div><b>{busy}</b><small>请不要关闭课堂</small></div>}
    </div>
  );
}

export default App;
