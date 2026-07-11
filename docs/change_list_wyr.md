# Wyr Change List

> 目的：记录 `wyr` 分支涉及的前后端改动与接口约定。任何会影响调用方、数据结构、运行环境或演示流程的修改，都在本文件追加一条，方便定位联调问题和合并冲突。

## 使用规则

- 修改接口路径、请求字段、响应字段、枚举值或默认行为时，先更新本文件，再通知对应负责人。
- 不记录密钥、令牌、个人路径和上传材料内容；只记录接口契约与运行要求。
- 同一功能若前后端同时改动，分别写明文件和兼容性，不要只写“已联调”。
- 合并 `main`、其他成员分支或解决冲突后，补一条“合并记录”，说明采用了哪个版本的接口。

## 当前联调契约

### 材料与内容

| 功能 | 前端调用 | 当前行为 | 影响方 |
| --- | --- | --- | --- |
| 上传并解析 | `POST /api/v1/materials/process` | 接收 `.pdf` / `.pptx`，返回 `material` 与 `pages` | 前端、材料模块 |
| 重新解析 | `POST /api/v1/materials/{id}/parse` | 返回逐页 `PageMetadata` | 前端、材料模块 |
| 构建学习内容 | `POST /api/v1/materials/{id}/learning-content` | 返回 `LearningContent` 与章节、来源页码 | 前端、内容模块 |
| 页面图片 | `GET /api/v1/materials/{id}/pages/{pageNo}/image` | 用于逐页预览和课堂展示 | 前端、材料模块 |

### 课堂与视频

| 功能 | 前端调用 | 当前行为 | 影响方 |
| --- | --- | --- | --- |
| 创建课堂 | 先创建 `classroom-plan`，再 `POST /api/v1/classroom-plans/{id}/sessions` | 支持 `lecture` 与 `interactive` 模式 | 前端、课堂模块 |
| 教学动作 | `POST /api/v1/classroom-sessions/{id}/next` | 返回 `TeachingAction`，前端按 `type` 渲染 | 前端、课堂模块 |
| 智能体轮次 | `POST /api/v1/classroom-sessions/{id}/agent-turns/next` | 返回调度决策与智能体发言 | 前端、课堂模块 |
| 提问/答题 | `questions` / `answers` 接口 | 更新 session、反馈与掌握度 | 前端、课堂模块 |
| 视频生成 | `POST /api/v1/learning-contents/{id}/videos` | 当前同步完成任务后返回结果 | 前端、视频模块 |
| 视频播放 | `GET /api/v1/videos/{resultId}/download` | 返回 MP4，前端直接使用 `<video>` 播放 | 前端、视频模块 |

## 变更记录

### 2026-07-11 - 对齐 yj 的学生智能体选择契约

- 修改文件：`apps/api/src/metaclass/modules/classroom/agent_schemas.py`、`apps/api/src/metaclass/modules/classroom/schemas.py`、`apps/web/src/App.tsx`、`apps/web/src/shared/types.ts`、`apps/api/tests/test_schemas.py`。
- `student_agent_types` 的公开枚举值统一为小写 snake_case：`classroom_atmosphere_regulator`、`deep_thinker`、`note_taker`、`researcher`、`foundation_weak`、`silent_observer`、`concept_confused`、`practical_applier`。
- 默认行为：不传或传空数组 `[]` 时，后端均初始化前四位默认学生；显式选择时最多支持八位学生。
- 兼容性：后端仍接受旧客户端传来的全大写值，并可读取旧 session 中保存的全大写角色值；新接口与 yj 文档保持一致。
- 前端：互动模式默认选中四位默认学生，并提供八张可选角色卡片；空选后创建课堂时由后端回退为默认四位。
- 验证：`apps/api/tests/test_schemas.py` 与 `apps/api/tests/test_mvp_flow.py` 共 25 项通过；`apps/web` 的 `npm run build` 通过。

### 2026-07-11 - 互动课堂学生智能体选择

- 初版新增开课前的学生智能体多选配置，仅在 `interactive` 模式显示；初版默认选择“深度思考者”和“课堂笔记员”，最多四位，后续已按上方记录调整。
- 前端将选择结果通过创建 session 请求的 `student_agent_types` 字段发送；课堂创建后右侧显示本堂已启用学生。
- 后端 `CreateClassroomSessionRequest`、课堂 API 与 `ClassroomService.create_session` 支持可选角色列表，并只初始化选择的学生状态。
- 历史说明：此版本之后，空列表的行为已调整为回退四位默认学生，详见上方对齐记录。
- 新增四张纸张拼贴风头像资源，位于 `apps/web/src/assets/agents/`。

### 2026-07-11 - 合入 `main` 的课堂自动对话

- 合入提交 `1c8db91`（经 `087e578` 合并到 `main`），未发生冲突。
- 新增课堂自动播放师生对话相关的前端状态、API 调用和后端课堂服务逻辑。
- 影响文件包括 `apps/web/src/App.tsx`、`apps/web/src/shared/api.ts`、`apps/web/src/shared/types.ts` 及课堂模块后端代码。
- 联调注意：后续修改课堂自动播放、Agent 发言或 session 响应结构时，需要同时核对 `agent-turns/next` 与新增自动对话相关字段，避免前端显示逻辑和后端响应脱节。

### 2026-07-11 - 建立分支变更追踪文档

- 新增本文件，作为 `wyr` 分支的接口与联调变更入口。
- 当前分支已合入当时最新 `origin/main`；本地 `wyr` 比 `origin/wyr` 领先 3 个提交，尚未推送。
- 后续合并其他分支前，先对照上方“当前联调契约”，确认 API 路径、字段与枚举是否仍兼容。

### 2026-07-10 - 逐页讲解与视频播放前端

- 新增 `apps/web/src/features/video/SlideNarrationPlayer.tsx`。
- 讲解播放器根据 `PageMetadata` 与 `LearningContent.sections[].source_refs.page_no` 对应当前页面和讲解文本。
- 本地预览配音使用浏览器 `speechSynthesis`，不依赖后端 TTS 或模型调用；不支持的浏览器会显示提示。
- `apps/web/src/App.tsx` 在未进入课堂时展示逐页讲解播放器；视频生成完成后直接播放下载接口返回的 MP4。
- 风险：浏览器语音的音色和可用性由客户端决定；后端视频当前仍使用 `FakeTTSProvider`，并非真实语音。

### 2026-07-10 - 合入课堂智能体与学习模式

- 合入 `origin/main` 中的课堂智能体、LLM provider 与 `lecture` / `interactive` 模式改动。
- 前端增加 `agent-turns/next` 调用和智能体发言展示；课堂状态与 `TeachingAction` 的响应结构成为前后端联调关键。
- 当前默认 `METACLASS_LLM_PROVIDER=fake`。若切换真实兼容模型，需同时确认 `base_url`、模型名、密钥环境变量和返回 JSON 格式。

## 已知环境依赖

- PDF 解析使用 PyMuPDF；可提取有文本层的 PDF 并渲染页面图片。
- PPTX 解析使用 `python-pptx`；PPTX 页面渲染依赖系统可执行的 LibreOffice `soffice`。缺失时会退化为占位图片，联调前应在 Win/Mac 均检查。
- 本地前端通过 Vite 代理访问 `http://127.0.0.1:8000`；部署到不同域名时需设置 `VITE_API_BASE` 或配置反向代理。

## 追加模板

每次修改请复制以下区块，按日期倒序追加在“变更记录”顶部：

```md
### YYYY-MM-DD - 简短标题

- 负责人：姓名/分支
- 改动文件：`path/to/file`
- 变更：说明新增、删除或调整的行为。
- 接口影响：`无` / 写出路径、请求字段、响应字段、兼容策略。
- 联调状态：`未测` / `本地通过` / `待某成员确认`。
- 回滚或注意事项：需要时填写。
```
