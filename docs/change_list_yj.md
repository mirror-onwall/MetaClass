# yj 修改日志

本文记录 yj 近期围绕 classroom 自动课堂、多 agent 互动和前端对接的改动，方便后续和前端同学协调。

## 补充记录：本次检查后新增在前面的重点

以下是对当前修改记录的补充，主要补上之前容易被忽略、但后续联调时很重要的细节。

### 新增：A/B 边界和 OpenMAIC-style 异步课堂计划任务

现在 A 同学的链路只负责到 `LearningContent`，这个边界是合理的：

```text
A: PPT/PDF -> PageMetadata -> PageUnderstanding -> LearningContent
B: LearningContent -> ClassroomPlan -> ClassroomSession/Event -> Teacher/Student Agents
```

B 侧已经补了一个参考 OpenMAIC 的异步计划生成入口。旧同步接口仍然保留，前端当前不需要被迫改调用方式。

新增后端表：

```text
classroom_plan_jobs
```

新增接口：

```http
POST /api/v1/learning-contents/{content_id}/classroom-plan-jobs
GET  /api/v1/classroom-plan-jobs/{job_id}
```

`POST` 会返回 202 和 `ClassroomPlanJob`：

```json
{
  "id": "plan_job_xxx",
  "content_id": "content_xxx",
  "status": "queued",
  "step": "queued",
  "progress": 0,
  "message": "课堂计划生成任务已创建",
  "plan_id": null,
  "error": null
}
```

`GET` 用于轮询任务状态：

```text
queued -> running/planning -> running/persisting -> succeeded/completed
```

如果成功，`plan_id` 会指向生成好的 `ClassroomPlan`；如果失败，`error` 会给出错误信息。这个设计的意义是：以后真实 LLM 生成课堂计划耗时较长时，前端可以像 OpenMAIC 那样创建任务并轮询进度，而不是一直卡在一个同步请求里。

兼容性说明：

- 原来的同步接口 `POST /api/v1/learning-contents/{content_id}/classroom-plans` 没有删除。
- 当前前端如果继续用旧接口，仍然可以跑通。
- 后续如果要优化体验，可以改成先调 `classroom-plan-jobs`，轮询成功后再用 `plan_id` 创建课堂 session。

### 新增：Presentation 模块，承接 PPT 生成和每页讲稿

新增独立后端模块：

```text
apps/api/src/metaclass/modules/presentation/
```

这个模块不塞进 `classroom`，而是作为 `LearningContent` 后面的独立中间层：

```text
LearningContent
  -> PresentationPlan
      -> slides[]
      -> slide_id
      -> source_section_ids
      -> title
      -> key_points
      -> speaker_script
      -> suggested_visual
  -> PPT skill 生成真实 pptx

PresentationPlan
  -> 后续可被 ClassroomPlan 引用
```

当前已经实现：

- `PresentationPlanGenerator`：调用 LLM 根据 `LearningContent` 生成每页 PPT 规划和每页讲稿。
- `PPTSkillAdapter`：预留未来调用 PPT skill 的入口。
- `presentation_plans` 表：保存中间层 `PresentationPlan`。
- `ppt_generation_jobs` 表：记录 PPT 生成任务。
- `ppt_artifacts` 表：记录 PPT 生成产物或 skill 请求文件。

新增接口：

```http
POST /api/v1/learning-contents/{content_id}/presentation-plans
GET  /api/v1/learning-contents/{content_id}/presentation-plan
GET  /api/v1/presentation-plans/{plan_id}
POST /api/v1/presentation-plans/{plan_id}/ppt-jobs
GET  /api/v1/ppt-jobs/{job_id}
GET  /api/v1/ppt-jobs/{job_id}/artifact
GET  /api/v1/ppt-artifacts/{artifact_id}
GET  /api/v1/ppt-artifacts/{artifact_id}/skill-request
GET  /api/v1/ppt-artifacts/{artifact_id}/download
```

当前 PPT job 的行为：

```text
PresentationPlan
  -> PPTSkillAdapter.prepare_request()
  -> data/generated/presentations/{job_id}/deck.pptx
  -> data/generated/presentations/{job_id}/slides/slide_001.png
  -> data/generated/presentations/{job_id}/slides/slide_002.png
  -> data/generated/presentations/{job_id}/speaker_scripts.json
  -> data/generated/presentations/{job_id}/skill_request.json
  -> job.status = finished
```

也就是说，现在已经能用项目现有 `python-pptx` 生成一版基础真实 `.pptx`，同时把 PPT 每页渲染成课堂可展示的 PNG 图片，并保留结构化 `skill_request.json`。之后等更高级的 PPT skill 放进来，只需要替换：

```text
apps/api/src/metaclass/modules/presentation/skill_adapter.py
```

里的 `prepare_request()`，让它调用更强的 skill 渲染器即可。

PPT 页图片渲染技术路线：

```text
python-pptx 生成 deck.pptx
  -> 优先调用 soffice/LibreOffice 转成 PDF
  -> 使用 PyMuPDF(fitz) 把 PDF 每页渲染为 PNG
  -> 如果本机没有 soffice，则用 Pillow 生成基础预览占位图
```

新增/更新接口：

```http
GET /api/v1/ppt-artifacts/{artifact_id}/slides
GET /api/v1/ppt-artifacts/{artifact_id}/slides/{slide_no}/image
```

前端播放课堂时的显示规则：

```text
SHOW_PAGE
  -> 优先使用 /ppt-artifacts/{artifact_id}/slides/{page_no}/image
  -> 如果没有生成 PPT slide image，回退到原来的 /materials/{material_id}/pages/{page_no}/image
EXPLAIN / PROBE / REVIEW / REMEDIATE / END / agent 发言
  -> 不再把主屏切换成文字卡片
  -> 保留上一页 PPT 投影画面
ASK_QUIZ
  -> 仍然显示正式小测题，等待真实用户作答
```

当前短期对齐策略：

- `PresentationPlanGenerator` 先尽量保持“一个 LearningSection 对应一页生成 PPT”。
- 前端暂时用 `source_ref.page_no -> slide_no` 做映射。
- 后续如果允许 PPT 页合并/拆分，需要把 `SHOW_PAGE` 的 payload 扩展为直接携带 `slide_id` 或 `artifact_id + slide_no`。
- 默认课堂计划不再每页固定插入 `SUMMARIZE` / “本页小结”，让 PPT 按正常页序推进；`SUMMARIZE` 这个 ActionType 仍保留，后续可由 LLM controller 在确实需要阶段总结时触发。

### 试用后修复：PPT 中文乱码和互动消失

试用发现两个问题：

1. 如果本机 `soffice` 转 PDF 不可用，后端会走 Pillow fallback 生成 slide 图片；之前 fallback 使用 `ImageFont.load_default()`，不支持中文，导致生成 PPT 图片中文字乱码。
2. 去掉每页固定 `SUMMARIZE` 后，原来依赖 `SUMMARIZE` 触发的学生总结/插话入口也一起消失，互动课堂看起来没有 agent 对话。

已修复：

- `PPTSkillAdapter` 生成 PPT 时给文字指定 `PingFang SC`。
- fallback PNG 渲染会依次尝试系统中文字体：

```text
/System/Library/Fonts/PingFang.ttc
/System/Library/Fonts/STHeiti Medium.ttc
/System/Library/Fonts/Supplemental/Arial Unicode.ttf
/System/Library/Fonts/Supplemental/Songti.ttc
```

- 互动课堂现在在每页 `EXPLAIN` 执行后自动触发一轮自然互动：

```text
SHOW_PAGE
  -> EXPLAIN
  -> student agent 自然插话/提问/复述
  -> teacher agent 简短回应并拉回主线
  -> ASK_QUIZ 或 END
```

这个互动不会替用户回答正式小测，也不会把主屏 PPT 页面切走。

### 调整：按 ClassroomPlan 规划触发互动，取消每页固定对话

试用后发现“每页 EXPLAIN 后固定学生插话 + 老师回应”仍然太机械，和 OpenMAIC 的设计不一致。

已调整为：

```text
LearningContent
  -> LLM ClassroomPlanGenerator 生成轻量 ClassroomPlanBlueprint
  -> 后端 hydrate 成严格 ClassroomPlan
  -> Runtime 只在计划内 PROBE 动作之后触发学生/老师互动
```

现在的互动节奏由 `ClassroomPlan` 决定：

```text
SHOW_PAGE
  -> EXPLAIN
  -> 如果 plan 有 PROBE:
       PROBE action
       -> student agent 回应开放问题
       -> teacher agent 简短回应并拉回主线
  -> 如果 plan 有 ASK_QUIZ:
       用户小测
  -> END / 下一页
```

取消的机械规则：

- 不再每个 `EXPLAIN` 后固定触发学生插话。
- 不再每个 `ASK_QUIZ` 前固定让老师额外追问。
- 小测是否出现由 LLM planner 的 `include_quiz` 决定；即使上游提供了 `quiz_items`，也不要求每页都测。

当前 fake LLM 的本地开发行为：

- 第 1 页和每 3 页左右安排一次 `PROBE`，用于保证本地 demo 能看到互动。
- 多页材料不会每页都 `include_quiz=true`，避免“页页小测”。
- 真实 LLM 接入后，由 prompt 控制其按关键概念、易错点和阶段收束来安排互动/小测。

和 classroom 的关系：

- 当前没有强行改 `ClassroomPlan` 输入，所以不会影响现有前端课堂流程。
- 后续更理想的结构是让 `ClassroomPlan` 从 `PresentationPlan` 生成，这样 `SHOW_PAGE` 可以稳定指向某一页 PPT，`EXPLAIN` 可以直接用这一页的 `speaker_script`。
- 目前先保留 `LearningContent -> ClassroomPlan` 的旧链路，避免前端联调被打断。

### 新增：LLM ClassroomPlan 生成组件

新增后端组件：

```text
apps/api/src/metaclass/modules/classroom/planner.py
```

核心类：

```python
ClassroomPlanGenerator
```

用途：

- 根据 `LearningContent` 生成可运行的 `ClassroomPlan`。
- 优先调用 LLM 输出轻量课堂规划蓝图，而不是让 LLM 直接吐完整 `ClassroomPlan`。
- 后端根据蓝图和原始 `LearningContent` 组装完整 `ClassroomPlan` / `TeachingAction`。
- 使用 Pydantic 校验 LLM 蓝图和最终 `ClassroomPlan` 契约。
- 额外校验运行时关键约束：`ASK_QUIZ` 后必须紧跟 `GIVE_FEEDBACK`，且 `quiz_action_id` 必须指向前一个小测动作。
- 如果 LLM 输出非法、解析失败或结构不满足课堂运行要求，自动回退到原来的规则模板生成，避免课堂创建失败。

当前外部 API 不变：

```http
POST /api/v1/learning-contents/{content_id}/classroom-plans
```

也就是说前端暂时不需要改调用方式。变化发生在后端内部：

```text
LearningContent
  -> ClassroomPlanGenerator
  -> LLM 生成轻量 blueprint
  -> 后端 hydrate 成 ClassroomPlan
  -> Pydantic / runtime 校验
  -> 保存 ClassroomPlan
```

修复记录：

- 早期版本让 LLM 直接生成完整 `ClassroomPlan`，需要复制大量 `source_refs` 和 quiz payload，真实 API 容易超时并导致 `/classroom-plans` 返回 500。
- 现在改为“LLM 只决定课堂结构，后端负责填充契约字段”，减少 token 和输出长度。
- `TimeoutError` 会触发回退，不再让课堂创建接口直接 500。
- 自动课堂中 `SHOW_PAGE`、`EXPLAIN`、`PROBE`、`REVIEW`、`REMEDIATE`、`END` 等普通计划动作不再先询问 LLM controller，避免每个动作都卡一次模型请求。
- 如果 plan 已经安排了 `PROBE`，下一步到 `ASK_QUIZ` 时不会再额外生成一次老师开放提问，避免重复提问和小测前卡顿。

需要和 PDF/PPT 解析同学对齐的接口重点：

- `LearningContent.sections[].title`：LLM planner 会用它作为 scene 标题。
- `LearningContent.sections[].summary`：LLM planner 会用它生成讲解、总结、回顾等动作。
- `LearningContent.sections[].knowledge_points`：后续可用于决定是否插入 `PROBE` / `REVIEW` / `REMEDIATE`。
- `LearningContent.sections[].source_refs`：必须稳定、完整，planner 不允许捏造 source ref；`SHOW_PAGE` 和讲解动作都依赖它。
- `LearningContent.sections[].quiz_items`：正式小测只能从这里选择，不由 planner 临时编造。

联调建议：

- PDF/PPT 解析链路要保证每个 section 至少有一个 `source_ref`。
- 如果希望课堂自动生成更好的小测，需要上游在 `quiz_items` 中提供题目、选项、正确答案、解释和知识点。
- 如果某页不适合出题，可以让 `quiz_items=[]`，planner 会跳过正式小测。

### 试用后修复：小测等待时保留题目

试用时发现一个前端状态问题：

```text
ASK_QUIZ 已经展示
  -> auto-step 返回 waiting
  -> 前端把 action 清成 null
  -> 小测卡片消失
  -> 用户无法作答，课堂无法继续
```

已在 `apps/web/src/App.tsx` 修复：

- 当 `auto-step` 返回 `status === "waiting"` 且 `session.waiting_for === "quiz_answer"` 时，前端会保留当前 `ASK_QUIZ` action。
- 小测题不会因为进入等待状态而从页面消失。
- 这个修复同时适用于互动课堂和连续讲解模式。

当前前端逻辑：

```ts
setAction((currentAction) => {
  if (result.action) return result.action;
  if (result.status === "waiting" && result.session.waiting_for === "quiz_answer") {
    return currentAction;
  }
  return null;
});
```

### 试用后修复：答题后不立即清空课堂画面

连续讲解模式下还发现一个体验问题：

```text
用户提交小测答案
  -> answer() 返回评估结果
  -> 前端 setAction(null)
  -> 当前画面瞬间变成等待态
```

已在 `apps/web/src/App.tsx` 修复：

- 用户提交答案后，不再立刻清空当前 action。
- 答题后小测卡片和反馈会短暂停留。
- 自动播放恢复后，下一次 `auto-step` 再切换到后续课堂内容。

这让连续课堂和互动课堂在小测后的过渡都更稳定。

### LLM 配置入口

新增后端集中配置文件：

```text
apps/api/src/metaclass/core/llm_config.py
```

用途：

- `VLLM_CONFIG`：配置 OpenAI-compatible LLM，例如智增增、阿里云百炼中转站等。
- `EMBEDDING_CONFIG`：预留 embedding 模型配置，目前检索链路暂未接入。
- `GENERATION_CONFIG`：配置生成参数，例如 `temperature` 和 `max_tokens`。

后端应用装配点 `apps/api/src/metaclass/core/application.py` 已改为通过：

```python
get_llm_runtime_config()
```

统一读取 LLM 配置，再构造 `build_llm_provider(...)`。

配置方式：

- 可以直接改 `llm_config.py`。
- 也可以用项目根目录 `.env` 覆盖。
- 默认 `provider=auto`：没有 API key 时使用 fake，有 API key 时使用 OpenAI-compatible。

`.env.example` 已同步更新相关说明。

### 自动课堂不是后台长任务

当前自动课堂仍然是“前端定时调用后端单步接口”的实现：

```text
前端 setTimeout/useEffect
  -> POST /api/v1/classroom-sessions/{session_id}/auto-step
  -> 后端只推进一个自然课堂节拍
  -> 前端根据返回结果决定继续、暂停或等待用户
```

也就是说：

- 后端不会自己在后台无限循环。
- 刷新页面后需要重新从当前 session 状态恢复 UI。
- 后续如果要做更真实的实时课堂，可以把 `auto_step()` 复用为 SSE/WebSocket 的事件生成核心。

### 正式小测和 agent 对话的边界

当前规则是：

- `ASK_QUIZ` 是正式小测，只能由真实用户作答。
- 学生 agent 可以回答老师的开放式提问。
- 学生 agent 不会替用户提交 `/answers`。
- 当 `session.waiting_for === "quiz_answer"` 时，`auto-step` 返回 `waiting`，前端自动暂停。

这个边界很重要，避免互动课堂看起来“学生 agent 把用户的小测也做了”。

### 前端自动播放的状态控制

`apps/web/src/App.tsx` 里新增了两类状态：

```ts
autoPlaying
autoStepInFlight
```

用途：

- `autoPlaying` 控制自动课堂开始/暂停。
- `autoStepInFlight` 防止前端定时器重复发起并发 `auto-step` 请求。
- 自动播放时禁用“单步推进”和“智能体下一轮”，避免多个入口同时改同一个 session。
- 遇到正式小测等待、课堂完成、接口报错时，自动播放会暂停。

### 前端按钮行为变化

原来的“执行下一步”现在拆成：

```text
开始/暂停自动课堂
单步推进
智能体下一轮（仅互动课堂模式显示）
```

建议联调时重点检查：

- 连续讲解模式下不应该出现学生 agent 插话。
- 互动课堂模式下自动播放会在小测前产生老师开放提问、学生回答、老师回应。
- 自动播放过程中手动按钮应被禁用，暂停后可以手动推进。

### 学生 agent 设计期望的前端调用方式

当前后端已经有学生 agent 画像设计，核心目标是让前端可以在创建互动课堂前选择“这节课有哪些学生智能体参与”。

目前默认策略：

- 如果前端没有传学生 agent 配置，后端默认挂载 4 个学生画像。
- 默认 4 个是：
  - `classroom_atmosphere_regulator`：课堂气氛调节者，负责轻松提问、生活化类比。
  - `deep_thinker`：深度思考者，负责追问原因、提出反例。
  - `note_taker`：课堂笔记员，负责提炼知识点、整理笔记。
  - `researcher`：研究型同学，负责追问场景、引导讨论。

后续前端推荐交互：

```text
创建课堂前
  -> 用户选择学习方式
    -> 连续讲解模式
    -> 互动课堂模式
  -> 如果选择互动课堂模式，显示“选择学生智能体”
  -> 用户可以勾选不同类型学生
  -> 如果不选，使用默认 4 个
  -> 创建 ClassroomSession
```

建议前端 UI：

```text
[ ] 课堂气氛调节者
[ ] 深度思考者
[ ] 课堂笔记员
[ ] 研究型同学
[ ] 基础薄弱型同学
[ ] 沉默观察者
[ ] 概念混淆型同学
[ ] 实践应用型同学
```

学生类型说明：

```text
课堂气氛调节者：活跃气氛、降低课堂压力，适合让课堂不冷场。
深度思考者：深入追问、挑战理解，适合推动概念理解。
课堂笔记员：总结重点、整理信息，适合辅助学习者形成结构化笔记。
研究型同学：持续探究、连接应用，适合把知识迁移到真实场景。
基础薄弱型同学：提出基础问题，适合暴露学习门槛和易错点。
沉默观察者：低频发言，只在关键处表达困惑，适合模拟真实课堂中的安静学生。
概念混淆型同学：故意暴露典型误解，适合触发老师纠错和澄清。
实践应用型同学：关注怎么用、在哪里用，适合把课堂拉向应用案例。
```

理想 API 形态建议：

```http
POST /api/v1/classroom-plans/{plan_id}/sessions
```

请求体可以扩展为：

```json
{
  "mode": "interactive",
  "student_agent_types": [
    "classroom_atmosphere_regulator",
    "deep_thinker",
    "note_taker",
    "researcher"
  ]
}
```

如果前端不传 `student_agent_types`，或者传空数组：

```json
{
  "mode": "interactive"
}
```

后端应使用默认 4 个学生画像。

注意：截至本日志补充时，后端已经有 `StudentAgentProfile` / `StudentAgentState` / 默认画像等基础结构，但“前端自选学生类型并传给 create session”这一步可以作为下一轮接口扩展继续做。

### 新增/调整测试覆盖点

`apps/api/tests/test_mvp_flow.py` 新增了自动课堂回归测试，覆盖：

```text
SHOW_PAGE
EXPLAIN
teacher PROBE
student answer
teacher reply
ASK_QUIZ
waiting for user quiz answer
user answer updates mastery
```

检查命令：

```bash
metaclass_env/bin/pytest -q apps/api/tests
metaclass_env/bin/ruff check apps/api/src apps/api/tests
npm --prefix apps/web run build
```

最近一次检查结果：

```text
28 passed
ruff passed
frontend build passed
```

### 当前还没做但后续建议做

- 刷新页面后，从 `session.events` 恢复右侧 agent 对话历史。
- 给自动课堂增加速度设置，例如慢速/正常/快速。
- 把 `auto-step` 返回的 `status` 再细分，例如 `teacher_probe`、`student_answer`、`teacher_reply`。
- 用户答错正式小测后，自动触发补救讲解 `REMEDIATE`。
- 真实 LLM 接入后，按角色区分模型配置：controller / teacher / student。

## 当前状态

- 已实现课堂自动播放入口，不再依赖用户手动连续点击“下一步”。
- 已实现互动课堂中的自动师生对话链：老师提问、学生 agent 回答、老师回应。
- 小测题仍由真实用户完成，学生 agent 不代答正式小测。
- 用户可随时输入问题，老师 agent 会生成回答并记录事件。
- 当前实现仍保持同步 HTTP 轮询式 `auto-step`，后续可演进为 SSE 或 WebSocket 事件流。

## 后端改动

### `apps/api/src/metaclass/modules/classroom/schemas.py`

- 新增 `AutoClassroomStep`。
- 用于自动课堂每一步的统一返回结构。
- 返回内容可能包含：
  - `status`：当前自动步状态。
  - `action`：本步执行的 `TeachingAction`。
  - `directed_turn`：本步生成的老师/学生/评价器 agent 发言。
  - `feedback`：前端可直接展示的提示或老师回复。
  - `session`：更新后的课堂 session。

当前 `AutoClassroomStep.status` 包括：

```text
action
agent_turn
quiz_answered
waiting
completed
```

其中 `quiz_answered` 目前主要作为兼容状态保留；自动课堂不会替用户答题。

### `apps/api/src/metaclass/modules/classroom/api.py`

新增接口：

```text
POST /api/v1/classroom-sessions/{session_id}/auto-step
```

用途：

- 前端自动播放课堂时循环调用。
- 每次调用只推进一个自然课堂节拍。
- 这个接口以后可以作为 SSE/WebSocket 事件流的核心服务方法复用。

### `apps/api/src/metaclass/modules/classroom/service.py`

主要新增和调整：

- 新增 `auto_step(session_id)`。
- 新增自动对话续接逻辑。
- 保留手动 `next`、`answer`、`questions` 等旧接口。

自动课堂主流程：

```text
auto_step
  -> 如果 session completed，返回 completed
  -> 如果 waiting_for=quiz_answer，返回 waiting，等待用户答题
  -> 如果 interactive 模式，尝试生成自动师生对话
  -> 如果没有对话要继续，推进下一个 TeachingAction
```

自动师生对话逻辑：

```text
老师 PROBE
  -> 学生 agent 回答
  -> 老师回应学生
  -> 回到课堂主线
```

正式小测前的行为：

```text
即将进入 ASK_QUIZ
  -> 老师先提出一个开放式理解检查问题
  -> 学生 agent 简短回答
  -> 老师回应并拉回主线
  -> 执行正式 ASK_QUIZ
  -> 前端暂停，等待用户答题
```

重要约束：

- 学生 agent 可以插话、提问、总结、回答老师开放问题。
- 学生 agent 不替用户完成正式 `ASK_QUIZ`。
- 如果上一轮已经是 agent 发言，系统会避免无限 agent 循环，下一轮会按对话状态回应或回到教学动作。

### `apps/api/src/metaclass/modules/classroom/agents/teacher.py`

- 调整 fake teacher 的本地演示行为。
- 没有真实 LLM key 时，也能区分：
  - 老师发起开放式提问。
  - 老师回应学生发言。
  - 老师普通推进课堂。

真实 LLM 接入后，`TeacherAgent.generate_turn()` 会走 provider 生成结构化 JSON。

### `apps/api/tests/test_mvp_flow.py`

新增/调整自动课堂回归测试：

- 自动课堂能推进 `SHOW_PAGE` 和 `EXPLAIN`。
- 正式小测前会触发老师开放提问。
- 学生 agent 会回答老师问题。
- 老师会回应学生。
- 随后执行 `ASK_QUIZ`。
- 遇到正式小测后返回 `waiting`，等待用户答题。
- 用户提交答案后，掌握度正常更新。

当前验证结果：

```text
pytest -q apps/api/tests
28 passed
```

## 前端改动

### `apps/web/src/shared/types.ts`

- 新增 `AutoClassroomStep` 类型。
- 与后端 `AutoClassroomStep` 对齐。

前端需要关注字段：

```ts
status
action
directed_turn
feedback
session
```

### `apps/web/src/shared/api.ts`

新增 API client 方法：

```ts
autoStep(sessionId: string)
```

对应后端：

```text
POST /api/v1/classroom-sessions/{session_id}/auto-step
```

### `apps/web/src/App.tsx`

新增自动播放状态和循环：

- `autoPlaying`
- `autoStepInFlight`
- `useEffect` 定时调用 `autoStep()`

当前行为：

- 创建课堂后自动开始播放。
- 点击“暂停自动课堂”可以暂停。
- 点击“开始自动课堂”可以继续。
- 自动播放中禁用手动 agent 下一轮按钮，避免状态冲突。
- 遇到小测等待时自动暂停。
- 用户提交小测答案后，自动播放继续。

需要前端同学注意：

- `result.action` 用于渲染正式课堂动作。
- `result.directed_turn` 用于渲染老师/学生 agent 对话。
- `result.status === "waiting"` 且 `session.waiting_for === "quiz_answer"` 时，应显示小测并等待用户操作。
- 用户提问接口仍是：

```text
POST /api/v1/classroom-sessions/{session_id}/questions
```

## 课堂事件关系

计划动作执行时记录：

```text
ActionExecutedEvent
```

用户提交小测后记录：

```text
QuizEvaluatedEvent
```

用户自由提问后记录：

```text
UserQuestionEvent -> TeacherAnswerEvent
```

自动师生对话记录：

```text
AgentTurnEvent -> AgentTurnEvent -> AgentTurnEvent
```

例如：

```text
teacher PROBE
student answer
teacher reply
```

## 推荐前端展示方式

前端可以把课堂内容分成两条展示线：

- 主投影区：展示 `TeachingAction`，例如页面、讲解、小测、小结。
- 对话区：展示 `directed_turn.turns`，例如老师提问、学生回答、老师回应。

建议不要把 agent 对话和正式小测混在一个 UI 层里：

- agent 对话是课堂自然互动。
- `ASK_QUIZ` 是用户要完成的正式测验。

## 仍需协调的问题

- 是否要把 `ControllerDecision` 扩展成更细粒度事件，例如 `teacher_probe`、`student_answer`、`student_question`、`teacher_reply`。
- 是否要把自动课堂从 HTTP 轮询升级为 SSE/WebSocket。
- agent 对话是否需要独立时间轴 UI。
- 用户答错后是否自动触发 `REMEDIATE`。
- 真实 LLM 接入后，是否区分 controller、teacher、student 的模型配置。
- 是否为前端提供完整 event history 接口，方便恢复刷新后的对话区。

## 当前未提交文件

截至本日志创建时，本轮修改主要涉及：

```text
apps/api/src/metaclass/modules/classroom/agents/teacher.py
apps/api/src/metaclass/modules/classroom/api.py
apps/api/src/metaclass/modules/classroom/schemas.py
apps/api/src/metaclass/modules/classroom/service.py
apps/api/tests/test_mvp_flow.py
apps/web/src/App.tsx
apps/web/src/shared/api.ts
apps/web/src/shared/types.ts
```

## 2026-07-12：补充 plan generation meta 与 TTS artifact 接口

新增 ClassroomPlan 生成元信息记录，用来判断某个课堂规划到底来自 LLM 还是 fallback：

```text
GET /api/v1/classroom-plans/{plan_id}/generation-meta
```

返回字段包括：

```text
plan_id
content_id
source              # llm 或 fallback
provider            # fake / openai-compatible / none
model
fallback_reason
raw_response        # LLM 原始 JSON 字符串
parsed_blueprint    # 解析后的轻量蓝图
created_at
```

说明：

- 新生成的 ClassroomPlan 会自动写入 generation meta。
- 如果 LLM 成功，`source = "llm"`。
- 如果 LLM 超时、返回非 JSON、结构校验失败，或者没有配置 LLM，`source = "fallback"`，并记录 `fallback_reason`。
- 旧的 ClassroomPlan 由于之前没有记录 meta，可能查不到该接口。

新增 TTS artifact 标准接口，前端可以把任意课堂文本统一转成可播放音频：

```text
POST /api/v1/tts-artifacts
GET  /api/v1/tts-artifacts/{artifact_id}
GET  /api/v1/tts-artifacts/{artifact_id}/audio
```

请求示例：

```json
{
  "text": "这一页我们讲解大模型工具服务搭建。",
  "scope": "slide_script",
  "ref_id": "slide_001",
  "voice": "teacher"
}
```

返回字段包括：

```text
id
text
scope
ref_id
voice
audio_url
duration_ms
duration_seconds
created_at
```

建议前端使用方式：

- PPT 页面讲稿：`scope = "slide_script"`，`ref_id = slide_id`。
- 老师 agent 发言：`scope = "teacher_turn"`，`ref_id = event_id/action_id`。
- 学生 agent 发言：`scope = "student_turn"`，`ref_id = event_id`。
- 小测反馈：`scope = "quiz_feedback"`，`ref_id = quiz_action_id`。
- 用户提问后的老师回答：`scope = "teacher_answer"`，`ref_id = question_event_id`。

自动播放逻辑建议从固定计时器改成：

```text
拿到课堂动作/agent 发言
-> 请求 TTS artifact
-> 播放 artifact.audio_url
-> audio ended 后再请求下一次 auto-step
```

## 2026-07-12：优化课堂规划、学生智能体互动与小测生成

调整 ClassroomPlan planner prompt：

- 不再鼓励每页固定“讲解 + 提问 + 小测”。
- 要求 planner 先判断每个 section 在整节课中的作用：封面/目录/过渡页、新概念页、易混淆页、案例/结果页、阶段总结页。
- `include_probe` 只在有讨论价值、能暴露误区或连接例子的地方打开。
- `probe_question` 必须具体指向本页内容，避免“你理解了吗”这类泛泛提问。
- `include_quiz` 只在关键概念、易错点、阶段收束处少量出现；多页内容通常每 2-4 个 section 最多 1 次。

调整老师/学生 agent prompt：

- 学生发言必须尽量贴着当前页、当前知识点或最近老师/同学说的话。
- 学生不必每次都提问，可以是困惑、复述、轻微吐槽、走神、要求例子或短反馈。
- 课堂气氛调节者允许偶尔像真实学生一样说“有点无聊”“我想上厕所”“这个例子突然听懂了”，但不能每次都搞笑，也不能离课堂太远。
- 老师需要能接住这类真实课堂插话，先回应情绪，再一句话拉回当前页内容。

调整小测生成：

- 以前的小测是在 `ContentService.build()` 里机械生成的：

```text
问题：本页主要讲解的内容是？
选项：核心知识点 / 以上内容均未出现
```

- 现在改为在 PageUnderstanding 阶段由 LLM 生成 `quiz_items` 草稿。
- `LearningContent.sections[].quiz_items` 会优先使用 LLM 生成的小测。
- 如果 LLM 没给小测，后端才生成一个保底题。
- 新增 `PageUnderstanding.quiz_items` 字段，并落库到 `page_understandings.quiz_items`。
- 旧 SQLite 数据库会自动补 `quiz_items = []`，不需要手动迁移。

需要和 A 同学对齐：

- A 的 LearningContent 生成链路现在最好把每页的 `quiz_items` 一起生成。
- 每个 quiz item 应包含：

```text
question
options
correct_index
explanation
knowledge_point
```

- 小测设计目标不是问“本页讲了什么”，而是检查概念区分、条件、因果、应用判断或常见误区。

## 2026-07-12：优化 PPT 生成字体，降低跨平台预览乱码概率

修复背景：

- 之前下载 `deck.pptx` 本身通常正常，但网页课堂区展示的 slide image 可能乱码。
- 原因多半出在 `soffice --headless` 把 PPTX 转 PDF/PNG 时，运行机器缺少对应中文字体。
- 原实现主要使用 macOS 的 `PingFang SC`，别的组员在 Windows/Linux 上重新生成预览图时容易字体替换失败。

本次调整：

- PPT 生成时不再只设置 `paragraph.font.name`。
- 现在会同时设置：

```text
latin font
eastAsia / CJK font
complex script font
```

- 按运行平台选择字体：

```text
macOS:   Arial + PingFang SC
Windows: Arial + Microsoft YaHei
Linux:   DejaVu Sans + Noto Sans CJK SC
```

- placeholder PNG 预览图的 PIL 字体搜索路径也加入了 Linux/Windows/macOS 常见字体：

```text
Noto Sans CJK / Noto Sans SC
WenQuanYi Micro Hei / WenQuanYi Zen Hei
Microsoft YaHei / SimHei / Arial
PingFang / STHeiti / Arial Unicode / Songti / Helvetica
```

注意：

- 这个修复会影响“新生成”的 PPT artifact。
- 已经生成过的旧 `data/generated/presentations/.../slides/*.png` 不会自动刷新。
- 如果组员拉代码后网页预览仍乱码，需要重新生成 PPT job。
- Linux 环境最好安装 `Noto Sans CJK SC` 或其他中文字体，否则 LibreOffice 渲染中文仍可能 fallback 不理想。

补充修复：

- 进一步排查发现，部分网页预览图并不是 LibreOffice 成功转换出来的，而是 `soffice --headless --convert-to pdf` 失败后进入了 PIL fallback renderer。
- fallback renderer 之前只是直接 `draw.text(...)`，没有按框宽换行，所以会出现“下载 PPT 正常，但网页图中文字冲出框/压到别的元素上”的问题。
- 现在 `soffice` 转 PDF 时会给 LibreOffice 单独设置临时 user profile：

```text
-env:UserInstallation=file://...
```

- 这样可以避免 LibreOffice headless 复用/锁住默认 profile 导致导出 PDF 失败。
- 如果仍然失败，会在对应 PPT job 目录写入：

```text
render_error.txt
```

- fallback PNG renderer 也补了手动换行和行距控制，标题、key points、visual direction 都会按框宽截断/换行，不再直接画出框。

## 2026-07-12：按前端选择的学生智能体类型创建和调度课堂 agent

修复背景：

- 前端已经支持选择 8 类学生智能体，并在创建课堂 session 时传：

```json
{
  "mode": "interactive",
  "student_agent_types": ["foundation_weak", "concept_confused"]
}
```

- 后端原本会创建选择的学生列表，但 controller prompt 仍假设固定顺序：

```text
student_agent_001 = 课堂气氛调节者
student_agent_002 = 深度思考者
student_agent_003 = 课堂笔记员
student_agent_004 = 研究型同学
```

- 如果用户只选择“基础薄弱型同学、概念混淆型同学”，这个假设会错位。

本次调整：

- `student_agent_types is None` 时仍使用默认四个学生：

```text
课堂气氛调节者
深度思考者
课堂笔记员
研究型同学
```

- 前端显式传入列表时，后端严格按该列表和顺序创建 `student_states`。
- 前端显式传入 `[]` 时，表示当前 session 没有学生 agent，不再自动回默认四个。
- ClassroomController prompt 改为读取当前 `available_students`，不再写死 `student_agent_001/002/003/004` 的含义。
- 如果 LLM controller 返回了不存在的 `next_agent_id`，后端会自动纠正为当前 roster 里的第一个学生。
- 如果当前 roster 为空，controller 不允许选择 student，会转为 teacher 推进。
- 计划内 `PROBE` 选择学生时改为按类型优先：

```text
deep_thinker
concept_confused
foundation_weak
researcher
practical_applier
classroom_atmosphere_regulator
note_taker
silent_observer
```

新增测试：

- 未传 `student_agent_types` 时默认四个。
- 显式传空列表时保持空 roster。
- API 创建 session 时传 `foundation_weak + concept_confused`，后端 session 只包含这两个类型，并在计划内 probe 时优先选择概念混淆型同学。

## 2026-07-12：修复用户自由提问时老师没有针对问题回答

修复背景：

- 前端提问框会调用：

```text
POST /api/v1/classroom-sessions/{session_id}/questions
```

- 但后端 `TeacherAgent.answer_question()` 之前没有真正使用用户输入的 `question` 生成答案，只是固定返回：

```text
根据当前材料：{当前页讲解文本}
```

- 所以前端看起来像“老师没有回答用户的问题，只是在重复默认讲解”。

本次调整：

- 新增教师自由答疑 prompt：

```text
build_teacher_answer_messages(...)
```

- 有 LLM provider 时，老师会基于：

```text
user_question
current_explanation
ClassroomState
recent_events
source_refs
```

生成针对用户问题的回答。

- 返回结构仍然保持原接口兼容：

```text
ControllerResult.status = "answered"
ControllerResult.feedback = teacher_answer.answer
ControllerResult.source_refs = 当前页 source refs
```

- FakeLLMProvider 也补了对应分支，本地测试/无真实 key 时也会把用户问题纳入回答。
- fallback 逻辑也改成至少会引用用户问题：

```text
你问的是“...”。结合当前材料，这里可以这样理解：...
```

新增测试：

- 端到端测试确认用户输入 `"How does it work?"` 后，返回的 `feedback` 中包含该问题文本。
