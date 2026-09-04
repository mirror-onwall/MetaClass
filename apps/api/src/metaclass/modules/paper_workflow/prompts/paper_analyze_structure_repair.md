# Stage 1 结构修复（仅一次）

上一次 `output/paper_analysis.json` 存在 JSON 语法或字段形状错误。

只修复结构，不重新分析论文，不新增事实，不改变已有证据的语义。读取：
- 当前无效的 `output/paper_analysis.json`；
- PaperAnalysis JSON Schema；
- `source/paper_source.json`，仅用于确认引用 ID 和页码格式。

覆盖写回合法的 `output/paper_analysis.json`。保留 `output/paper_analysis.md`。不得写入其他文件。

