# 开题设计评审

## 结论

整体架构合理，适合作为课程项目，但应缩小首版范围。设计中最有价值的三项决定是：

1. 用 `LearningContent` 隔离 PPT/PDF、主题生成等不同输入源。
2. 用 `ClassroomPlan + TeachingAction` 表达可执行课堂，而不是让 Agent 自由聊天。
3. 区分静态 `ClassroomPlan` 与动态 `ClassroomSession/StudentState`。

这些抽象能够支撑材料学习、主题学习和课堂运行，也符合 OpenMAIC 的“先生成课程结构和场景，再由状态机编排多 Agent 互动”的总体方向。

## 与 OpenMAIC 的关系

OpenMAIC 当前公开实现更准确的描述是：

- 课程生成采用两阶段管线：输入分析并生成 outline，再将 outline 扩展为 slide、quiz、simulation、PBL 等 scene。
- 课堂运行采用基于状态机的多 Agent 编排，由 director/controller 决定发言者和动作。
- 前端通过动作执行引擎渲染语音、白板、聚光、指示等多种课堂行为。

因此，本项目的 `Read -> Plan -> Run` 是合理的工程归纳，但不宜在报告中表述为 OpenMAIC 官方命名的固定三阶段架构。更稳妥的说法是：

> 借鉴 OpenMAIC 的两阶段课程生成与状态机式多智能体编排，将本项目工程流程归纳为 Read、Plan、Run。

## 需要调整的设计

### 1. 缩小两周 MVP

首版只保证一条纵向闭环：

```text
上传材料 -> 页面解析 -> LearningContent -> ClassroomPlan
-> 规则 Controller -> Teacher + Evaluator -> StudentState
```

AI 同学首版保留一个角色即可；TA 可以先由普通服务生成总结，不必每个能力都包装成 Agent。视频只实现“页面图片 + TTS + 字幕 + MP4”。主题生成 PPT、向量检索和 Presenter 材料重构作为第二优先级。

### 2. 不要把所有 LLM 调用都叫 Agent

`TopicOutlineAgent`、`DocumentAgent`、`PPTAgent` 如果只是一次结构化生成，更适合命名为 generator/service。Agent 应保留给具备状态、工具或多轮决策的运行时角色。这样能减少无意义的多智能体复杂度。

### 3. TeachingAction 应使用可校验的联合类型

不要使用一个任意结构的 `value` 字段承载全部动作。每个 action payload 应有独立 schema，并至少包含：

- `id`、`scene_id`、`type`、`actor`
- 类型化 `payload`
- `status`、`created_at`
- 可选的 `source_refs`，用于追踪讲解依据

运行时插入的 `AnswerUser` 不应修改原始计划；它应作为 session event 追加到事件流中。

### 4. StudentState 要标明“不确定性”

掌握度不能仅凭 LLM 主观生成。MVP 可结合小测得分、回答评价和交互证据计算，并保存 evidence。界面应称为“估计掌握度”，避免把 0.65 显示成精确测量。

### 5. 长任务需要明确边界

FastAPI `BackgroundTasks` 适合演示，但进程重启会丢任务。目录中保留 `jobs` 边界；MVP 可用数据库状态 + 单进程 worker，之后再换 RQ/Celery，而不改 API。

### 6. 材料解析需保留来源追踪

`PageMetadata` 和生成内容应保存页码、文本块或图片区域等 `source_refs`。否则问答、讲稿和诊断难以解释，也无法发现模型编造。

## 建议验收标准

- 一份 10–20 页的标准 PDF/PPTX 能稳定解析，失败页可定位并重试。
- 所有 LLM 结构化输出都通过 Pydantic/JSON Schema 校验。
- 课堂在用户提问、答错、要求举例、上一页/下一页时不会丢失状态。
- 同一材料重复生成时可命中中间结果缓存。
- 无 LLM/TTS 密钥时，使用 fake provider 仍能跑通自动测试和 Demo 主流程。

## 开发顺序

1. 契约与 fake provider
2. 材料上传、解析、LearningContent
3. ClassroomPlan、规则 Controller、session event
4. 前端课堂页与状态恢复
5. Evaluator 与轻量学习诊断
6. 视频链路
7. Presenter 基础讲稿/提问
8. 主题生成、检索和 PPT 重构

