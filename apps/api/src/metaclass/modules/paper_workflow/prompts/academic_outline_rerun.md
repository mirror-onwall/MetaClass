# Stage 3：完整重做学术汇报大纲

上一次输出虽然可能满足 JSON 形状，但叙事结构未达到学术汇报要求。读取 `validation_errors.json` 了解失败原因，然后重新执行 `academic-pptx` 的完整内容规划流程。

重新读取 `paper_analysis.json`、`figures.json`、`resolved_request.json`、`paper_source.json` 和两份 `*_schema.json`。重新生成 `outline.md`、`presentation_outline.json` 和 `slide_evidence.json`。必须保持论文事实和定量结果的证据边界，不得复用被 Validator 判定为不合格的叙事结构。

重点修正：每页一个主要论点、action title 串联形成完整论证、页数符合时长、结果页使用真实证据、不得引入分析中不存在的定量结论。完成前重新读取三个输出，并确认两份 JSON 符合对应 Schema。
