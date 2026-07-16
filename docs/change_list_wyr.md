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
| 实时语音 | `POST /api/v1/tts-artifacts`，再读取响应中的 `audio_url` | 请求携带 `text`、`scope`、`ref_id`、`voice`；教师和学生角色使用固定音色映射 | 前端、视频模块、TTS Provider |
| 视频生成 | `POST /api/v1/learning-contents/{id}/videos?presentation_artifact_id={id}` | 异步生成任务；使用已生成 PPT 画面和逐页 `speaker_script` 合成讲解视频 | 前端、演示文稿模块、视频模块 |
| 视频播放 | `GET /api/v1/videos/{resultId}/download` | 返回 MP4，前端直接使用 `<video>` 播放 | 前端、视频模块 |

## 变更记录

### 2026-07-16 - MiniMax Speech 2.8 云端语音与固定角色音色

- 负责人：Wyr / `wyr`。
- 改动文件：`apps/web/src/features/video/useTTSNarration.ts`、`apps/web/src/App.tsx`、`apps/api/src/metaclass/modules/video/api.py`、`apps/api/src/metaclass/modules/video/service.py`、`apps/api/tests/test_mvp_flow.py`。
- 变更：新增 MiniMax T2A v2 Provider，使用低延迟 `speech-2.8-turbo`，解析接口返回的十六进制 MP3 并统一转换为现有课堂和视频流程使用的 WAV。
- 变更：一轮课堂发言会并行预生成最多三条云端 TTS，并缓存已下载、解码的音频；后端对相同 Provider、文本、作用域、引用和角色音色复用稳定的语音产物，减少重复生成等待。
- 变更：课堂前端不再启用浏览器 `speechSynthesis`，只播放后端生成的云端语音。MiniMax 配置为芊芊老师和八类学生提供九种固定普通话音色，保证同一角色前后音色一致。
- 环境要求：设置 `METACLASS_TTS_PROVIDER=minimax`、`METACLASS_TTS_BASE_URL=https://api-bj.minimaxi.com`、`METACLASS_TTS_MODEL=speech-2.8-turbo`，并在 `METACLASS_TTS_API_KEY` 填入 MiniMax 官方 Key；本文档不记录实际密钥。
- 联调注意：使用 MiniMax 官方北京入口，不再经过智增增语音通道；`.env` 已被 Git 忽略，禁止把官方 Key 写入提交文件。
- 接口影响：路径和请求结构不变；`voice=teacher` 固定映射教师音色，`voice=student_{agent_type}` 按八种学生角色稳定映射学生音色列表。
- 联调状态：MiniMax 官方接口真实联调通过；教师测试短句 1.48 秒返回，八种学生音色测试短句分别约 0.67～0.91 秒返回。后端 Provider/缓存测试与前端生产构建均通过。

### 2026-07-16 - 智能体文字气泡稳定显示

- 负责人：Wyr / `wyr`。
- 改动文件：`apps/web/src/App.tsx`、`apps/web/src/features/video/useTTSNarration.ts`、`apps/web/src/styles.css`。
- 变更：连续师生发言切换时保留上一条字幕，下一位开始后直接替换，取消字幕短暂清空后回退旧反馈的状态跳转。
- 变更：语音播放期间稳定显示当前完整发言，不再每 100 毫秒按句切换；气泡使用固定高度和内部滚动，并取消重复入场位移动画，避免角色切换时页面抖动。
- 接口影响：无，仅调整前端字幕状态与布局。
- 联调状态：前端生产构建通过。

### 2026-07-16 - 学习内容模型输出兼容

- 负责人：Wyr / `wyr`。
- 改动文件：`apps/api/src/metaclass/infrastructure/providers/learning.py`、`apps/api/tests/test_services.py`。
- 变更：解析大模型生成的学习内容草稿时忽略 Schema 未声明的附加字段，避免模型在 `key_excerpts` 中偶发输出 `Color` 等展示字段后触发 `extra_forbidden`，导致“流程暂停”。
- 接口影响：公开响应结构不变；只丢弃未定义的模型附加字段，已声明字段仍按 Pydantic 类型校验。
- 联调状态：页面理解定向测试通过。

### 2026-07-16 - 合入最新 main

- 负责人：Wyr / `wyr`。
- 合并记录：合入 `main` 提交 `019cd4d`，包含 yj 的演示文稿生成与课堂播放改进，以及 lsq 的材料图片提取和 LearningContent 视觉候选改动。
- 兼容处理：保留 Wyr 的 TTS、语音视频与前端课堂联动实现；未修改 `.env` 中的个人模型配置。
- 联调状态：前后端已在本地启动并完成主要流程检查。

### 2026-07-14 - 真实 TTS、师生语音联动与 PPT 讲解视频

- 负责人：Wyr / `wyr`。
- 提交：`109caeb`（`feat: add synchronized classroom TTS and PPT video narration`）。
- 改动文件：TTS Provider、视频服务与 API、演示文稿服务、前端课堂播放和语音 Hook、共享 API 与类型，以及对应测试和 `.env.example`。
- 变更：接入 OpenAI-compatible TTS；PPT 逐页讲稿和 Teacher/Student Agent 发言均先生成文字，再按教师或学生角色转换成语音，前端按发言顺序同步显示头像、姓名、文字和声音。
- 变更：讲解视频改为使用系统生成的 PPT 页面图片，不再使用原 PDF 页面；每页画面与对应 `speaker_script` 教师配音合成为分段视频，最终输出 MP4 与字幕。
- 接口影响：新增 `POST /api/v1/tts-artifacts`、`GET /api/v1/tts-artifacts/{id}`、`GET /api/v1/tts-artifacts/{id}/audio`；视频生成请求增加可选查询参数 `presentation_artifact_id`，并继续通过视频任务接口轮询结果。
- 联调状态：TTS Provider、视频流程和前端构建测试通过，已于 2026-07-14 合入 `main`。

### 2026-07-12 - 黑板右侧发言席

- 新增 `apps/web/src/assets/agents/teacher-qianqian.png`，作为芊芊老师的纸雕头像。
- 修改 `apps/web/src/App.tsx` 与 `apps/web/src/styles.css`：黑板扩宽，并在有实时发言时展示“左侧 PPT + 右侧头像与气泡”的课堂发言席。
- 气泡根据当前发言者显示芊芊老师或对应学生头像与姓名；发言约 5.8 秒后自动收起，也支持手动关闭。右侧发言席的版位始终预留，确保 PPT 在发言前后保持同一尺寸。
- 移动端将发言席切换为 PPT 下方的紧凑横向头像与气泡布局。

### 2026-07-12 - 黑板顶部实时字幕

- 修改 `apps/web/src/App.tsx` 与 `apps/web/src/styles.css`：将原本位于黑板下方的智能体发言和教师反馈，合并为黑板内部、PPT 上方的实时讲台字幕带。
- 字幕带显示实际发言者名称，且位于投影页面外部，不会遮挡 PPT 内容；保留关闭按钮。
- 移除面向学生的 LLM 调度详情卡，避免课堂时将注意力拉到 PPT 下方。

### 2026-07-12 - 智能体发言显示角色姓名

- 修改 `apps/web/src/App.tsx`：课堂智能体发言不再显示内部 ID（如 `student_agent_002`）。
- 学生发言会根据当前 session 映射为“姓名 · 角色”，例如“浩浩 · 深度思考者”；教师统一显示为“芊芊老师”。
- 映射信息缺失时回退显示原始 ID，避免影响课堂发言的正常展示。

### 2026-07-12 - 学生头像性别表现修正

- 重绘 `apps/web/src/assets/agents/atmosphere-regulator.png`、`researcher.png`、`foundation-weak.png`、`practical-applier.png`、`silent-observer.png`。
- 凡凡、琪琪调整为男生；涵涵、包包、跳跳调整为女生；继续沿用纸雕便签、图钉和米白纸张的既有视觉风格。
- 教师智能体的前端发言和反馈展示名统一为“芊芊老师”；保留内部 `teacher` 标识，避免影响既有 API 兼容性。

### 2026-07-12 - 学生智能体悬浮档案

- 修改 `apps/web/src/App.tsx` 与 `apps/web/src/styles.css`：课堂同学选择卡在鼠标悬浮或键盘聚焦时，显示放大头像和同学档案。
- 档案包含姓名、性别、角色和课堂表现；悬浮预览不改变原有点击卡片选中/取消选中学生的行为。
- 角色名称：凡凡、浩浩、婧婧、涵涵、琪琪、跳跳、昊昊、包包。

### 2026-07-12 - 顶部课程进度刻度

- 修改 `apps/web/src/App.tsx` 与 `apps/web/src/styles.css`：移除左栏课程流程模块，左栏从课程材料和备课操作开始。
- 将流程收纳进顶部标题栏，使用中文当前阶段、`01 / 05` 计数和五段细刻度静态呈现，不再具备菜单或卡片的视觉暗示。
- 移动端保留简洁标题栏，避免压缩主要操作区域。

### 2026-07-11 - 根目录 LLM 环境配置

- 新增项目根目录 `.env`，配置 Qwen OpenAI-compatible LLM 与 embedding 参数；该文件已由 `.gitignore` 忽略，不能提交或推送。
- `apps/api/src/metaclass/core/config.py` 现在始终从项目根目录读取 `.env`，因此按 `cd apps/api` 的日常启动方式运行时也会加载同一份配置。

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
