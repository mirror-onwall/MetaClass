# lsq 修改日志

本文记录 lsq 围绕材料解析、多文档资料管理、PageUnderstanding 与 LearningContent 生成链路的改动，方便后续与 PPT 生成、课堂生成和前端同学对齐。

## 当前职责边界

本部分工作聚焦到 `LearningContent` 生成为止，不负责后续 PPT 真实生成、课堂动作流和视频生成。

当前边界：

```text
PDF / PPTX 上传
  -> Material 历史资料
  -> MaterialCollection 多文档资料集
  -> PageMetadata 标准化页面解析
  -> PageUnderstanding v2 单页素材分析
  -> LearningContent 多文档教学内容蓝图
```

后续同学使用边界：

```text
LearningContent
  -> PresentationPlan / PPT
  -> ClassroomPlan / ClassroomSession
  -> Video
```

核心原则：

- `PageMetadata` 是原始材料解析结果。
- `PageUnderstanding` 是单页教学素材分析。
- `LearningContent` 不是原始页面，也不是 PPT 页面，而是跨页、跨文档重组后的教学内容蓝图。
- `LearningContent.sections[]` 表示教学单元，不强制等于原材料页，也不强制等于 PPT 页。
- 所有重组后的内容必须保留 `source_refs` / `page_refs`，便于追溯原文和页面图片。

## 2026-07-14：支持 PDF/PPTX 多文件上传与批量解析

### 后端改动

原有接口只支持单文件上传解析：

```http
POST /api/v1/materials/process
```

本次新增批量解析接口：

```http
POST /api/v1/materials/batch-process
```

请求字段：

```text
files: 多个 PDF/PPTX 文件
```

返回结构：

```json
{
  "items": [
    {
      "material": {},
      "pages": []
    }
  ],
  "collection": {}
}
```

说明：

- 后端已有 PDF / PPTX 解析能力，本次主要补齐多文件入口。
- 每个文件都会保存为独立 `Material`。
- 每个文件解析后生成自己的 `PageMetadata[]`。
- 批量上传后自动创建一个 `MaterialCollection`，把本次上传的多个 `material_id` 组织到一起。
- 旧单文件接口保留，避免影响已有调用。

涉及文件：

```text
apps/api/src/metaclass/modules/materials/api.py
apps/api/src/metaclass/modules/materials/schemas.py
apps/api/tests/test_mvp_flow.py
```

### 前端改动

上传控件从单文件改为多文件：

```tsx
<input type="file" accept=".pdf,.pptx" multiple />
```

拖拽上传也支持多个文件。

前端 API 新增：

```ts
api.uploadMany(files)
```

涉及文件：

```text
apps/web/src/App.tsx
apps/web/src/shared/api.ts
apps/web/src/shared/types.ts
```

## 2026-07-14：历史资料库基础与 MaterialCollection

### Material 作为长期资产

为了支持后续“历史资料复用”，上传文件不再只被看作一次性任务输入，而是作为长期 `Material` 保存。

`Material` 新增字段：

```text
file_hash
```

用途：

- 标识文件内容。
- 为后续重复上传检测做准备。
- 支持历史资料库复用。

当前关系：

```text
Material
  -> PageMetadata[]
  -> PageUnderstanding[]

MaterialCollection
  -> material_ids[]
```

注意：`MaterialCollection` 只引用已有 `Material`，不复制原始文件和页面数据。

### 新增 MaterialCollection

新增资料集模型：

```text
MaterialCollection
  id
  title
  material_ids
  primary_material_id
  created_at
  updated_at
```

新增后端表：

```text
material_collections
```

新增接口：

```http
GET  /api/v1/materials
POST /api/v1/materials/collections
GET  /api/v1/materials/collections
GET  /api/v1/materials/collections/{collection_id}
```

涉及文件：

```text
apps/api/src/metaclass/modules/materials/models.py
apps/api/src/metaclass/modules/materials/repository.py
apps/api/src/metaclass/modules/materials/schemas.py
apps/api/src/metaclass/modules/materials/service.py
apps/api/src/metaclass/modules/materials/api.py
apps/api/src/metaclass/infrastructure/database.py
```

## 2026-07-14：上传解析改为异步任务，避免大文件请求超时

### 问题背景

多文件或大 PDF/PPT 解析耗时较长，前端直接等待同步接口时容易出现：

```text
流程暂停
请求超时，请检查后端服务或缩小材料后重试
```

原因是浏览器请求一直等待后端同步解析完成，超过前端 fetch timeout。

### 新增 MaterialProcessingJob

新增异步任务模型：

```text
MaterialProcessingJob
  id
  status: queued | running | succeeded | failed
  progress
  step
  message
  material_ids
  collection_id
  error
  created_at
  updated_at
```

新增接口：

```http
POST /api/v1/materials/processing-jobs
GET  /api/v1/materials/processing-jobs/{job_id}
GET  /api/v1/materials/processing-jobs/{job_id}/result
```

当前流程：

```text
前端上传文件
  -> 后端创建 Material 和 MaterialCollection
  -> 立即返回 job_id
  -> 后台逐个 parse material
  -> 前端轮询 job 状态
  -> succeeded 后获取 ProcessedMaterials
```

前端 `api.uploadMany()` 已改为自动走异步任务，不需要页面调用方关心 job 细节。

## 2026-07-14：LearningContent 生成改为异步任务

### 问题背景

解析完成后仍可能在构建学习内容时报超时。日志中可以看到解析任务已经成功：

```text
GET /api/v1/materials/processing-jobs/{job_id}/result 200 OK
```

但后续 `LearningContent` 生成会逐页调用 LLM / 视觉模型 / 全局组织器，材料大时仍会超时。

### 新增 ContentGenerationJob

新增异步任务模型：

```text
ContentGenerationJob
  id
  material_id
  collection_id
  status: queued | running | succeeded | failed
  progress
  step
  message
  content_id
  error
  created_at
  updated_at
```

单材料接口：

```http
POST /api/v1/materials/{material_id}/learning-content-jobs
GET  /api/v1/learning-content-jobs/{job_id}
GET  /api/v1/learning-content-jobs/{job_id}/result
```

多文档资料集接口：

```http
POST /api/v1/material-collections/{collection_id}/learning-content-jobs
GET  /api/v1/learning-content-jobs/{job_id}
GET  /api/v1/learning-content-jobs/{job_id}/result
```

前端 `api.buildContent()` 已改为异步任务。

前端新增：

```ts
api.buildCollectionContent(collectionId)
```

当前页面逻辑：

```text
如果上传结果中有 MaterialCollection:
  构建 LearningContent 时优先使用 collection_id
否则:
  使用单个 material_id
```

## 2026-07-14：PageUnderstanding v2 单页素材分析

### 设计目标

原有 `PageUnderstanding` 主要是：

```text
summary
knowledge_points
teaching_focus
possible_questions
quiz_items
```

这对简单逐页讲解够用，但不利于多文档融合和高质量 LearningContent 生成。

本次将它升级为“单页素材分析器”。

### 新增字段

`PageUnderstanding` / `PageUnderstandingDraft` 新增：

```text
page_role
title
teachable_points
key_excerpts
concepts
formulas
visual_analysis
misconceptions
relations
```

含义：

- `page_role`：判断页面是封面、目录、概念、方法、公式、例子、数据、总结、参考文献等。
- `teachable_points`：本页可教学的知识点，带重要性和难度。
- `key_excerpts`：精选原文、定义、结论、公式文本、例子或数据。
- `concepts`：本页涉及的概念及定义。
- `formulas`：公式结构化信息。
- `visual_analysis`：页面图表、流程图、公式排版等视觉分析。
- `misconceptions`：可能误解点及纠正方式。
- `relations`：页面间依赖、承接和同主题关系。

数据库表 `page_understandings` 已补充对应字段，并在旧 SQLite 数据库中自动补列。

## 2026-07-14：LearningContent 升级为教学内容蓝图

### 顶层结构升级

`LearningContent` 保留旧字段：

```text
id
material_id
title
objectives
sections
version
created_at
updated_at
```

新增多文档和教学蓝图字段：

```text
material_ids
collection_id
subtitle
audience
teaching_intent
material_overview
global_concepts
generation_guidance
quality
```

说明：

- `material_id` 保留，用于兼容旧的 PPT / classroom / video 链路。
- `material_ids` 表示本次 LearningContent 实际使用的所有材料。
- `collection_id` 指向多文档资料集。
- `material_overview` 用于说明原材料整体结构。
- `generation_guidance` 给后续 PPT/课堂生成器提供约束，例如不要机械一页材料对应一页 PPT。
- `quality` 用于记录缺失信息、覆盖度、风险提醒等。

数据库表 `learning_contents` 已新增对应字段，并在旧 SQLite 数据库中自动补列。

### LearningSection 升级

`LearningContent.sections[]` 不再被视为页面，而是教学单元。

新增字段：

```text
role
content_goal
key_points
teaching_narrative
source_excerpts
formulas
examples
visual_opportunities
misconceptions
interaction_opportunities
transition
page_refs
```

说明：

- `role` 表示该教学单元的功能，例如 motivation / concept / method / formula / example / comparison / summary。
- `content_goal` 表示该 section 承担的教学任务。
- `key_points` 可供后续 PPT 生成 bullet 候选。
- `teaching_narrative` 是给教师或脚本生成器的讲解思路，不是最终逐字稿。
- `source_excerpts` 保存精选证据，而不是保存全文。
- `page_refs` 标记该教学单元主要依据哪些材料页。
- `source_refs` 仍保留，保证兼容已有课堂和 PPT 链路。

核心原则：

```text
LearningContent.sections[] != 原材料页面
LearningContent.sections[] != PPT 页面
LearningContent.sections[] = 教学逻辑单元
```

## 2026-07-14：多文档 LearningContent 生成

### 当前实现

新增 collection 级生成流程：

```text
MaterialCollection
  -> material_ids
  -> 每个 material 的 PageMetadata
  -> 每个 material 的 PageUnderstanding
  -> organize_collection_learning_content
  -> LearningContent
```

如果 provider 支持：

```python
organize_collection_learning_content(...)
```

则优先调用该方法，由 LLM 根据多个文档的 PageUnderstanding 统一组织教学内容。

如果 provider 不支持或生成失败，则使用 fallback：

```text
按知识点主题粗分组
  -> 合并多个材料中的 PageUnderstanding
  -> 生成可追溯 sections
```

fallback 目标不是最终高质量融合，而是保证多文档链路稳定跑通。

### LLM prompt 调整

`LLMLearningProvider` 新增 collection 级 prompt，要求：

- 不按源文件组织。
- 不按原始页面组织。
- 按教学逻辑组织。
- 去除多文档重复内容。
- 每个 section 保留 `page_refs` 和 `source_refs`。
- 输出适合后续 PPT/课堂使用的 LearningContent 蓝图。

## 当前前端行为

上传多个文件后：

```text
api.uploadMany(files)
  -> POST /materials/processing-jobs
  -> poll job
  -> result.items + result.collection
```

页面保存：

```text
materials
materialCollection
material = 第一个 material
pages = 第一个 material 的 pages
```

构建 LearningContent 时：

```text
如果 materialCollection 存在:
  api.buildCollectionContent(materialCollection.id)
否则:
  api.buildContent(material.id)
```

注意：

- 页面仍主要展示第一个材料的页面图。
- LearningContent 已经可以基于 collection 生成。
- 后续如果要展示多文档来源，需要新增调试视图或材料库视图。

## 当前验证结果

后端全量测试通过：

```text
53 passed
```

前端构建通过：

```text
npm run build
```

新增/覆盖的关键测试：

- 批量上传 PDF + PPTX。
- 批量上传自动创建 MaterialCollection。
- MaterialProcessingJob 可轮询并返回结果。
- LearningContent 单材料 job 可轮询并返回结果。
- MaterialCollection LearningContent job 能使用多个 material。
- LearningContent 结果包含 `collection_id`、`material_ids`、`page_refs`。

## 当前未完成但后续建议继续推进

### 1. 优化多文档组织 prompt

当前已有 collection 级 prompt，但还需要继续调优：

- 更稳定地产生 `teaching_intent`。
- 更稳定地区分 section role。
- 减少重复 section。
- 提高 `source_excerpts` 质量。
- 明确哪些内容适合公式页、例子页、流程图页。

### 2. 做 KnowledgeUnit / Canonical KnowledgeUnit

当前多文档融合是 `PageUnderstanding -> LearningContent`。

更完整的下一阶段是：

```text
PageUnderstanding
  -> KnowledgeUnit
  -> 跨文档相似度召回
  -> LLM 判断 same_as / extends / prerequisite_of / example_of
  -> Canonical KnowledgeUnit
  -> Course Knowledge Tree
  -> LearningContent
```

### 3. 做历史资料库 UI

当前后端已经有历史资料基础：

- `Material`
- `file_hash`
- `MaterialCollection`

但前端还没有独立历史资料库页面。

后续可补：

- 查看历史材料。
- 搜索历史材料。
- 选择历史材料加入当前 collection。
- 根据 `file_hash` 提示重复上传。

### 4. 增加 LearningContent 调试视图

建议前端后续增加：

- 原始 PageMetadata 查看。
- PageUnderstanding v2 查看。
- LearningContent sections 查看。
- source_excerpts / page_refs 查看。
- 多文档来源覆盖情况查看。

这对汇报和验收很有用。

## 当前主要涉及文件

```text
apps/api/src/metaclass/infrastructure/database.py
apps/api/src/metaclass/infrastructure/providers/learning.py
apps/api/src/metaclass/modules/content/api.py
apps/api/src/metaclass/modules/content/models.py
apps/api/src/metaclass/modules/content/repository.py
apps/api/src/metaclass/modules/content/schemas.py
apps/api/src/metaclass/modules/content/service.py
apps/api/src/metaclass/modules/materials/api.py
apps/api/src/metaclass/modules/materials/models.py
apps/api/src/metaclass/modules/materials/repository.py
apps/api/src/metaclass/modules/materials/schemas.py
apps/api/src/metaclass/modules/materials/service.py
apps/api/tests/test_mvp_flow.py
apps/api/tests/test_repositories.py
apps/web/src/App.tsx
apps/web/src/shared/api.ts
apps/web/src/shared/types.ts
```

不建议提交：

```text
logs/api.err.log
.tmp/
```

## 2026-07-14：补充 LearningContent 素材保底提取

### 背景

`LearningContent` 新结构要求 sections 中尽量包含：

```text
source_excerpts
concepts
teachable_points
page_refs
```

但在以下情况下这些字段可能为空：

- fake provider 只返回旧版 summary / knowledge_points。
- 真实 LLM 输出不稳定，没有给 `key_excerpts`。
- 某些页面内容简单，模型没有主动抽取概念。

如果这些字段为空，后续 PPT / 课堂同学拿到的 `LearningContent` 仍然会像“空大纲”，缺少可用素材。

### 本次补充

在 `ContentService._understanding_from_draft()` 中增加保底逻辑：

```text
如果 draft.key_excerpts 为空:
  从 PageMetadata.raw_text 中截取一段代表性文本
  生成 SourceExcerpt
  绑定 page.source_refs

如果 draft.concepts 为空:
  从 draft.knowledge_points 生成 ConceptNote

如果 draft.teachable_points 为空:
  从 draft.knowledge_points 生成 TeachingPoint
```

这样无论真实 LLM 是否稳定返回新版字段，系统都能保证：

- `PageUnderstanding` 至少有基础素材。
- `LearningContent.sections[].source_excerpts` 尽量不为空。
- 每个 excerpt 都能追溯到原始 `source_refs`。

### 测试补充

在 collection 级 LearningContent 测试中增加断言：

```python
assert any(section["source_excerpts"] for section in payload["sections"])
```

验证结果：

```text
2 passed
```

涉及文件：

```text
apps/api/src/metaclass/modules/content/service.py
apps/api/tests/test_mvp_flow.py
docs/change_list_lsq.md
```

## 2026-07-14：兼容 LLM 公式变量的多种输出格式

修复 LearningContent 生成过程中 `FormulaNote.variables` 因模型返回字符串列表而校验失败的问题。

以下模型输出现在都会被统一为标准的变量对象列表：

```json
["u", "v", "θ"]
"v"
{"n_v": "number of samples"}
```

标准化结果示例：

```json
[
  {"symbol": "u", "meaning": ""},
  {"symbol": "v", "meaning": ""}
]
```

该兼容处理位于 schema 输入边界，不改变 LearningContent 对外的标准结构。

## 2026-07-14：LearningContent 组织进度展示

LearningContent 异步任务不再只从 10% 跳到 100%，后端现在按真实处理阶段更新：

```text
准备材料
-> 逐页理解
-> 提取 KnowledgeUnit
-> 跨文档语义融合
-> 组织 LearningContent
-> 保存结果
```

前端轮询 job 时会接收最新 `progress`、`step` 和 `message`，并在等待层显示百分比进度条、
当前处理阶段以及页面处理数量。已有 PageUnderstanding 缓存时，逐页理解阶段会快速完成。

## 2026-07-14：Canonical KnowledgeUnit 语义融合第一版

### 统一多文档生成主链路

collection 级 LearningContent 现在固定经过知识单元层：

```text
PageUnderstanding[]
  -> 原始 KnowledgeUnit[]
  -> 本地相似度候选合并
  -> LLM 语义合并与关系判断
  -> Canonical KnowledgeUnit[]
  -> LearningContent
```

LLM 全局 organizer 不再绕过 KnowledgeUnit。无论最终使用 LLM organizer 还是 fallback，
`LearningContent.knowledge_units` 都会保存本次生成实际使用的规范化知识单元。

### KnowledgeUnit 字段升级

新增字段：

```text
aliases
source_unit_ids
relations
confidence
```

其中：

- `source_unit_ids` 记录规范化知识单元由哪些原始知识单元合并而来。
- `relations` 支持 `prerequisite_of`、`extends`、`example_of`、`contrasts_with`、`related_to`。
- `confidence` 记录语义合并或关系判断的置信度。
- `source_refs`、`page_refs`、`source_excerpts` 仍由后端从原始单元汇总，避免 LLM 伪造来源。

### 两阶段融合策略

第一阶段使用本地标题和关键词相似度合并明显重复项，减少 LLM 输入规模。

第二阶段由 `LLMLearningProvider.canonicalize_knowledge_units()` 判断语义相同项及知识关系。
LLM 只返回合并分组和关系，不负责生成或改写来源引用。

如果 LLM 语义融合失败，系统会继续使用本地合并结果，并在
`LearningContent.quality.warnings` 中记录降级信息。

### 验证

新增测试覆盖：

- 相似标题知识单元的本地合并。
- 跨文档 `source_unit_ids`、`page_refs` 保留。
- `example_of` 关系保存。
- LLM canonicalization JSON 解析。
- collection 端到端生成仍包含 KnowledgeUnit、来源摘录和页面引用。

验证结果：

```text
55 passed
ruff check: passed
frontend npm run build: passed
```

涉及文件：

```text
apps/api/src/metaclass/modules/content/schemas.py
apps/api/src/metaclass/modules/content/service.py
apps/api/src/metaclass/infrastructure/providers/learning.py
apps/api/tests/test_mvp_flow.py
apps/api/tests/test_services.py
apps/web/src/shared/types.ts
docs/change_list_lsq.md
```

## 2026-07-14：CourseKnowledgeTree 与树驱动 LearningContent

本轮暂不实现历史资料库 UI，聚焦完善 LearningContent 生成质量。

### CourseKnowledgeTree

新增课程知识树结构：

```text
CourseKnowledgeTree
  -> root_node_ids
  -> nodes[]
       -> parent_id
       -> knowledge_unit_ids
       -> prerequisite_node_ids
       -> order
  -> teaching_sequence
```

知识树节点引用 Canonical KnowledgeUnit，不复制原始页面内容。知识树作为 JSON 随
LearningContent 持久化，旧 SQLite 数据库会自动补充 `knowledge_tree` 列。

### 统一单材料和多材料生成链路

单材料和 MaterialCollection 现在统一使用：

```text
PageUnderstanding
-> Canonical KnowledgeUnit
-> CourseKnowledgeTree
-> LearningContent
```

LearningSection 新增 `tree_node_ids`。LLM organizer 必须覆盖全部顶层知识树节点并保留
`page_refs`，否则使用基于知识树的 fallback。fallback 按教学章节聚合知识单元，不再机械地
一个 KnowledgeUnit 生成一个 section，同时保留 quiz_items 以兼容课堂链路。

### 自动质量检查

`LearningContent.quality` 新增或补充：

```text
coverage_score
material_coverage
knowledge_coverage
missing_material_ids
orphan_unit_ids
duplicate_section_titles
sections_without_sources
low_confidence_unit_ids
low_confidence_relation_count
conflict_count
warnings
```

### 查看接口与前端视图

新增接口：

```http
GET /api/v1/learning-contents/{content_id}/knowledge-tree
GET /api/v1/learning-contents/{content_id}/diagnostics
```

前端右侧 LearningContent 区域新增“大纲 / 知识树 / 质量”视图，可查看章节结构、知识单元数量、
覆盖率和质量警告。

## 2026-07-14：新增轻量 KnowledgeUnit 层

### 背景

为了实现最终的多文档知识融合目标，`LearningContent` 不应该直接从页面或 PageUnderstanding 拼接出来。

更合理的中间层是：

```text
PageUnderstanding
  -> KnowledgeUnit
  -> 合并 / 去重 / 排序
  -> LearningContent.sections[]
```

这样可以逐步从“按页理解”过渡到“按知识单元组织”，符合当前原则：

```text
LearningContent.sections[] 不是原材料页，也不是 PPT 页，而是教学逻辑单元。
```

### 新增结构

新增 schema：

```text
KnowledgeUnit
  id
  title
  unit_type
  summary
  keywords
  concepts
  source_excerpts
  formulas
  examples
  misconceptions
  source_refs
  page_refs
  importance
```

`LearningContent` 顶层新增：

```text
knowledge_units
```

数据库表 `learning_contents` 同步新增：

```text
knowledge_units JSON
```

旧 SQLite 数据库会自动补列并填充 `[]`。

### 当前生成逻辑

在 fallback collection 生成链路中：

```text
PageUnderstanding[]
  -> _knowledge_units_from_understandings()
  -> _merge_knowledge_units()
  -> LearningSection[]
```

其中：

- 每个 PageUnderstanding 会先被转为一个 KnowledgeUnit。
- KnowledgeUnit 带上 `source_excerpts`、`source_refs`、`page_refs`。
- 后端会用标题归一化做第一版粗合并。
- 合并后按教学角色排序：

```text
motivation
concept
method
formula
example
case
comparison
summary
reference
```

这不是最终的语义融合算法，但已经把结构从“页面拼接”推进到“知识单元组织”。

### 验证

collection 级 LearningContent 测试新增断言：

```python
assert payload["knowledge_units"]
assert payload["knowledge_units"][0]["source_excerpts"]
```

验证结果：

```text
1 passed
```

涉及文件：

```text
apps/api/src/metaclass/modules/content/schemas.py
apps/api/src/metaclass/modules/content/models.py
apps/api/src/metaclass/modules/content/repository.py
apps/api/src/metaclass/modules/content/service.py
apps/api/src/metaclass/infrastructure/database.py
apps/api/tests/test_mvp_flow.py
apps/web/src/shared/types.ts
docs/change_list_lsq.md
```
