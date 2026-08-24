# Stage 1：paper-analyze

使用 `paper-analyze` Skill 深度分析当前论文。保留该 Skill 对研究问题、知识缺口、方法、实验、贡献、局限、复现条件和批判性评估的分析要求，但使用本任务提供的本地证据，不执行其 Obsidian、联网下载或知识图谱步骤。

输入：
- `source/paper.pdf`：原始论文；若运行指令明确标记 PDF 不可读，则不要读取它。
- `source/paper_source.json`：页码、block、asset 和 bbox 的权威索引。
- `source/paper_content.md`：带稳定锚点的 Markdown 投影，也是 PDF 不可读时的降级输入。
- `resolved_request.json`：语言、受众、深度和汇报配置。

必须生成：
- `output/paper_analysis.md`
- `output/paper_analysis.json`

`paper_analysis.json` 必须严格符合 PaperAnalysis JSON Schema，并满足：
1. 至少包含一个 `importance="core"` 的 claim。
2. 每个 claim、quantitative_result、limitation 和 figure_candidate 都必须包含非空 `source_refs`。
3. source_ref 的 `page_no` 从 1 开始且不能超过论文页数。
4. `block_id` 和 `asset_id` 若存在，必须来自 paper_source.json。
5. 所有 confidence 必须位于 0 到 1。
6. 不得包含 HOME、Obsidian、Skill 目录或其他本机绝对路径。
7. 不能从论文验证的信息只能写入 `warnings`，不得写成确定事实。

`paper_analysis.md` 是供人检查的完整分析笔记；其中的重要结论、定量结果和局限应标注对应的 page/block/asset。JSON 是下游程序使用的权威结果。

不要生成 PPT、汇报大纲、图片目录或其他阶段文件。

