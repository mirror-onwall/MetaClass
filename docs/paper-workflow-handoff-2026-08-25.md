# MetaClass 论文讲解链路与 QA Bank 重构交接

更新时间：2026-08-25  
工作区：`/Users/mirror/project/mirror/EduVerse_Classroom`  
当前分支：`feature/yj`  
状态：存在未提交改动，未推送远端

## 1. 给下一位 Codex 的一句话摘要

论文讲解后端的四 Stage、多 Skill 编排、证据化产物、`paper_deck` 课堂投影、论文知识树和原文辅助讲稿已经基本闭环；前端已改为原备课左栏中的第三条课件路线，课堂继续复用原 ClassroomPlan/ClassroomSession。当前最关键的问题不再是论文 PPT，而是三条课件路线共用的 QA Bank：旧策略会让 8 种学生画像扫描所有页面，24 页论文可触发数十次 Kimi-K3 请求，导致互动课堂创建超过两小时。工作区里已经有一版“先选教学节点再生成问题”的未提交 WIP，但还需要系统化收尾。

## 2. 首要阅读材料

- 总设计稿：[paper-teaching-mode-design.md](/Users/mirror/project/mirror/EduVerse_Classroom/docs/paper-teaching-mode-design.md)
- 本交接文档：[paper-workflow-handoff-2026-08-25.md](/Users/mirror/project/mirror/EduVerse_Classroom/docs/paper-workflow-handoff-2026-08-25.md)
- 论文工作流模块：[paper_workflow](/Users/mirror/project/mirror/EduVerse_Classroom/apps/api/src/metaclass/modules/paper_workflow)
- QA Bank 模块：[question_bank](/Users/mirror/project/mirror/EduVerse_Classroom/apps/api/src/metaclass/modules/question_bank)
- 课堂运行核心：[classroom/service.py](/Users/mirror/project/mirror/EduVerse_Classroom/apps/api/src/metaclass/modules/classroom/service.py)
- 前端主流程：[App.tsx](/Users/mirror/project/mirror/EduVerse_Classroom/apps/web/src/App.tsx)

## 3. 已确定的产品决策

这些结论已经和用户反复对齐，后续不要重新推翻：

1. 论文讲解不是独立产品页面，而是现有备课流程的一种课件生成路线。
2. 左侧课件路线应保持三选一：
   - 使用原稿讲解：`source_deck`
   - 重新生成一套 PPT：`generated`
   - 论文讲解：`paper_deck`
3. 论文完成后仍然进入原来的课堂舞台播放，不实现论文专用播放器。
4. 课堂继续复用原来的 `ClassroomPlan`、`ClassroomSession`、老师 Agent、学生 Agent、Controller、Evaluator、TTS、用户打断提问和掌握度逻辑。
5. 复用原知识树基础设施；论文专用的是 Builder/Adapter，不是另一套知识树数据体系。
6. 知识树节点容量不要硬编码为最多 3 个知识单元，两条路线都只使用“尽量控制”的软约束。
7. 讲稿由 LLM 结合论文原文证据理解后自然生成，不要求机械朗读原文。
8. 最终 PPTX 必须先归一化和严格验证，再登记为派生 Material。
9. QA Bank 是三条课件路线共享能力，不允许做成论文专用题库。
10. 新 QA 策略的方向已经确定：先选择少量高价值教学节点，再为节点生成问题，不再逐页、逐画像穷举。

## 4. 论文工作流当前架构

统一链路为：

```text
Material/PDF
→ Stage 0 Paper Source Bundle
→ Stage 1 paper-analyze
→ Stage 2 Figure Catalog
→ Stage 3 academic-pptx outline
→ Stage 4 Anthropic 官方 pptx
→ ArtifactNormalizer strict
→ final/presentation.pptx
→ 派生 Material
→ PaperDeckLearningContent
→ paper_deck PresentationPlan
→ 原 ClassroomPlan/ClassroomSession
```

工作流模块位于：

```text
apps/api/src/metaclass/modules/paper_workflow/
├── api.py
├── schemas.py
├── models.py
├── repository.py
├── service.py
├── orchestrator.py
├── source_bundle.py
├── stage_cache.py
├── validators.py
├── artifact_normalizer.py
├── paper_classroom.py
├── presentation_stages.py
├── runtime/codex_skill_runtime.py
└── providers/composed_skills.py
```

## 5. Stage 0–4 已完成内容

### Stage 0：Paper Source Bundle

已经完成：

- 从 Material rich parse result 构建统一论文输入。
- 解析优先级遵守：`content_list_v2.json → content_list.json → middle.json → PageMetadata`。
- 生成稳定 block ID、统一页码、统一 bbox 定义。
- figure/table/equation 不因 Markdown 投影而消失。
- Job workspace 内路径使用相对路径。
- 最终 JSON 不写入 MinerU 临时绝对路径。
- 重复构建同一 Material 得到稳定 hash。

主要产物：

```text
source/
├── paper.pdf
├── paper_source.json
├── paper_content.md
├── existing_assets/
└── mineru/
```

### Stage 1：paper-analyze

已经完成：

- 输入 PDF、source JSON、content Markdown 和 resolved request。
- 输出 `paper_analysis.md/json` 和 execution report。
- JSON Schema、core claim、定量结果 source ref、页码、block/asset、confidence、绝对路径检查。
- JSON 形状错误只做一次结构修复。
- 证据大面积缺失可完整重跑 Stage 1。
- PDF 不可读时降级到 `paper_content.md` 并记录 warning。

### Stage 2：Figure Catalog

已经完成：

- `MinerUFigureCatalogBuilder`
- `ExtractPaperImagesAdapter`
- `FigureCatalogMerger`
- 根据 slide target、可用 MinerU assets、核心图缺失、低分辨率和 panel 缺失决定是否调用图片提取 Skill。
- 下游统一只读取 `figures.json + assets/`。
- 检查图片存在性、可打开性、尺寸、caption、page、source method、claim 引用和装饰图过滤。

### Stage 3：academic-pptx outline

已经完成：

- Skill 输入中提供 JSON Schema。
- 输出 `outline.md`、`presentation_outline.json`、`slide_evidence.json`。
- 检查 slide ID、顺序、章节连续性、claim 覆盖、asset 存在性、页数、单页主论点和定量结论。
- 增加三层失败处理：
  - JSON 字段形状错误：一次结构修复。
  - 小范围证据引用错误：一次 outline repair。
  - 明显叙事结构失败：完整重跑 Stage 3。

### Stage 4：Anthropic 官方 pptx

已经完成：

- 输出 PPTX、slide plan、speaker notes、QA report 和 rendered pages。
- 二次验收包括 python-pptx 打开、页数、标题映射、关键图片、placeholder、shape 越界、文本溢出、LibreOffice 渲染和 evidence 页码重绑。
- 轻微 JSON/notes/标题问题可 repair；PPTX 损坏、严重缺页、关键图片缺失直接失败。
- Stage 4 失败不会自动重跑 Stage 1–3。
- 修复过 Stage attempt 多加一次的问题。
- 真实 Stage 4 所需 LibreOffice/Codex CLI contract 已处理，但不同电脑仍需满足本机依赖。

## 6. Artifact Bundle 与派生 Material

最终目录按设计归一化：

```text
final/
├── presentation.pptx
├── paper_analysis.json
├── presentation_outline.json
├── slide_evidence.json
├── asset_manifest.json
├── speaker_notes.json
├── generation_report.json
├── qa_report.json
└── assets/
```

`ArtifactNormalizer` 只做格式和路径归一化，不生成新论文事实；支持：

- `strict`：最终发布必须通过。
- `diagnostic`：保留产物并返回错误清单。

派生 Material 顺序已经按设计调整为：

```text
生成 final/
→ strict 验证通过
→ 注册 final/presentation.pptx
→ 写 derived_material_id
→ 保存 Bundle 和 Job
```

并已考虑按 source job/PPTX hash 的幂等登记，避免 resume 时重复 Material。

## 7. paper_deck 课堂闭环

已实现 `paper_deck` 模式：

- 最终 PPTX 注册为派生 Material。
- 解析最终 PPT 页面供课堂投影。
- 从论文分析、大纲和 slide evidence 构建 `PaperDeckLearningContent`。
- 复用原知识树模型，使用论文专用 Builder 投影知识节点和 source refs。
- 构建 `paper_deck PresentationPlan`。
- 讲稿生成时为每页检索论文原文 block，LLM 同时看到页面语义、论文原文、claim、figure 和证据。
- 对讲稿进行数字、source ref、转场和长度校验。
- LLM 不可用或超时才回退 compact fallback。
- 创建课堂后完全进入原 ClassroomPlan/ClassroomSession。

相关提交历史：

```text
b2f2021 feat: close paper deck classroom workflow
4a46376 feat: project paper evidence into course knowledge tree
339f631 feat: compose evidence-grounded paper narration
4f5f3ff feat: ground paper narration in source context
b748404 feat: generate natural paper narration with llm
```

## 8. 前端当前状态

最新未提交改动已经撤掉论文独立页面，并把论文入口嵌入原主页面左侧。

现有流程：

```text
左侧上传 PDF
→ 选择“论文讲解”
→ 设置听众和时长
→ 生成论文讲解课件
→ 自动回填 Material、LearningContent、PresentationPlan、投影资源
→ 点击原来的“创建互动课堂/创建连续课堂”
→ 在原中间黑板播放
```

相关代码：

- `App.tsx` 中的 `buildPaperDeck()`。
- 左侧 `presentationMode === "paper_deck"` 路线。
- `shared/api.ts` 中的 paper workflow API。
- `shared/types.ts` 中的 paper workflow 类型。
- `styles.css` 中的小型 paper route settings/progress 样式。

注意：旧独立 `PaperWorkflowPage.tsx` 已删除；`styles.css` 里可能仍残留部分已无调用者的旧 `.paper-studio*` CSS，后续可清理。

前端最近一次验证：

```text
cd apps/web
npm run build
```

构建成功。没有启动新的服务。

## 9. 真实 Skill 与双样本验收现状

曾完成过 ShapefileGPT 和 Kimi-K3 双样本的真实四 Skill 验收，结果包括：

- 两个 workflow 都成功完成。
- 四个 Skill 在验收中均被实际观察到。
- 两篇论文都生成有效 PPTX。
- 原文 block 覆盖分别达到 22/22、24/24 slides。
- python-pptx 可打开。
- LibreOffice 可渲染。
- 两个样本均成功创建 Classroom Session。

尚存的不稳定点：

- `api.zhizengzeng.com / kimi-k3` 延迟波动很大。
- 一次真实讲稿请求耗时约 181 秒，且仍可能因数字、source ref 或 transition 校验失败而 fallback。
- 因此“真实四 Skill + 最新知识树 + 最新讲稿 + 最新 QA 策略”的双样本整套验收还需要在 QA 重构完成后重新跑。

验收脚本：

```text
apps/api/scripts/paper_workflow_acceptance.py
```

## 10. 当前 Git 状态

当前分支：`feature/yj`。

最近已有提交：

```text
b748404 feat: generate natural paper narration with llm
4f5f3ff feat: ground paper narration in source context
339f631 feat: compose evidence-grounded paper narration
4a46376 feat: project paper evidence into course knowledge tree
b2f2021 feat: close paper deck classroom workflow
eb53abb feat: add resumable paper presentation workflow
f588e24 feat: improve classroom QA and runtime resilience
d99f0f8 feat: enhance source deck and classroom workflows
```

当前工作树包含大量未提交改动，至少涉及：

```text
.gitignore
apps/api/src/metaclass/modules/classroom/planner.py
apps/api/src/metaclass/modules/paper_workflow/*
apps/api/src/metaclass/modules/presentation/*
apps/api/src/metaclass/modules/question_bank/generator.py
apps/api/tests/*
apps/api/scripts/*
apps/web/src/App.tsx
apps/web/src/shared/api.ts
apps/web/src/shared/types.ts
apps/web/src/styles.css
```

不要 reset、checkout 或覆盖这些改动。继续前先运行：

```bash
git status --short
git diff --stat
```

用户此前明确要求：当前先不要推送远端。是否提交也应再次征询，除非用户明确说“保存/commit”。

## 11. 当前互动课堂卡顿事故

2026-08-25 用户创建互动课堂时，课堂计划 Job 长时间停留在 20%。只读诊断结果：

```text
job_id: plan_job_c10812df2afe
status: running
step: planning
progress: 20
created_at: 2026-08-25 07:41:16 UTC
```

诊断时该 Job 已运行约 113 分钟。数据库中已经保存：

```text
39 条 QA
覆盖前 16/24 页
覆盖 6 种学生画像
```

这证明不是前端单纯卡死，而是旧 QA 生成策略仍在执行大量 LLM 请求。

当前机器上用户自己启动的 API：

```text
uvicorn metaclass.main:app --host 127.0.0.1 --port 8000
```

该进程没有 `--reload`，所以启动后写入的新代码不会影响当前任务。用户明确要求不要代替他启动或关闭服务；后续 Codex 不要擅自 kill/restart。

另外，不要把 `.env` 中的任何 API key 写入聊天、文档、测试输出或提交。

## 12. 旧 QA Bank 策略

旧策略位于 `question_bank/generator.py`，其实际流程是：

```text
所有 PresentationPlan 页面
→ 每 8 页一个 Batch
→ 全部 8 种学生画像分别扫描每个 Batch
→ 每个画像每页生成 0–2 个问题
→ 同页字符串级去重
→ 每页最多保留 4 个候选
→ 老师每 8 个问题生成一次答案
→ Controller 审核插入位置
→ 按 Batch 保存
→ ClassroomPlan 再从中选约 ceil(slide_count / 2) 个问题
```

当前配置大致为：

```text
STUDENT_SLIDES_PER_BATCH = 8
STUDENT_CONTEXT_WINDOW = 3
qa_student_concurrency = 3
qa_candidates_per_slide = 4
LLM timeout = 360 秒，瞬态失败可重试一次
```

24页最坏规模：

```text
3个页面 Batch
× 每批8次学生画像请求
+ 每批最多4次老师答案请求
+ 每批1次 Controller 请求
≈ 最多39次 LLM 请求
```

主要缺陷：

1. 用户选哪些学生只在创建 Session 时传入，QA 备课仍使用全部8种画像。
2. 先大规模生成再筛选，浪费绝大部分调用。
3. 只有同页、规范化后完全相同的问题会去重，语义重复无法去除。
4. 没有全课程互动预算、章节预算或核心 claim 优先级。
5. 老师回答每批重复携带整套 PPT、完整讲稿和 LearningContent。
6. Controller 失败时默认全部批准，不能作为严格质量门。
7. fallback 会为失败页面强行补模板题，失败越多越机械。
8. QA 的 source refs 是 slide section 的宽泛引用，不一定精确支撑答案。
9. ClassroomPlan 的教师检查题和学生 QA 独立生成，可能在同一页发生重复互动。
10. Job 内部有批次保存，但前端进度仍长期停留在 20%。
11. QA Bank 同时承担预设课堂脚本和真实用户问题检索，职责混杂。

## 13. 工作区已有的 QA 重构 WIP

注意：当前未提交 diff 已经开始将策略改成“先选教学节点再生成问题”，不能把它误当成尚未实现的纯设计。

已加入：

1. `interaction_temperature: 0..1`：目前用于表达互动密度，而不是模型采样温度。
2. 三条路线共享参数：
   - generated PresentationPlan job
   - source deck PresentationPlan job
   - paper workflow request / paper deck course
3. `_select_interaction_nodes()`：
   - `target = round(2 + temperature * 6)`，目标约2–8个节点。
   - 排除封面、目录、致谢、参考文献等页面。
   - 根据 key points、讲稿长度、visual payload 和关键词确定性打分。
   - 根据互动密度控制页面间距。
4. `_generate_node_batch()`：只为选中节点生成问题。
5. `_node_candidates()`：一次 LLM 调用为每个节点生成一道问题，并选择一个最适合的学生画像。
6. paper deck 保存 plan 后会调用共享 `PresentationService.prepare_question_bank()`。

这版 WIP 的价值是把复杂度从“页数 × 8画像”降到“2–8个节点”。但它尚未成为完整的新架构。

## 14. QA WIP 尚未解决的问题

下一位 Codex 应优先审查并补齐：

1. **缺少独立 InteractionPlanningJob**：QA 仍同步阻塞 PresentationPlan 或 paper deck course。
2. **缺少真实进度和 checkpoint**：节点选择、生成、验证、保存没有独立状态机。
3. **缺少 InteractionBlueprint**：现在直接从 `SlidePlan` 跳到 `QuestionCandidate`，没有稳定保存教学目标、互动意图、方向、importance 和证据要求。
4. **教师问学生与学生问教师仍未统一规划**：原 `_prepare_teacher_check_actions()` 仍是另一套逻辑。
5. **缺少严格 Validator**：没有检查未来知识泄露、问题/答案证据、数字、语义重复、章节覆盖、互动节奏。
6. **节点评分过于关键词化**：需要结合 knowledge unit、section role、claim、figure 和 evidence strength。
7. **`interaction_temperature` 命名易误解**：建议改成 `interaction_intensity` 或封装成 `InteractionPolicy`。
8. **仍把所有8种画像提供给模型**：虽然不再每种画像单独调用，但还没有明确用户所选 roster 与可复用题库之间的关系。
9. **没有分离脚本互动和用户问答检索**。
10. **fallback 仍会为缺失节点补模板题**：建议允许少生成，不要为了达到数量牺牲质量。
11. **旧生成方法仍保留在 generator.py**：需要明确兼容期、删除策略或 feature flag，避免两套逻辑继续漂移。
12. **三路线测试不足**：需要 generated/source/paper 各自证明使用同一 selector/generator/validator。
13. **当前运行中的 API 没有加载 WIP**：修改后必须由用户自行重启，再做验证。

## 15. 推荐的目标 QA 架构

建议把 QA Bank 提升为统一的课堂互动规划器：

```text
generated/source_deck/paper_deck Adapter
→ InteractionPlanningContext
→ TeachingNodeSelector
→ InteractionBlueprint
→ QuestionGenerator
→ InteractionValidator
→ ScriptedInteractionBank
→ ClassroomPlan
```

统一输入：

```python
class InteractionPlanningContext:
    content_id: str
    presentation_plan_id: str
    mode: Literal["generated", "source_deck", "paper_deck"]
    duration_minutes: int
    slides: list[InteractionSlide]
    sections: list[InteractionSection]
    knowledge_units: list[InteractionKnowledgeUnit]
    evidence_index: EvidenceIndex
```

稳定中间契约：

```python
class InteractionBlueprint:
    id: str
    slide_id: str
    direction: Literal["student_to_teacher", "teacher_to_student"]
    intent: Literal[
        "concept_clarification",
        "concept_contrast",
        "mechanism_reasoning",
        "evidence_reading",
        "boundary_condition",
        "procedure_check",
        "application_transfer",
        "section_synthesis",
    ]
    teaching_goal: str
    knowledge_unit_ids: list[str]
    claim_ids: list[str]
    source_refs: list[SourceRef]
    preferred_agent_types: list[StudentAgentType]
    importance: float
```

推荐策略：

1. 确定性筛除封面、目录、过渡、致谢和参考文献页。
2. 基于知识单元重要度、认知难度、claim、figure、section 边界和证据强度评分。
3. 只保留约 `目标节点数 × 2` 的候选。
4. 一次 LLM 全局选择最终节点与互动意图。
5. Validator 检查数量、章节覆盖、间距、重复和引用合法性。
6. 一到两次 LLM 生成最终问题、答案和课堂口语。
7. 定量问题必须有精确 source refs；论文问题可额外绑定 claim/block/asset。
8. 不达标节点允许删除，不强制补齐。
9. Session 根据实际 roster 绑定发言学生；题库不因换学生而整体重做。
10. 真实用户提问改为直接检索内容、知识单元和 evidence index，不依赖预生成大量 QA 覆盖问题空间。

## 16. 推荐实施顺序

### P0：保护当前现场

1. 不停止用户的 API 进程。
2. 不直接修改当前 running Job 数据。
3. 检查并保存现有未提交改动，用户明确同意后再 commit。
4. 为新策略增加 feature flag 或 schema version，避免旧 QA 数据混入新结果。

### P1：先固定契约

1. 新增 `InteractionPolicy`。
2. 新增 `InteractionPlanningContext`。
3. 新增 `InteractionBlueprint`。
4. 明确 `ScriptedInteractionBank` 与检索索引的边界。
5. 将 `interaction_temperature` 重命名或提供兼容 alias。

### P2：完成选择器

1. 将现有 `_select_interaction_nodes()` 拆成独立组件。
2. 增加三路线 Adapter。
3. 加入 section/knowledge unit/claim/figure/evidence 信号。
4. 添加确定性单测：预算、间距、禁选页、章节覆盖、稳定性。

### P3：完成生成与验证

1. 一次批量生成最终节点问题和答案。
2. 增加证据、数字、未来页泄露、重复、语言和长度 Validator。
3. 将 teacher check 纳入统一 InteractionBlueprint。
4. fallback 只保留高质量、证据充分的少量节点。

### P4：Job、恢复和前端进度

1. 增加独立 `InteractionPlanningJob`。
2. 状态：`queued → selecting_nodes → generating → validating → succeeded/failed/paused`。
3. 原子 checkpoint。
4. 按节点或批次更新真实进度。
5. 允许课堂在互动尚未完成时选择无互动启动，而不是无限阻塞。

### P5：三路线验收

至少覆盖：

- generated：普通 PDF → LearningContent → 新 PPT → QA → 课堂。
- source deck：原 PPT/PDF → 逐页讲稿 → QA → 课堂。
- paper deck：论文 → 四 Skill → paper deck → QA → 课堂。

验收指标：

- 30分钟课程互动节点约4–6个。
- 不对每页生成问题。
- 总 LLM 请求控制在2–4次左右。
- 课堂创建在可接受时间内完成。
- 节点覆盖关键章节而非均匀机械抽页。
- 问题不使用未来页面知识。
- 数字和论文结论有精确证据。
- 学生问法自然，老师回答可直接播放。
- 三条路线消费同一数据契约。

## 17. 测试状态与建议命令

历史上在较早版本跑通过：

```text
255 passed, 1 skipped
```

但这是最新 QA WIP 和前端嵌入改动之前的结果，不能代表当前工作树完整通过。

前端最新生产构建已通过：

```bash
cd /Users/mirror/project/mirror/EduVerse_Classroom/apps/web
npm run build
```

QA 重构后建议先跑：

```bash
cd /Users/mirror/project/mirror/EduVerse_Classroom
metaclass_env/bin/python -m pytest \
  apps/api/tests/test_schemas.py \
  apps/api/tests/test_mvp_flow.py \
  apps/api/tests/test_paper_deck.py \
  -q
```

再跑完整后端测试。不要为了测试启动第二个 API 服务占用8000端口。

## 18. 下一对话建议直接使用的开场提示

可以把下面这段原样发给下一位 Codex：

> 请先阅读 `/Users/mirror/project/mirror/EduVerse_Classroom/docs/paper-workflow-handoff-2026-08-25.md` 和 `/Users/mirror/project/mirror/EduVerse_Classroom/docs/paper-teaching-mode-design.md`。当前分支是 `feature/yj`，存在未提交改动，不要 reset、不要推送、不要启动或停止用户进程。先检查当前 diff，重点评审 `question_bank/generator.py` 中已经存在的“先选教学节点再生成问题”WIP，然后按照交接文档第14–16节补齐统一 InteractionPlanningContext、InteractionBlueprint、Validator、Job/checkpoint 和三路线测试。开始编码前先向我汇报你对现状、缺陷和拟修改边界的理解。

## 19. 交接完成标准

下一阶段不应只做到“请求次数变少”，而应同时满足：

1. 三条路线共享一个互动规划契约。
2. 互动节点先于问题生成并可单独检查。
3. 教师检查和学生提问不再互相打架。
4. 课堂脚本 QA 与用户自由提问检索职责清楚。
5. 生成数量由时长和教学价值控制，不由页数乘学生画像控制。
6. 失败不会导致每页填充机械模板题。
7. Job 有真实进度、恢复和最大耗时边界。
8. 普通课件、原稿和论文各有真实验收样本。
9. 课堂仍走原链路，不新增论文专用课堂页面或运行时。
