# Stage 3：academic-pptx 内容与叙事冻结

使用 `academic-pptx` Skill 设计学术汇报的内容、论证顺序和逐页契约。本阶段禁止创建或修改任何 PPTX。

输入是 `paper_analysis.json`、`figures.json`、`resolved_request.json` 和 `paper_source.json`。论文事实和定量结论只能来自 paper_analysis.json；图片只能来自 figures.json。

输入中还提供了 `presentation_outline_schema.json` 和 `slide_evidence_schema.json`。它们是 MetaClass 的权威输出契约。生成 JSON 前必须完整读取这两个 Schema；字段名、必填字段、枚举、嵌套结构和数据类型必须严格匹配，不要自行猜测或增加 Schema 未声明的字段。

必须在当前 output 目录生成：
- `outline.md`
- `presentation_outline.json`
- `slide_evidence.json`

要求：每页一个主要论点；使用结论式 action title；slide id 唯一且 order 从 1 连续；section 必须连续覆盖全部页面；核心 claim 必须被至少一页覆盖；结果页必须绑定 claim 或 source_ref；所有 asset id 必须存在；页数与 resolved_request.json 的时长匹配；不得新增分析 JSON 中不存在的数字。

slide_evidence.json 必须逐页覆盖 presentation_outline.json，顺序完全一致。不要生成 PPTX、slide_plan 或渲染图。

完成前必须重新读取两个 JSON 文件并确认它们可解析且符合对应 Schema。如果无法在不捏造论文事实的前提下满足契约，最后返回 blocked，并在 warnings 中说明原因；不要用空字符串、虚构 claim、虚构 asset 或虚构 source ref 填充必填字段。
