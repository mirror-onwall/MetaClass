# MetaClass 组员开发手册

本文面向第一次打开仓库的组员。目标是让每个人知道代码放在哪里、如何启动、如何修改，以及怎样避免多人同时改一个文件。

## 0. 第一阶段必须完成的 MVP 闭环

第一阶段不追求真实 LLM、真实 TTS、复杂异步或精致前端，只要求稳定跑通：

1. 上传 PDF/PPTX，保存经过 Schema 校验的 Material。
2. 解析页面，生成 PageMetadata 和结构化 SourceRef。
3. 基于 PageMetadata 构建包含 QuizItem 的 LearningContent。
4. 生成 ClassroomPlan 和强类型 TeachingAction。
5. 创建 ClassroomSession，通过 next/answer 推进课堂。
6. 记录 ClassroomEvent 和 Evidence，更新估计掌握度。
7. 基于页面图片和讲解文本生成基础视频结果。

所有生成内容必须保留 `source_refs`。验收入口是 `apps/api/tests/test_mvp_flow.py`，不是前端视觉效果。

## 1. 五分钟开始

要求：Python 3.11+、Node.js 20+、LibreOffice。

```bash
git clone <repository-url>
cd <repository-directory>
python3 -m venv metaclass_env
source metaclass_env/bin/activate
python -m pip install -e 'apps/api[dev]'
cd apps/web && npm install && cd ../..
./scripts/dev.sh
```

打开 <http://127.0.0.1:8000>。`Ctrl+C` 关闭服务。

提交代码前运行：

```bash
source metaclass_env/bin/activate
ruff format apps/api/src apps/api/tests
ruff check apps/api/src apps/api/tests
pytest -q apps/api/tests
cd apps/web && npm run build
```

## 2. 仓库文件职责

### 根目录

| 文件/目录 | 职责 | 谁通常修改 |
|---|---|---|
| `README.md` | 项目入口、启动命令、文档导航 | 负责人 |
| `MetaClass_具体设计_新版.md` | 产品设计原稿，不等同于最终代码架构 | 产品/负责人 |
| `MetaClass_开题PPT_详细版.md` | 开题展示内容 | 汇报组 |
| `.gitignore` | 忽略环境、缓存、数据库和生成产物 | 负责人 |
| `scripts/dev.sh` | 构建前端并启动同源 FastAPI | 工程负责人 |
| `data/raw` | 上传的原始 PDF/PPTX，不提交 Git | 程序生成 |
| `data/processed` | 页面图片与解析中间产物 | 程序生成 |
| `data/generated` | 视频等最终生成物 | 程序生成 |
| `data/runtime` | SQLite 与运行时状态 | 程序生成 |

### 后端入口与核心

| 文件 | 职责 |
|---|---|
| `apps/api/pyproject.toml` | Python 依赖、pytest 和 Ruff 配置 |
| `metaclass/main.py` | 创建 FastAPI、挂载 Router 和 Web 静态文件；不要写业务逻辑 |
| `core/config.py` | 环境变量和数据目录配置 |
| `core/application.py` | 依赖装配中心；在这里选择真实/Fake Provider |
| `core/schemas.py` | 所有公共契约的严格基类，拒绝未知字段 |
| `infrastructure/database.py` | SQLAlchemy Engine、共享 Base 与自动提交/回滚/关闭的事务上下文 |
| `infrastructure/storage.py` | 上传文件名清理和分块保存 |
| `infrastructure/providers/base.py` | LLM/学习内容和 TTS Provider 接口 |
| `infrastructure/providers/fake.py` | 无密钥可运行的假实现 |

### `modules/materials`

| 文件 | 职责 |
|---|---|
| `api.py` | 上传、上传并解析、页面列表、页面图片接口 |
| `schemas.py` | `Material`、纯解析 `PageMetadata`、`SourceRef` |
| `service.py` | PDF/PPTX 文本提取、页面渲染、状态更新 |
| `models.py` | `materials`、`pages` SQLAlchemy 表 |
| `repository.py` | MaterialRepository 接口和 SQLAlchemy 实现 |
| `README.md` | 模块边界说明 |

PDF 使用 PyMuPDF；PPTX 使用 `python-pptx` 提取文字，通过 LibreOffice 转 PDF 后渲染，失败时生成占位图片。PageMetadata 只包含原始标题、文本、图片和来源，不允许加入摘要、知识点等模型理解字段。OCR 应在这个模块新增独立 provider，不要写进 API。

### `modules/content`

| 文件 | 职责 |
|---|---|
| `api.py` | 创建和查询 LearningContent |
| `schemas.py` | `PageUnderstanding`、`LearningContent`、`LearningSection`、`QuizItem` |
| `service.py` | PageMetadata → PageUnderstanding → LearningContent 的用例编排 |
| `models.py` | `page_understandings`、`learning_contents` SQLAlchemy 表 |
| `repository.py` | 理解结果和统一内容的持久化接口/实现 |
| `README.md` | 模块边界说明 |

PageUnderstanding 保存摘要、知识点、教学重点、可能问题以及 provider/model/prompt_version。接入真实 LLM 时主要修改这里和 Provider，不要让前端直接调用模型，也不要把理解字段写回 PageMetadata。

### `modules/classroom`

| 文件 | 职责 |
|---|---|
| `api.py` | Plan、Session、next、answer、question 接口 |
| `schemas.py` | 类型化 TeachingAction、Plan、Session、Event |
| `service.py` | 生成计划、规则 Controller、Teacher/Evaluator MVP 行为 |
| `models.py` | `classroom_plans`、`classroom_sessions` 表 |
| `repository.py` | ClassroomRepository 接口和 SQLAlchemy 实现 |
| `README.md` | 计划与运行时事件边界 |

动作类型固定从 `SHOW_PAGE / EXPLAIN / ASK_QUIZ / WAIT_STUDENT / GIVE_FEEDBACK / REMEDIATE / END` 中扩展。新增动作必须同时修改 schema、计划生成、Controller、前端 ActionView 和测试。

所有 ClassroomEvent 必须包含全局唯一 `id` 和所属 `session_id`，以支持幂等、审计以及后续拆分独立事件表。

### `modules/assessment`

| 文件 | 职责 |
|---|---|
| `schemas.py` | Evidence 与 MasteryEstimate |
| `service.py` | 基于证据计算估计掌握度 |
| `README.md` | 诊断边界和用词约束 |

这里不负责控制课堂，只负责解释/计算评价结果。

### `modules/video`

| 文件 | 职责 |
|---|---|
| `api.py` | 创建、查询、下载视频 |
| `schemas.py` | VideoJob 任务生命周期与 VideoResult 文件结果 |
| `service.py` | 同步执行任务状态流、TTS、字幕、ffmpeg 与结果生成 |
| `models.py` | `video_jobs`、`video_artifacts` SQLAlchemy 表 |
| `repository.py` | 任务与结果分别持久化、按 job 查询结果 |
| `README.md` | 视频任务边界 |

真实 TTS 应实现 `TTSProvider`，不应直接替换 VideoService 的合成逻辑。当前任务仍同步执行，但 API 已使用 `VideoJob → VideoResult` 契约，后续迁移 worker 不需要修改前端数据结构。

当前 `GIVE_FEEDBACK` 是由提交答案接口触发并消费的计划动作，不会由 `next` 单独返回；`QUIZ_EVALUATED` 同时记录被评估的 quiz action 和对应 feedback action。

### 预留模块

| 目录 | 未来职责 |
|---|---|
| `modules/jobs` | 长任务、进度、失败重试、worker |
| `modules/topics` | 主题输入生成大纲、讲义、PPT |
| `modules/presenter` | 讲稿、问题预测和结构诊断 |

这些目录目前只有 README。开始实现时按照 `api.py + schemas.py + service.py + tests` 创建，不要提前制造空文件。

### 前端

| 文件 | 职责 |
|---|---|
| `apps/web/package.json` | React/Vite 依赖和脚本 |
| `vite.config.ts` | 开发代理配置 |
| `src/main.tsx` | React 挂载入口 |
| `src/App.tsx` | 当前 MVP 页面状态和五阶段流程编排 |
| `src/styles.css` | 全局设计 token、布局和组件样式 |
| `src/shared/api.ts` | 唯一 HTTP client；组件不要散写 fetch |
| `src/shared/types.ts` | 当前手写 API 类型，后续由 OpenAPI 生成 |
| `src/shared/components.tsx` | 无业务含义的通用 UI |
| `src/shared/format.ts` | 无业务含义的格式化函数 |
| `src/features/classroom/ActionView.tsx` | 按 TeachingAction 类型渲染课堂动作 |
| `src/features/*/README.md` | 各 feature 预期边界 |

新功能优先放入 `features/<name>`，`App.tsx` 只保留跨阶段状态和页面装配。

### 测试与契约

| 文件/目录 | 职责 |
|---|---|
| `apps/api/tests/test_health.py` | 应用最小存活测试 |
| `apps/api/tests/test_schemas.py` | Schema 契约、强类型动作和校验边界 |
| `apps/api/tests/test_repositories.py` | 数据表约束、事务和 Repository 往返 |
| `apps/api/tests/test_services.py` | Service 失败状态和外部 Provider 异常 |
| `apps/api/tests/test_mvp_flow.py` | PDF、PPTX、课堂、视频完整闭环及 API 负向路径 |
| `packages/contracts/schemas` | 未来导出的 JSON Schema |
| `packages/contracts/generated` | 未来生成的 TypeScript 类型 |
| `tests/e2e` | 未来浏览器级端到端测试 |

日常修改可先定向运行对应层，合并前必须运行全部测试：

```bash
pytest -q apps/api/tests/test_schemas.py apps/api/tests/test_repositories.py
pytest -q apps/api/tests
```

## 3. 标准修改方式

### Schema 优先原则

- 所有 API 输入、输出和持久化对象必须先构造成 Pydantic Schema。
- `extra="forbid"` 不得随意关闭；新增字段必须先修改公共契约和测试。
- Repository 只接收强类型 Schema；JSON 列只能由 Repository 内部通过 `model_dump(mode="json")` 产生。
- 公共 Schema 由负责人维护；字段重命名需要同时更新后端、前端类型和闭环测试。

### 新增后端接口

1. 在对应模块 `schemas.py` 定义输入/输出。
2. 在 `service.py` 写业务逻辑并保持与 HTTP 无关。
3. 在 `api.py` 增加路由，仅做参数转换。
4. 在 `apps/api/tests` 增加成功和失败测试。
5. 更新 OpenAPI 后再修改前端 `shared/api.ts`。

不要直接把接口写回 `main.py`。

### 接入真实 LLM

1. 在 `infrastructure/providers/base.py` 扩展明确的 Protocol，例如 `build_content()`。
2. 新建 `providers/openai.py` 或实际厂商文件。
3. 密钥从环境变量读取，绝不提交到 Git。
4. 在 `core/application.py` 根据配置选择 Fake/真实实现。
5. 给 Provider 写 mock 测试，默认测试仍使用 Fake。
6. 对模型 JSON 输出执行 Pydantic 校验，失败只允许有限次数修复。

### 接入真实 TTS

1. 新类实现 `TTSProvider.synthesize(text, output) -> duration`。
2. 在 `core/application.py` 替换注入实例。
3. 保持 VideoService 不依赖具体厂商 SDK。
4. 测试失败、限流和空音频情况。

### 新增 TeachingAction

1. `classroom/schemas.py` 新增 Payload 和 Action。
2. 加入 `TeachingAction` 判别联合。
3. `classroom/service.py` 决定谁生成、何时执行。
4. `ActionView.tsx` 增加对应渲染。
5. 完整流程测试验证未知动作不会悄悄跳过。

### 新增前端功能

```text
features/<feature>/
├── components/       # 只属于该功能的 UI
├── hooks/            # 该功能的数据和状态逻辑
├── types.ts          # 仅前端内部类型
└── README.md         # 边界和入口
```

API 调用统一加入 `shared/api.ts`。只有两个以上 feature 都使用的组件才移动到 `shared`。

## 4. 推荐分工

| 角色/组员 | 独占目录 | 第一阶段任务 |
|---|---|---|
| A：材料解析 | `modules/materials` | OCR、解析错误定位、缓存 |
| B：内容生成 | `modules/content`、`providers` | 真实 LLM、Prompt、结构化校验 |
| C：课堂运行 | `modules/classroom`、`assessment` | Controller、事件、诊断 |
| D：视频 | `modules/video` | 真实 TTS、字幕时间轴、任务化 |
| E：前端 | `apps/web/src/features` | 拆页、状态恢复、错误反馈 |
| 负责人 | `main.py`、`core/application.py`、契约 | 合并、版本和依赖方向 |

尽量让每名组员拥有独占目录。`main.py`、`application.py`、`shared/types.ts` 属于高冲突文件，修改前先在群里说明。

## 5. Git 协作约定

分支命名：

```text
feature/material-ocr
feature/real-llm-provider
fix/classroom-session-resume
docs/api-conventions
```

一个提交只做一类事情。推荐提交信息：

```text
feat(materials): add OCR fallback for scanned PDF
fix(classroom): preserve cursor after user question
test(video): cover failed ffmpeg process
docs: explain provider replacement workflow
```

Pull Request 至少说明：改了什么、为什么、如何测试、影响哪些 API/数据结构、是否需要环境变量。

## 6. 完成定义

功能合并前必须满足：

- API 输入输出有 Pydantic 类型。
- 生成内容保留 `source_refs`。
- 无密钥时测试仍可运行。
- Ruff、pytest、前端 build 全部通过。
- 没有提交 `.env`、数据库、上传材料、视频、`node_modules`。
- README 或模块文档已同步。
- UI 不把“估计掌握度”描述为精确测量。

## 7. 下一步优先级

1. 引入 Alembic，停止依赖 `create_all` 演进表结构。
2. 把 `LearningProvider` 扩展为完整 LearningContent 结构化生成接口。
3. 将课堂 Event、Evidence、Mastery 拆成独立表，方便查询和审计。
4. 引入 job 状态边界；第一版可同步执行并直接返回 finished。
5. 从 OpenAPI 生成 TypeScript 类型，删除重复手写契约。
6. 前端拆分 Materials、Content、Classroom、Video feature 页面。
7. 增加 session 恢复和浏览器端端到端测试。

## 8. 数据库演进原则

项目已经完成从 JsonRepository 到模块 Repository + SQLAlchemy ORM 的迁移。开发期继续使用 SQLite，部署或多人试用前再切 PostgreSQL。

仓储统一通过 `Database.session()` 访问数据库：上下文正常结束时提交，发生异常时回滚，最终关闭 Session。FastAPI lifespan 会在应用退出时释放 Engine 连接池。`build_services(..., create_schema=False)` 可供测试或未来接入 Alembic 时关闭自动建表；MVP 默认仍自动建表。

MVP 允许 `source_refs`、`sections`、`scenes`、`evidence`、`mastery`、`events` 等复杂对象使用 JSON 列。出现跨课堂查询、按知识点聚合、事件审计、学习历史分析或并发追加事件等需求时，再拆为独立关系表。

必须守住三条边界：

1. 业务 service 通过 `MaterialRepository`、`ContentRepository`、`ClassroomRepository` 等模块接口访问数据，禁止直接依赖具体数据库。
2. 保存对象必须先经过 Pydantic Schema 校验，Repository 不能接收任意 JSON 字段。
3. PDF、图片、音频和视频只保存路径或 URL，不把二进制塞进数据库。

演进路线：

```text
当前：严格 Schema + 模块 Repository + SQLAlchemy + SQLite 正式表
下一步：Alembic migration + 独立 Event/Evidence/Mastery 表
部署前：相同 Repository + SQLAlchemy + PostgreSQL
```
