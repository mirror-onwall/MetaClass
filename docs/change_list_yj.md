# yj 修改日志

本文记录 yj 近期围绕 classroom 自动课堂、多 agent 互动和前端对接的改动，方便后续和前端同学协调。

## 补充记录：本次检查后新增在前面的重点

以下是对当前修改记录的补充，主要补上之前容易被忽略、但后续联调时很重要的细节。

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
