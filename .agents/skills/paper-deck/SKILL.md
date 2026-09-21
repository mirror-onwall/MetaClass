---
name: paper-deck
description: |
  将论文、技术文章或知识内容制作成高真实感的 AIGC 幻灯片。先做叙事结构和逐页视觉导演，再调用生图模型生成每一页 16:9 slide image，最后合成为 PPTX/PDF。适合论文汇报、组会、公开课、技术分享、商业化研究展示；当用户提到“论文PPT”“AI生成PPT”“不像AI的PPT”“高质感幻灯片”“逐页生图PPT”时使用。
---

# Paper Deck — Visual Slide Director

把论文/知识内容做成**看起来真的被设计过**的幻灯片。

核心路线不是用 PPT 对象硬摆版式，而是：

1. 先理解内容，做出 deck brief 和逐页叙事。
2. 为每一页写清楚“这页要让观众看到什么、感到什么、记住什么”。
3. 用生图模型生成 16:9 slide image。
4. 合成 PPTX/PDF，并保留 prompts 作为可返修的源文件。

## 不可绕过的生图要求

Paper Deck 的 V1 是 **raster-first AIGC slide image** 工作流。除非用户明确要求“不要生图”“用代码画图”“只要可编辑 PPT”或“使用 HTML/SVG/Canvas 生成”，否则必须调用真实的 raster image generation backend 为每一页生成图片。

严格禁止把以下产物冒充为本 skill 的“生图页”：

- 用 Python/Pillow、SVG、HTML/CSS、Canvas、Mermaid、matplotlib、PPT shapes 或任何本地绘图代码直接画出的整页图片
- 用模板、纯排版脚本、截图、占位图或手工组合元素替代生图后端输出
- 先本地画整页，再仅做轻微滤镜/后处理后当作 AIGC slide image

允许的本地处理仅限：

- 移动、复制、重命名生图后端输出文件
- 必要的格式转换、压缩、尺寸校验、PPTX/PDF 合成
- 用户明确选择混合方案时，在生图背景上叠加少量可编辑文字层；此时必须在 `deck-brief.md` 和交付说明中明确记录“混合文字层”，不能声称整页文字都由生图模型完成

如果当前环境没有可用的 raster image generation backend，必须停止并说明缺少生图后端；不要退化成本地绘图替代方案。

## 何时使用

适合：
- 论文组会、答辩、reading group、技术分享
- 需要“一眼不像模板 PPT”的视觉汇报
- 用户愿意接受每页是高质感图片，优先追求整体观感和传播效果
- 需要逐页返修：重做第 N 页、换风格、加真实感、减少 AI 味

不适合：
- 需要多人在 PowerPoint 里精细编辑每个文本框
- 大量表格、财务报表、合规材料
- 需要准确复制已有企业 PPT 母版

如果用户需要完全可编辑的 PPT，说明本 skill 的 V1 是 raster-first；可改用常规 PPTX 工具，或生成“图片背景 + 可编辑文字层”的混合方案。

## 工作流

### Step 1: 输入分析

接受：
- arXiv / DOI / 网页链接
- PDF 路径
- Markdown / 文本 / 文章
- 已有大纲
- 参考图片或参考 PPT 截图

如果是论文，优先复用 `paper-analyzer` 的阅读方式：读摘要、方法、实验、图表、结论；必要时搜索代码仓库。目标不是写长文，而是提取适合做 slide 的核心叙事。

输出并保存 `analysis.md`：
- 主题、受众、汇报场景
- 论文/内容的 1 句话主张
- 3-5 个必须讲清楚的核心点
- 推荐页数、推荐风格、语言
- 需要生成的图像类型：封面、机制图、流程图、数据页、结论页等
- 可直接使用的真实素材：论文 Figure/Table、PDF 截图、用户提供的截图、代码截图、实验曲线

### Step 2: 生成前确认

默认必须确认，不要直接生成图片。除非用户明确说“直接生成/不用确认/按默认来”。

询问时控制在 3 个问题以内：

1. 页数和用途：组会 / 答辩 / 公开分享 / 商业汇报，需要几页？
2. 风格：见 `references/style-system.md`。
3. 是否插入真实素材：是否允许从 PDF/论文图表中截图，或由用户提供截图/图片？如果允许，说明预计第几页使用哪些真实素材。

推荐话术：

```text
我建议做 12 页，风格用 journal-minimal：像 Nature/IEEE 论文图 + 正式学术汇报，清晰、克制、不花哨。
也可以换成 business-research 做商业研究分享，warm-notes 做手记风，或 liquid-glass 做 Apple 式玻璃质感。
这篇论文我建议在第 4 页插入原论文方法图局部截图，第 8 页插入实验曲线/表格截图，再基于这些真实素材做设计化排版。
确认后我会先生成 outline.md 和每页 prompt，再逐页出图并合成 PPTX/PDF。
```

### Step 3: Deck Brief

保存 `deck-brief.md`。必须包含：

- `style_preset`
- `audience`
- `slide_count`
- `language`
- `visual_rules`
- `do_not_use`
- `reference_images`（如有）
- `source_visual_plan`：哪些页使用真实图表/截图，来源和处理方式

风格细节按需读取 `references/style-system.md`。
真实素材策略按需读取 `references/source-visuals.md`。

### Step 4: Outline

保存 `outline.md`。每页用固定结构：

```markdown
## 01. Slide Title
- Role: cover / context / method / mechanism / evidence / result / takeaway
- Message: 这一页唯一要讲清楚的观点
- Render mode: native-raster | source-grounded-hybrid
- Visual: 画面主视觉和构图
- Text: 页面上允许出现的短文字
- Evidence: 引用的论文图表/公式/实验数据/代码位置
- Source visual: 是否使用真实截图/论文图表；来源、裁剪范围和落位
- Repair handle: 后续返修时可引用的定位描述
```

`native-raster` 页只需增加 `Render mode`，不增加额外结构负担。`source-grounded-hybrid` 页必须追加：

```yaml
- Source assets:
  - asset_id: asset_figure_crop_005_2
    placement: [0.07, 0.23, 0.62, 0.66]
    fit: contain
    crop: full
    preserve: [axes, legend, labels, values]
- Generated layer: 白色 journal-minimal 背景，左侧保留证据区域
- Overlay annotations:
  - 未筛选：规模增加几乎无增益
  - 高质量子集：持续提升
```

`placement` 使用相对于整页的归一化 `[x, y, w, h]` 坐标，数值范围为 0–1。`Source assets`、`Generated layer` 和 `Overlay annotations` 是 Paper Deck 对混合页的视觉导演结果，不是平台预置的模板。

规则：
- 每页只承载一个主观点。
- 页面文字尽量少；复杂解释放 speaker script 或备注里。
- 机制页优先画“输入 → 处理 → 输出”，不要画抽象灵感。
- 数据页只放最有说服力的 1-3 个数字。
- 真实论文图/截图通常比凭空生成更可信；能用真实素材时优先规划真实素材落位。
- 不要过度留白。主视觉、图表或证据区域通常应占画面 60%-80%，除非是封面或章节页。
- 8 页以上必须有节奏变化：封面、问题、方法、机制、证据、结论交替。

### Step 5: Prompt Files

每页必须先写 prompt 文件，再调用任何生图工具。

路径：

```text
paper-deck/{topic-slug}/
├── analysis.md
├── deck-brief.md
├── outline.md
├── prompts/
│   ├── 01-slide-cover.md
│   ├── 02-slide-context.md
│   └── ...
├── source-visual-manifest.json
├── images/
│   ├── 01-slide-cover.png
│   ├── 02-slide-context.png
│   └── ...
├── rendered/
│   ├── 01-slide.png
│   ├── 02-slide.png
│   └── ...
├── {topic-slug}.pptx
└── {topic-slug}.pdf
```

Prompt 写法读取 `references/prompt-template.md`。

如果任一页使用 `source-grounded-hybrid`，在调用任何生图工具之前，必须生成 `source-visual-manifest.json`：

- 完整格式和字段约束见 `references/source-visual-manifest.schema.json`。
- 该文件是 Paper Deck 设计决策与确定性合成器之间的机器可读契约。
- `slides` 必须覆盖 outline 的每一页，且 `slide_id`、`order` 和 `render_mode` 必须与 outline 一致。
- `native-raster` 页不要填充无意义的 source asset 或 annotation。
- `source-grounded-hybrid` 页必须记录生图底层、至少一个已验证源素材、归一化落位、原始页码/图号和 SHA-256。
- 每个混合页素材的 `asset_id`、`source_path`、`source_page`、`figure_label` 和 SHA-256 必须与 `provider_input/paper_source.json` 及其对应文件严格一致；不得只借用合法 asset ID 指向另一张图。
- 输出到 MetaClass 运行目录时，路径必须为 `provider_output/source-visual-manifest.json`；在独立 skill 目录中运行时，文件位于 deck 根目录。

硬规则：
- prompt 必须明确 16:9。
- prompt 里要写清楚风格、构图、文字语言、文字数量限制。
- 不要让模型生成页码、logo、水印、PPT 外壳。
- 如果需要精准文字，尽量减少图片内文字；可以后续做混合文字层。
- `native-raster` 页如果仅把源素材作为非保真的构图参考，prompt 必须明确不得发明新的事实。
- `source-grounded-hybrid` 页的 prompt 只描述生图底层、空白证据区和周边构图；不得要求生图模型嵌入、裁切、重画或修改真实素材。

### Step 6: 生成图片

#### 逐页渲染模式

Paper Deck 在完成论文分析、deck brief、outline 和逐页 prompt 后，自主为每页选择渲染模式。平台不得在 Paper Deck 分析和规划之前预先决定哪些页使用原图。`source-grounded-hybrid` 是对前文整页 raster-first 合成方式的受限例外，不取消真实 raster image generation backend 的生图底层要求；真实源素材和可编辑标注只能在生图之后分层叠加。

```yaml
render_mode:
  native-raster:
    description: 原生 paper-deck 整页生图

  source-grounded-hybrid:
    description: 生图视觉底层 + 真实论文素材 + 可编辑标注
```

自动选择规则：

- 封面、背景、问题、抽象机制、总结等不依赖精确源素材的页面，使用 `native-raster`。
- 直接使用论文 Figure、Table、实验曲线、真实截图、精确数据图，或其他不允许改写标签、数值和事实关系的页面，必须使用 `source-grounded-hybrid`。
- “按论文关系重画”的解释性机制图不等于论文原 Figure；只有页面需要保留真实源图的标签、数值、坐标轴、图例或截图内容时，才必须进入 `source-grounded-hybrid`。
- 每页的模式选择必须记录在该页 prompt 和 `generation-log.md` 中；使用真实素材的页面还必须记录素材路径、图号/页码与预期落位。

两种模式共享同一份 analysis、deck brief、outline、风格锚点和叙事节奏。渲染模式只决定最终视觉如何落版，不得反向削弱、替换或重做 Step 1–5 的分析与规划。

图片后端选择：

1. Codex 环境优先用内置 `imagegen`。
2. 如果用户指定 `baoyu-imagine`、Gemini、OpenAI、Seedream 等后端，按用户指定。
3. 如果没有可用生图后端，停止并告诉用户需要一个 raster image backend。

生图门禁：
- 在调用任何生图工具之前，必须已经写好对应页的 `prompts/NN-*.md`。
- 每一页最终进入 `images/` 的主图必须来自真实 raster image generation backend。
- 不允许用 Python/Pillow、SVG、HTML/CSS、Canvas、Mermaid、matplotlib、PPT shapes 或本地绘图脚本生成整页主图来替代生图。
- 不允许因为担心中文文字错误，就绕过生图后端改成本地绘制整页。正确做法是减少图片内文字、改 prompt 重生成，或在用户同意的情况下使用“生图背景 + 可编辑文字层”的混合方案。
- 如果使用混合文字层，`images/` 中仍必须保留每页的生图背景或生图整页来源，并在 `deck-brief.md` 记录哪些文字是后叠加的。
- 生成后要在 `generation-log.md` 记录每页使用的后端、prompt 文件、输出文件、生成时间；没有生成记录的图片不能作为最终交付页。

`native-raster` 要求：

- 保持原生 raster-first 行为：逐页 prompt 生成完整 16:9 slide image。
- 现有生图门禁、风格锚点、失败页保留和生成日志规则全部继续适用。

`source-grounded-hybrid` 要求：

- 生图后端只生成与整份 deck 一致的 16:9 视觉底层，并为真实素材保留明确的空白落位区域。
- prompt 必须明确禁止生图后端绘制、模仿、重建、改写或插入论文 Figure/Table、实验曲线、截图和精确数据。
- 真实素材必须在生图完成后从已验证的源文件确定性嵌入，不得先经过生图模型重画。
- 生图底层与真实素材必须分别保留；标题、旁注、箭头和强调框应作为可编辑标注层，不得烧录进论文原图。
- 源素材嵌入只用于保真落版，不改变 Paper Deck 对该页 Message、Visual、Evidence、视觉层级和叙事位置的决策。

生成策略：
- 先生成第 1 页作为风格锚点。
- 后续页如果后端支持 reference image，就用第 1 页作为风格参考，降低漂移。
- 每 3-4 页检查一次缩略图，发现风格漂移就修 prompt 再继续。
- 保存失败页，不要覆盖成功页。

### Step 7: 合成 PPTX/PDF

生成完图片后，按逐页渲染模式合成：

- `native-raster`：保持现有行为，将完整 slide image 铺满 16:9 页面。
- `source-grounded-hybrid`：以生图底层为视觉基础，再按 Paper Deck 规划的位置确定性嵌入真实素材和可编辑标注层。论文原图不得被拉伸、重画或压平进生图底层。

两种模式都使用同一个合成器：

```bash
python3 <SKILL_ROOT>/scripts/merge_deck.py paper-deck/{topic-slug}
```

脚本行为：

- 没有 `source-visual-manifest.json` 时，保持原生兼容路径：读取 `images/NN-*.png|jpg|webp`，每张图片铺满一页 16:9。
- 存在 manifest 时，按页读取 `render_mode`。`native-raster` 页仍只有整页生成图；`source-grounded-hybrid` 页生成背景、已验证源图、文本标注、强调框和箭头均为独立 PowerPoint 对象。
- 合成器在嵌入前校验源素材 SHA-256、安全路径、归一化边界和宽高比；校验失败时停止交付。
- `images/` 始终表示生图后端产出的整页图或视觉底层，不得作为混合页的最终预览。同一个确定性合成器必须依据 manifest 分别生成分层 PPTX 与最终 `rendered/` 页面，再由 `rendered/` 生成 PDF；OCR、VLM 质检、课堂预览和 PDF 对比统一使用 `rendered/`。
- PDF/预览合成不得依赖 LibreOffice、PowerPoint、Keynote 或其他 GUI/Office 运行时。它可以栅格化最终页面，但不得改变源 Figure/Table 的内容、比例或落位；PPTX 中的源素材仍必须保持为独立图片对象。

MetaClass `provider_output/` 模式必须使用固定交付名：

```bash
python3 <SKILL_ROOT>/scripts/merge_deck.py provider_output --name presentation
```

#### 生成期故障自愈

不要把第一次命令失败直接交给用户，也不要无限排查环境。按以下边界自动恢复：

1. 在大批量生图前运行一次 `python3 <SKILL_ROOT>/scripts/merge_deck.py --help`，确认合成器能自动进入项目运行环境；失败时先解决这一项再继续生图。
2. 启动时先清点目标输出目录。若存在上次暂停留下的合法 analysis、outline、prompts、manifest 或图片，必须从现有检查点续跑，只补缺失或损坏的文件；不得默认从第一页重新生成。
3. 已成功生成的 `analysis.md`、outline、prompts、manifest 和 `images/` 都是检查点。后续失败不得删除、覆盖或从头重做无关页面。
4. 合成或验证失败时，读取完整错误，定位到依赖、manifest、路径、哈希、单页素材或输出数量中的具体原因，只修最小范围，然后重跑原命令。最多自动修复并重试 2 次。
5. 缺少 Python 依赖时优先使用合成器内置的项目运行环境切换；不得临时安装包。不得尝试 LibreOffice、PowerPoint、Keynote、WPS 或 GUI 自动化。
6. 单页生图失败只重试该页；违规文字或虚构数据只返修对应 prompt 和图片。不得因第 N 页或最终合成失败而重做已经通过的 1…N−1 页。
7. 为最终合成和验证预留至少 10 分钟。距离 runtime 截止不足 10 分钟时，停止非必要视觉返修，优先完成 PPTX、PDF、`rendered/` 和验证。
8. 两次最小修复仍失败时停止尝试，在 `generation-log.md` 记录原始错误、两次修复、剩余阻塞和已保留产物；禁止重复同一失败命令直到超时。

### Step 8: 质量检查

交付前按 `references/quality-gate.md` 检查：

- 是否一眼像真实设计作品，而不是模板堆砌
- 每页是否只有一个主观点
- 是否有过多无意义留白；关键内容是否占据足够画面
- 真实素材页是否明确记录来源、页码/图号和落位
- 每页是否记录 `native-raster` 或 `source-grounded-hybrid`，且模式选择是由 Paper Deck 在分析和规划后作出
- 引用 Figure、Table、实验曲线、真实截图或精确数据的页面是否使用 `source-grounded-hybrid`
- 混合页中的真实素材是否来自已验证的源文件，而非生图模型的重画结果
- 论文原图的标签、数值、坐标轴、图例、面板标记和事实关系是否保持不变，且未被拉伸或不当裁切
- 混合页的生图底层、真实素材和可编辑标注层是否分开保留
- 风格是否一致
- 图片文字是否清晰、无错别字、无伪字
- 是否存在 AI 常见问题：假 UI、假 logo、乱码标签、过度赛博、塑料 3D、无意义装饰
- `generation-log.md` 是否存在，且每一页都记录了真实 raster image generation backend、prompt 文件和输出文件
- 是否存在本地绘图/模板/截图冒充生图页；如有，必须重做或明确改成用户确认过的非 paper-deck 路线
- PPTX/PDF 是否能打开，页数是否正确

### Step 9: 返修

返修时永远先改源文件：

| 用户说 | 操作 |
|---|---|
| “第 5 页更学术一点” | 改 `prompts/05-*.md`，保留旧图，生成新图 |
| “统一成第 1 页的质感” | 把第 1 页风格锚点追加到相关 prompts |
| “第 7 页文字太多” | 修改 outline 的 Text，再改 prompt |
| “只重做背景，不动内容” | 在 prompt 中保留 Message/Text，重写 Visual |
| “新增一页机制细节” | 更新 outline，新增 prompt，生成图片，重跑合成脚本 |

不要用程序在生成图上涂改文字。文字错了就改 prompt 重生成，或切换到混合文字层方案。

## 参考文件

- `references/style-system.md`：风格预设和选择规则
- `references/layouts.md`：常用页面角色与构图
- `references/source-visuals.md`：PDF 截图、论文图表、用户图片的使用策略
- `references/source-visual-manifest.schema.json`：混合页源素材、落位和可编辑标注的机器可读契约
- `references/prompt-template.md`：逐页生图 prompt 模板
- `references/quality-gate.md`：交付前检查和返修标准
