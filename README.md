# EduVerse Classroom

EduVerse Classroom 是一个将 PPT/PDF 或学习主题转换为结构化学习内容、讲解资源和可控互动课堂的教学平台原型。

项目采用模块化单体结构：

- `apps/api`：FastAPI 后端与课堂运行时
- `apps/web`：React + Vite 前端
- `packages/contracts`：前后端共享的数据契约与 JSON Schema
- `data`：本地开发数据（原始材料、中间产物和生成结果）
- `docs`：架构、评审与 API 文档
- `tests`：跨模块和端到端测试

建议 MVP 主链路：

```text
上传 PDF/PPTX
  -> 解析 PageMetadata
  -> 构建 LearningContent
  -> 生成 ClassroomPlan
  -> 创建 ClassroomSession
  -> Controller 执行 TeachingAction
  -> 更新 StudentState
```

详细设计评审见 [docs/design-review.md](docs/design-review.md)。

