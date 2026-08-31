# Stage 2：extract-paper-images

使用 `extract-paper-images` Skill 为当前论文提取汇报所需的真实论文图表。

只提取 `paper_analysis.json` 中 `figure_candidates` 指向的图，以及补足核心方法或关键结果证据所必需的图。优先从 arXiv 源码获取原图；无法获得时，从当前 `paper.pdf` 提取或裁切。

禁止提取 logo、作者头像、二维码、社交媒体图标、期刊装饰和纯排版元素。

必须在当前 output 目录生成：
- `figures.json`
- `assets/` 下的实际图片文件

`figures.json` 必须符合 FigureCatalog JSON Schema。每个图必须包含：
- `id`、相对 `path`；
- `original_figure` 和可选 `panel`；
- 从 1 开始的 `page_no`；
- 非空 `caption`；
- `source_method`；
- `supports_claim_ids`，且只能引用 paper_analysis.json 中存在的 claim；
- `quality.width`、`quality.height`、`quality.readable`；
- `crop_notes`。

裁切图必须保留与科学解释有关的坐标轴、刻度、图例、caption、panel label、比例尺和方法标签。请在 `crop_notes` 中明确记录它们已保留；无法完整保留的图不要输出。

不得重新分析论文、修改 claim、编写汇报提纲或生成 PPTX。所有路径必须是相对于当前 output 目录的相对路径。
