# MetaClass API

FastAPI 模块化单体。代码位于 `src/metaclass`，业务按领域纵向组织；跨业务的技术实现放在 `infrastructure`。

`main.py` 只装配依赖和 Router。每个业务模块自行维护 `api.py`、`schemas.py` 和 `service.py`，详细协作方式见 [`../../docs/development-guide.md`](../../docs/development-guide.md)。

## 目录

```text
src/metaclass/
├── main.py
├── core/                 # settings 与应用依赖装配
├── infrastructure/
│   ├── database/         # 预留目录；当前 engine/session 在 database.py
│   ├── storage/          # 本地/对象文件存储
│   ├── providers/        # LLM、TTS、渲染器的接口与实现
│   └── tasks/            # worker 与 job 执行适配
└── modules/
    ├── materials/        # Material、PageMetadata、解析与渲染
    ├── content/          # LearningContent 与来源引用
    ├── classroom/        # plan、action、session、controller、event
    ├── assessment/       # 小测、证据与估计掌握度
    ├── video/            # 脚本、TTS、字幕、视频合成
    ├── presenter/        # 讲稿、问题与结构诊断
    ├── topics/           # 主题大纲、讲义和 PPT 生成
    └── jobs/             # 长任务状态、重试与进度
```

模块内部优先使用 `api.py`、`schemas.py`、`service.py`、`repository.py`；文件只在产生真实代码时创建。业务模块可依赖 `core` 和基础设施接口，避免互相读取数据库表；跨模块协作由 service 用例编排。

当前持久化使用 SQLAlchemy + SQLite。每个模块的 `service.py` 只依赖本模块 Repository Protocol；切换 PostgreSQL 时通过 `METACLASS_DATABASE_URL` 配置，不应修改业务 Service。

## 本地运行

```bash
cd ../..
python3 -m venv metaclass_env
source metaclass_env/bin/activate
python -m pip install -e 'apps/api[dev]'
uvicorn metaclass.main:app --reload
```

```bash
pytest -q apps/api/tests
ruff check apps/api/src apps/api/tests
```

默认测试不得调用真实 LLM/TTS。新增 provider 时同时提供 fake 实现，并把密钥留在环境变量中。

## 已实现的极简闭环

1. `POST /api/v1/materials` 上传 PDF/PPTX。
2. `POST /api/v1/materials/{id}/parse` 提取文本、渲染页面并生成来源引用。
3. `POST /api/v1/materials/{id}/learning-content` 构建统一内容。
4. `POST /api/v1/learning-contents/{id}/classroom-plans` 生成类型化课堂动作。
5. 创建 session 后通过 `/next`、`/answers`、`/questions` 驱动规则课堂。
6. `POST /api/v1/learning-contents/{id}/videos` 创建 VideoJob；完成后通过 `/video-jobs/{id}/result` 获取 MP4 结果。

当前操作为同步执行，便于理解和演示；生产化时应将解析和视频生成迁移到 `jobs` worker，同时保持现有 service 接口。

v1 面向文本型 PDF/PPTX；扫描件、复杂图表、SmartArt 和公式尚未接入 OCR/视觉模型。视频生成 SRT 并将其作为可开关的软字幕轨封装进 MP4，目前不是烧录在画面上的硬字幕。

## API 约定

- 路由前缀使用 `/api/v1`；`/health` 等系统端点除外。
- 长任务创建接口返回 `202 Accepted` 和 `job_id`。
- 错误响应使用稳定的机器码、用户消息和可选 details。
- 列表接口从首版开始统一分页结构。
- POST 创建请求支持幂等键，生成结果记录输入版本与来源。
