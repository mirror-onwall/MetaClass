# Stage 4：官方 pptx Skill 渲染

同时遵循 Stage 3 已应用的 `academic-pptx` 内容与结构规范，以及当前 `pptx` Skill 的文件生成、内容检查、视觉检查和修复流程。严格渲染已经冻结的学术汇报合同；不得重新分析论文、改变页面顺序、修改 claim、增加定量结论或替换 asset id。

输入：`outline.md`、`presentation_outline.json`、`slide_evidence.json`、`figures.json` 和 figures.json 登记的 assets。

必须在当前 output 目录生成：
- `presentation.pptx`
- `slide_plan.json`
- `speaker_notes.json`
- `qa_report.json`
- `rendered/`（可以先生成，MetaClass 仍会用 LibreOffice 重新渲染验收）

slide_plan.json 必须逐页包含 slide_id、order、title 和使用的 asset_ids。speaker_notes.json 必须逐页包含 slide_id 和 note。禁止 placeholder、Lorem Ipsum、TODO、TBD 或模板提示文字。关键图片必须真实嵌入 PPTX。

页面标题应与 presentation_outline.json 高度一致。所有 shape 必须位于页面边界内，正文应使用可读字号且不得明显溢出。必须按 `pptx` Skill 完成至少一次生成、渲染检查、修复和复验，并将结果写入 qa_report.json。
