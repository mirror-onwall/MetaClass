# Contracts

前后端共享的 OpenAPI/JSON Schema 与生成类型。优先定义：

- Material / PageMetadata
- LearningContent / SourceRef
- ClassroomPlan / Scene
- TeachingAction discriminated union
- ClassroomSession / SessionEvent
- StudentState / AssessmentEvidence
- JobStatus

## 规则

- 后端 Pydantic/OpenAPI 是 HTTP 契约的单一来源，前端类型由其生成。
- 需要离线校验或持久化的对象另行导出 JSON Schema。
- `TeachingAction` 以 `type` 为判别字段，不使用任意 `value`。
- 所有生成对象携带版本；引用材料的对象携带 `source_refs`。
- 生成文件提交到约定的 `generated/` 子目录，手写源文件与生成物分开。
