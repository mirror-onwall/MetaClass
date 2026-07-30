# MetaClass

MetaClass 是一个面向教学材料的 AI 课堂生成平台。它可以导入 PDF/PPTX，解析并组织学习内容，生成演示文稿和问题库，再运行由教师与多类学生 Agent 参与的互动课堂，同时支持 TTS 讲解视频。

## 已实现功能

- 材料库：上传、解析、预览、归档与删除 PDF/PPTX
- 学习内容：按材料生成结构化课程内容和知识树
- 演示文稿：规划、生成并导出 PPTX
- 问题库：基于课程内容生成课堂问题
- 互动课堂：课堂计划、教师讲解、学生 Agent、自由提问与学习诊断
- 讲解视频：逐页语音合成、字幕与 MP4 输出
- 本地持久化：SQLAlchemy + SQLite，默认数据位于 `data/runtime/`

没有配置外部模型密钥时，后端默认使用 fake LLM/TTS，仍可运行测试和体验基础流程。

## 技术栈

- 后端：Python 3.11+、FastAPI、Pydantic、SQLAlchemy
- 前端：React 19、TypeScript、Vite
- 测试与检查：pytest、Ruff、TypeScript

## 快速开始

### 1. 安装后端

```bash
python3 -m venv metaclass_env
source metaclass_env/bin/activate
python -m pip install -e 'apps/api[dev]'
```

### 2. 安装前端

```bash
cd apps/web
npm install
cd ../..
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

默认配置无需密钥。接入真实 LLM、TTS、视觉模型或 PPT 服务时，按 `.env.example` 中的说明填写对应配置；不要提交 `.env`。

### 4. 启动项目

```bash
./scripts/dev.sh
```

脚本会先构建前端，再由 FastAPI 在同一端口托管 Web 和 API：

- 应用：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

前端单独开发时：

```bash
cd apps/web
npm run dev
```

访问 <http://127.0.0.1:5173>。前后端分开部署时，可在 `apps/web/.env.local` 设置 `VITE_API_BASE`。

## 运行检查

```bash
source metaclass_env/bin/activate
pytest -q apps/api/tests
ruff check apps/api/src apps/api/tests
cd apps/web && npm run build
```

## 仓库结构

```text
.
├── apps/
│   ├── api/                  # FastAPI 后端、领域模块和测试
│   └── web/                  # React + Vite 前端
├── packages/contracts/      # 共享契约与生成类型预留目录
├── data/                    # 本地上传、处理结果和 SQLite 数据（不提交）
├── docs/                    # 架构、开发和数据处理文档
├── output/                  # 示例数据处理产物
├── scripts/                 # 本地启动脚本
├── tests/e2e/               # 端到端测试预留目录
└── tools/                   # 知识库和学习内容生成工具
```

后端按业务域组织在 `apps/api/src/metaclass/modules/`，包括 materials、content、presentation、question_bank、classroom 和 video。应用依赖在 `core/application.py` 统一装配。

## 配置说明

常用配置分为以下几组：

- `METACLASS_LLM_*`：课堂、内容和问题生成模型
- `METACLASS_VISION_*`：材料页面视觉理解
- `METACLASS_EMBEDDING_*`：问题检索向量模型
- `METACLASS_TTS_*`：讲解语音合成
- `METACLASS_PPT_PROVIDER`、`METACLASS_CODEX_*`、`PRESENTON_*`：PPT 生成
- `METACLASS_MATERIAL_PARSER`、`METACLASS_MINERU_*`：材料解析

完整字段和默认值见 [`.env.example`](.env.example)。

## 进一步阅读

- [产品与功能设计](MetaClass_具体设计.md)
- [架构说明](docs/architecture.md)
- [开发手册](docs/development-guide.md)
- [后端说明](apps/api/README.md)
- [前端说明](apps/web/README.md)
