# MetaClass

MetaClass 是一个把 PPT/PDF 或学习主题转换为结构化学习内容、讲解资源和可控互动课堂的教学平台原型。

项目同时服务两类场景：

- **Learner Studio**：材料解析、讲解文档、讲解视频、互动课堂与学习诊断。
- **Presenter Studio**：逐页讲稿、提问预测、结构诊断与 PPT 重构。

两端共享 `PageMetadata`、`LearningContent`、`ClassroomPlan` 和类型化 `TeachingAction`。课堂并非让多个 Agent 自由聊天，而是由 `ClassroomController` 按计划和会话状态调度。

> 当前状态：项目处于骨架搭建阶段，已提供 FastAPI 健康检查、领域目录和测试入口；材料解析、课堂运行与前端页面仍待实现。

## 核心流程

```text
PPT/PDF ─┐
         ├─ Read ─> PageMetadata ─> LearningContent
学习主题 ─┘                                │
                                          v
                              Plan ─> ClassroomPlan
                                          │
                                          v
                           Run ─> ClassroomSession
                                  + SessionEvent
                                  + StudentState
```

`Read → Plan → Run` 是本项目对“课程生成 + 状态机式课堂编排”的工程归纳，不是 OpenMAIC 的官方阶段命名。

## 仓库结构

```text
.
├── apps/
│   ├── api/                  # FastAPI 模块化单体
│   │   ├── src/metaclass/
│   │   │   ├── core/        # 配置、异常、日志等通用能力
│   │   │   ├── infrastructure/ # 数据库、存储、Provider、任务执行器
│   │   │   └── modules/     # 按 materials/content/classroom 等业务域组织
│   │   └── tests/            # API 单元与集成测试
│   └── web/                  # React + Vite 单页操作前端
│       └── src/
│           ├── app/          # 路由、布局和全局 Provider
│           ├── features/     # 按页面能力拆分的业务功能
│           └── shared/       # 通用 UI、API client、hooks
├── packages/contracts/      # OpenAPI/JSON Schema 与生成类型
├── data/                    # 本地数据，产物不提交 Git
├── docs/                    # 架构决策和设计评审
├── tests/e2e/               # 跨端主链路测试
└── MetaClass_具体设计_新版.md # 产品与功能设计原稿
```

后端各业务域内部建议统一采用以下结构，并按需创建，避免空目录泛滥：

```text
modules/<domain>/
├── api.py          # HTTP 路由与请求/响应适配
├── schemas.py      # Pydantic API/领域契约
├── models.py       # 持久化模型（需要数据库时再创建）
├── repository.py   # 数据访问接口与实现
└── service.py      # 用例编排与业务规则
```

课堂模块可额外包含 `controller.py`、`events.py`、`actions.py` 和 `agents/`；一次性结构化生成逻辑使用 `generator` 或 `service` 命名，不包装成 Agent。

## 快速开始

项目根目录已使用 `metaclass_env` 作为 Python 虚拟环境。首次安装执行：

```bash
python3 -m venv metaclass_env
source metaclass_env/bin/activate
python -m pip install -e 'apps/api[dev]'
uvicorn metaclass.main:app --reload
```

后续启动只需要：

```bash
./scripts/dev.sh
```

该脚本会先构建 Web，再由 FastAPI 在同一个 `8000` 端口托管页面与 API。访问 <http://127.0.0.1:8000>，按 `Ctrl+C` 即可关闭。

访问：

- 健康检查：<http://127.0.0.1:8000/health>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>

运行检查：

```bash
source metaclass_env/bin/activate
pytest -q apps/api/tests
ruff check apps/api/src apps/api/tests
```

启动前端：

```bash
cd apps/web
npm install
npm run dev
```

然后访问 <http://127.0.0.1:5173>。前端默认连接 <http://127.0.0.1:8000>，可通过 `VITE_API_BASE` 修改。

## MVP 范围

首版只保证一条可演示的纵向闭环：

1. 上传 PDF/PPTX，解析页面并保留来源引用。
2. 构建统一 `LearningContent`。
3. 生成 `ClassroomPlan` 与可校验的 `TeachingAction`。
4. 创建 `ClassroomSession`，由规则 Controller 驱动 Teacher/Evaluator。
5. 根据小测和交互证据更新“估计掌握度”。
6. 使用页面图片、TTS、字幕合成基础讲解视频。

主题生成、向量检索、完整 Presenter 重构排在主链路之后；数字人、3D 场景、复杂仿真和长期画像不进入 MVP。

## 关键工程约束

- `ClassroomPlan` 是不可变模板；用户提问等动态行为追加为 `SessionEvent`。
- `TeachingAction` 使用带判别字段的联合类型，各动作拥有独立 payload。
- 所有生成内容保留 `source_refs`，至少能追溯到材料页或文本块。
- 学习诊断必须保存 evidence，不把模型估计包装成精确测量。
- 默认测试使用 fake LLM/TTS provider，不依赖密钥和外网。Fake TTS 生成可播放测试音轨，接入真实语音时替换 Provider 即可。
- 解析、视频、PPT 生成通过 job 边界执行；MVP 可使用单进程 worker，避免依赖易丢失的进程内后台任务。

## 文档导航

- [具体设计](MetaClass_具体设计_新版.md)：产品功能、数据对象、API 和页面设计原稿。
- [设计评审](docs/design-review.md)：对原设计的范围收敛和架构修正。
- [架构说明](docs/architecture.md)：当前模块、依赖方向、主流程和技术债。
- [组员开发手册](docs/development-guide.md)：逐文件职责、修改步骤、测试和分工方式。
- [后端说明](apps/api/README.md)：后端模块边界与扩展约定。
- [前端说明](apps/web/README.md)：前端目录和状态管理原则。
- [共享契约](packages/contracts/README.md)：跨端数据契约的来源与生成规则。

## 推荐实现顺序

1. 契约、配置、数据库与 fake provider
2. 材料上传、解析与 `LearningContent`
3. `ClassroomPlan`、规则 Controller 与 session event
4. 前端课堂页和状态恢复
5. Evaluator 与轻量学习诊断
6. 视频链路
7. Presenter 基础能力
8. 主题生成、检索和 PPT 重构
