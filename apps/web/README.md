# MetaClass Web

React + TypeScript + Vite 单页前端，连接 MetaClass FastAPI 极简闭环。

## 已实现

- PDF/PPTX 拖拽选择和上传
- 页面解析、缩略图预览和来源页码展示
- LearningContent 目标与章节预览
- ClassroomPlan/Session 创建
- Controller 逐步执行、随堂小测、自由提问和掌握度展示
- 基础视频生成与 MP4 下载
- 桌面端和移动端响应式布局

## 启动

先在仓库根目录启动后端：

```bash
source metaclass_env/bin/activate
uvicorn metaclass.main:app --host 127.0.0.1 --port 8000
```

开发前端时可在另一个终端运行：

```bash
cd apps/web
npm install
npm run dev
```

访问 <http://127.0.0.1:5173>。

日常体验建议直接在仓库根目录执行 `./scripts/dev.sh`，构建后的前端将由 FastAPI 同源托管在 <http://127.0.0.1:8000>，不依赖代理或 CORS。

## 配置

开发环境通过 Vite 将 `/api` 同源代理到 `http://127.0.0.1:8000`，无需额外配置。前后端分别部署时，复制 `.env.example` 为 `.env.local`：

```text
VITE_API_BASE=https://api.example.com
```

生产构建：

```bash
npm run build
```

构建产物位于 `dist/`，不会提交 Git。

## 目录

```text
src/
├── App.tsx              # 当前极简版完整工作流
├── styles.css           # 视觉系统与响应式布局
├── shared/
│   ├── api.ts           # HTTP API client
│   ├── types.ts         # 前端契约类型
│   ├── components.tsx   # 通用无业务 UI
│   └── format.ts        # 格式化函数
├── features/classroom/
│   └── ActionView.tsx   # TeachingAction 渲染
├── app/                 # 后续路由和全局 Provider
└── features/            # 功能增长后再从 App 拆分
```

服务端状态直接来自 API；当前规模不引入额外全局状态库。功能增长后应按 feature 拆分，而不是复制后端 service 目录。
