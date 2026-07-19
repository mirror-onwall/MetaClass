# Presentation 模块交接说明

本文档描述 EduVerse Classroom 当前 PPT 生成链路、核心数据结构、运行时依赖、已知限制和后续建议。内容以仓库当前实现为准，不把规划中的能力写成已完成能力。

- 文档状态：交接基线
- 最后核对日期：2026-07-19
- 适用模块：`apps/api/src/metaclass/modules/presentation`
- 运行和环境配置的权威说明：仓库根目录 `README.md`、`apps/api/README.md` 和 `.env.example`

## 1. 模块职责

`metaclass.modules.presentation` 负责把已经生成的 `LearningContent` 转换为：

1. 可持久化的逐页 `PresentationPlan`；
2. 每页可编辑的 PowerPoint 元素；
3. `.pptx` 文件；
4. 浏览器使用的逐页 PNG 预览；
5. 独立的逐页讲稿 JSON；
6. 供课堂小测、课堂流程和视频生成复用的页面级数据。

本模块不负责原始材料解析。PDF、PPTX 和文档的解析、分页、图片抽取以及 `LearningContent` 的生成位于 materials/content 模块。

## 2. 当前总体链路

```text
上传材料
  -> MaterialService：解析文本、页码、图片和来源引用
  -> ContentService / LLMLearningProvider：生成 LearningContent
  -> PresentationPlanGenerator：生成逐页内容
  -> 内容容量分析：必要时换高容量布局或生成续页
  -> Layout Registry：选择语义布局并计算元素坐标
  -> PresentationPlan 持久化
  -> QuestionBankGenerator：为最终页面准备课堂题目
  -> PPTSkillAdapter：使用 python-pptx 导出 deck.pptx
  -> LibreOffice + PyMuPDF 或 PIL：生成逐页 PNG
  -> 保存 PPTArtifact，提供下载、预览和后续视频生成
```

核心设计原则是：

> LLM 负责理解材料和规划教学内容；确定性 Python 代码负责几何排版；python-pptx 负责生成可编辑文件。

默认链路不再让 LLM 自由生成每个元素的 `x/y/w/h`。自由场景生成代码仍然保留，但目前不是默认运行路径。

## 3. 目录与文件职责

| 文件 | 职责 |
| --- | --- |
| `api.py` | FastAPI 路由，创建/查询计划任务和 PPT 任务，下载产物与预览图 |
| `service.py` | 编排 PresentationPlan 和 PPTArtifact 的生成流程 |
| `planner.py` | LLM 内容规划、兜底内容、容量拆页、布局装配和场景校验 |
| `layout_registry.py` | 布局注册表、语义选择、容量切分和参数化元素生成 |
| `layout_constraints.py` | 画布安全区、最小字号、内容间距和学术排版原则 |
| `brand_palette.py` | 蓝白主题色板和颜色归一化 |
| `schemas.py` | PresentationPlan、SlidePlan、SlideElement、Job、Artifact 等 Pydantic 模型 |
| `models.py` | SQLAlchemy 持久化模型 |
| `repository.py` | PresentationPlan、PPT Job 和 Artifact 的数据库读写 |
| `skill_adapter.py` | PPTX 编译、字体处理、图表/表格生成和页面预览 |
| `pptx_skill/SKILL.md` | 项目内 PPT 内容与场景提示词；不是独立运行的外部 Codex skill |

## 4. 入口和任务模型

### 4.1 PresentationPlan 任务

推荐使用异步接口：

```http
POST /api/v1/learning-contents/{content_id}/presentation-plan-jobs
GET  /api/v1/presentation-plan-jobs/{job_id}
GET  /api/v1/presentation-plan-jobs/{job_id}/result
```

任务进度大致分为：

- `planning_content`：规划逐页教学内容；
- `planning_scenes`：容量分析、版式选择和元素生成；
- `saving`：保存计划；
- `preparing_questions`：为最终页面生成题库；
- `completed` / `failed`。

注意：`PresentationService._plan_jobs` 当前是进程内字典。服务重启后，所有 plan job 到 plan id 的映射都会丢失，包括已经完成的 job；客户端之后无法再通过原 job id 获取结果。最终 `PresentationPlan` 本身仍在数据库中，可以通过 content id 查询最新计划。多 worker 之间也不共享 plan job 状态。

### 4.2 PPTX 任务

```http
POST /api/v1/presentation-plans/{plan_id}/ppt-jobs
GET  /api/v1/ppt-jobs/{job_id}
GET  /api/v1/ppt-jobs/{job_id}/artifact
GET  /api/v1/ppt-artifacts/{artifact_id}/download
GET  /api/v1/ppt-artifacts/{artifact_id}/slides
GET  /api/v1/ppt-artifacts/{artifact_id}/slides/{slide_no}/image
```

PPT Job 和 Artifact 使用 SQLAlchemy 持久化。默认数据库是：

```text
<data_dir>/runtime/metaclass.db
```

生成文件默认写入：

```text
<data_dir>/generated/presentations/<ppt_job_id>/
  deck.pptx
  speaker_scripts.json
  skill_request.json
  slides/
    slide_001.png
    slide_002.png
    ...
```

## 5. LLM Provider

应用启动时通过 `build_llm_provider()` 构建统一 LLM Provider，并注入：

- `LLMLearningProvider`；
- `PresentationPlanGenerator`；
- `QuestionBankGenerator`。

当前 Provider 构建器支持：

- `fake`；
- `openai-compatible`；
- `gemini` / `google-gemini`。

具体模型、API 地址、密钥、超时、温度和最大 token 数由运行时配置决定。Presentation 模块不应直接依赖某个厂商 SDK；如新增 Provider，应优先实现统一 `LLMProvider` 协议。

需要特别注意：接入 Gemini API 不等于获得 Gemini in Google Slides 网页产品的完整生成能力。当前系统仍由本地布局引擎和 `python-pptx` 负责排版与导出。

## 6. PresentationPlan 内容规划

`PresentationPlanGenerator.generate()` 分两个逻辑阶段。

### 6.1 逐页内容生成

LLM 输入为完整 `LearningContent` 的压缩结构，包括：

- 课程标题、目标和大纲；
- sections；
- teaching script / narrative；
- concepts、formulas、examples、misconceptions；
- source excerpts 和 source refs；
- visual opportunities；
- quiz / interaction 信息。

模型只负责生成：

```text
title
source_section_ids
key_points
speaker_script
suggested_visual
visual_payload
layout = freeform
```

内容规划要求：

- 第一页是纯封面；
- 最后一页是课程总结；
- 不生成目录页；
- 每个 section 至少被一页覆盖；
- 页面顺序不能逆着 LearningContent section 顺序；
- 一页只讲一个核心观点或任务；
- 复杂概念可以拆成多页；
- 页面文字承载定义、步骤、变量、条件和结论；
- `speaker_script` 承载完整解释、过渡、案例和辨析；
- 禁止输出内部术语和 Markdown `**`。

LLM 输出经过 Pydantic 校验。如果请求超时、JSON 无效、字段不满足要求或 section 覆盖不完整，会使用 `_fallback_plan()`。

### 6.2 兜底计划

兜底计划按照：

```text
封面 -> 每个 section 一页 -> 课程总结
```

生成。兜底讲稿优先级为：

```text
section.teaching_script
  -> section.teaching_narrative
  -> section.summary
  -> section.title
```

因此，上游 LearningContent 的质量会直接影响 PPT。参考文献条目被错误归为教学知识时，兜底讲稿可能把完整文献著录带入课堂讲稿。这个问题应优先在 content 模块做 `reference_only` 分类和过滤，而不是只在 PPT 末端删除字符串。

## 7. 容量分析与自动续页

内容规划完成后，系统调用 `select_fallback_layout()` 和 `split_points_for_layout()`。

布局选择考虑：

- 页面位置；
- 标题、要点和 suggested visual 中的语义关键词；
- 要点数量；
- 平均文字长度；
- 布局最大槽位数；
- 布局密度和字符预算。

长文本会避开横向流程、时间线、阶梯和关系图等窄槽位，优先选择 split 或 evidence 类布局。

内容超过布局容量时：

1. 保持要点顺序；
2. 按最大槽位数和字符预算切分；
3. 创建 `<原 slide id>_part_N`；
4. 标题添加“（续）”；
5. 重新连续编号；
6. 再进行布局选择和元素生成。

当前切分以完整 `key_point` 为最小单位。单个极长 key point 不会在句子内部拆分，这是后续需要补充的能力。

实际顺序如下：

```python
expanded = []
for original_index, slide in enumerate(plan.slides):
    initial_spec = select_fallback_layout(slide, original_index)
    if original_index == 0:
        chunks = [slide.key_points]  # 封面不拆分
    else:
        chunks = split_points_for_layout(slide.key_points, initial_spec)
    expanded.extend(create_original_and_continuation_slides(slide, chunks))

for final_index, slide in enumerate(expanded):
    slide.order = final_index + 1
    final_spec = select_fallback_layout(slide, final_index)
    slide.elements = build_fallback_elements(slide, final_spec, palette)
```

这里没有“重选后再次切分”的迭代。初次布局决定切分容量，分片完成后会重新选最终布局，但最终布局容量更小时不会再次进入切分循环。这是需要继续完善的边界情况。

ID 和顺序契约：

- 原页面保留原 `slide.id`；
- 第一张续页为 `<原 id>_part_2`，不会生成 `_part_1`；
- 后续续页依次为 `_part_3`、`_part_4`；
- `order` 在所有续页生成后重新赋值为连续的 `1..N`；
- “连续编号”只指 `order`，不表示 `slide.id` 是连续数字；
- 重新生成整套计划会创建新的 plan，不能把 slide id 当成跨 plan 永久稳定的业务主键。

封面固定不参与容量拆分。总结页可以拆分；如果拆分，最后一张“课程总结（续）”仍是整套演示文稿的最后一页。

## 8. 布局系统

`LAYOUT_REGISTRY` 当前包含 16 个布局标识：

- `hero_minimal`
- `hero_statement`
- `split_left`
- `split_right`
- `focus_rail`
- `quote_field`
- `sequence_horizontal`
- `sequence_vertical`
- `comparison_split`
- `before_after`
- `timeline_alternating`
- `ladder`
- `constellation`
- `matrix`
- `evidence_strip`
- `summary_path`

它们归属于 hero、split、spotlight、process、comparison、timeline、hierarchy、relationship、evidence 和 summary 等布局族。

这 16 个标识并非 16 套完全独立的模板，部分布局族共用底层构图分支。当前实现是“稳定骨架 + 参数化变化”，还不是 PPTX skill 那种逐页自由设计和人工式修复。

参数化能力包括：

- 按主要内容和辅助内容的字符量动态调整左右栏宽；
- 根据辅助要点数量动态分配卡片高度；
- 流程节点根据数量计算宽度和间距；
- 长内容自动避开狭窄布局；
- 超载生成续页而不是静默省略。

## 9. 页面数据模型

`SlidePlan` 是内容、讲稿和视觉结构的共享页面模型：

```python
SlidePlan(
    id="slide_002",
    order=2,
    source_section_ids=["section_001"],
    title="...",
    key_points=["..."],
    speaker_script="...",
    suggested_visual="...",
    layout="freeform",
    layout_id="split_left",
    visual_payload=["..."],
    background="FFFFFF",
    elements=[...],
)
```

`SlideElement` 使用归一化的 16:9 坐标：

```python
SlideElement(
    type="text",
    x=0.08,
    y=0.25,
    w=0.44,
    h=0.55,
    z=2,
    text="...",
    style={...},
)
```

支持的元素类型：

- `text`
- `shape`
- `line`
- `image`
- `table`
- `chart`

Pydantic 会检查元素尺寸非零、坐标范围和 `x + w <= 1`、`y + h <= 1`。

字段语义需要特别区分：

- `layout="freeform"` 目前是兼容字段，表示页面由 `elements` 描述，不表示默认路径由 LLM 自由排版；
- `layout_id` 是布局注册表中的具体骨架身份，例如 `split_left`；
- `elements` 是 PPTX 导出的权威几何结构；只要它非空，renderer 优先编译 `elements`；
- 保留的 LLM 自由场景同样会生成 `elements`，但当前默认生成链路不会调用它。

## 10. 排版约束

`DEFAULT_LAYOUT_CONSTRAINTS` 当前主要配置：

| 约束 | 当前值 |
| --- | ---: |
| 水平画布边距 | `0.055` |
| 垂直画布边距 | `0.055` |
| 标题区域 | `0.05`–`0.20` |
| 内容区域 | `0.22`–`0.94` |
| 内容最小间距 | `0.025` |
| 标题最小字号 | `28 pt` |
| 正文最小字号 | `18 pt` |
| 引用最小字号 | `11 pt` |

保留的自由场景校验代码还会执行：

- 标题和正文安全区检查；
- 文字框高度估算；
- 字号向下适配；
- 文本、图片、表格和图表之间的矩形重叠检查；
- 无非文本视觉元素时判定场景无效。

当前默认确定性布局没有经过同一套 `_normalize_scene()` 路径；其安全性主要由模板坐标、Schema 边界和测试保证。

因此上述约束不能理解为全部都受到运行时硬校验。Schema 会强制画布边界；确定性布局中的字号、间距和安全区主要依靠 builder 实现与测试约定。修改 `build_fallback_elements()` 后，即使视觉上出现重叠，只要元素仍在画布内，任务也可能继续导出。

## 11. 蓝白主题

`brand_palette.py` 定义统一学术蓝白主题：

| 用途 | 色值 |
| --- | --- |
| 封面深蓝 | `12365A` |
| 更深背景 | `0E2742` |
| 内容页背景 | `FFFFFF` |
| 正文深蓝 | `17324D` |
| 主强调蓝 | `2E75B6` |
| 中蓝重点区域 | `4F8FCB` |
| 浅蓝卡片 | `DCEBFA` |
| 次要文字 | `6F8299` |
| 警告/误区 | `C94B5B` |

模型场景如输出其他颜色，会通过最近色映射归一化到允许色板。默认确定性布局直接使用这些 token。

## 12. PPTX 导出

`PPTSkillAdapter._render_basic_pptx()` 使用 `python-pptx`：

- 创建 13.333 × 7.5 英寸的 16:9 演示文稿；
- 为每页创建空白 slide；
- 按 `z` 排序编译 `SlideElement`；
- 创建可编辑文本框、形状、连线、图片、表格和图表；
- 写入字体、字号、颜色、对齐、填充和边框；
- 保存 `deck.pptx`。

文本框配置：

- `word_wrap = True`；
- `auto_size = None`；
- 使用固定内边距；
- 中文优先使用系统可用的 CJK 字体。

关闭 PowerPoint `TEXT_TO_FIT_SHAPE` 是有意设计：规划阶段已经决定字号和框尺寸，再允许 PowerPoint 自动缩放会让浏览器预览与下载文件产生第二套排版结果。

## 13. 讲稿

每页完整讲稿保存在 `SlidePlan.speaker_script`，并额外输出：

```text
speaker_scripts.json
```

视频模块使用 `(speaker_script, slide image)` 生成后续课程视频。

当前默认自由元素导出路径没有把完整讲稿写入 PowerPoint 原生 speaker notes XML。`speaker_scripts.json` 才是完整讲稿的权威产物。旧版非元素布局存在可见的“讲稿摘要”页脚逻辑，但默认元素路径不会执行该逻辑。

## 14. 页面预览

### Linux/服务器

1. 使用 headless LibreOffice 把 PPTX 转成 PDF；
2. 使用 PyMuPDF (`fitz`) 以 1.5 倍矩阵渲染每页 PNG；
3. 校验渲染页数与 `PresentationPlan.slides` 一致。

### macOS

当前直接使用 PIL 声明式渲染预览。原因是 bundled/headless LibreOffice 在 macOS 上无法稳定访问 PingFang 等系统中文字体，可能把中文渲染成方框。

### 降级

Linux 上 LibreOffice 不存在、超时或转换失败时：

- 写入 `render_error.txt`；
- 回退到 PIL 预览。

重要限制：macOS/PIL 预览不是实际 PPTX 的像素级截图，只是根据同一 `SlideElement` 数据重新绘制。最终交付前仍应在真实 PowerPoint 或可靠 LibreOffice 环境检查下载文件。

PIL 预览支持基础 shape、line、image、text、table 和 chart 占位绘制，但不保证完全复现 PowerPoint 的字体替换、文本内边距、透明度、图表样式和图片裁剪。Linux 实际转换的页数不一致会触发渲染异常并进入 PIL 降级；`render_error.txt` 当前没有作为独立 API 字段暴露。

## 15. 数据持久化

`PresentationPlanRecord` 保存：

- plan id；
- content id；
- 标题；
- slides JSON；
- 创建和更新时间。

`PPTGenerationJobRecord` 保存任务状态和 artifact id。

`PPTArtifactRecord` 保存：

- PPTX 路径；
- skill request 路径；
- slide image 元数据；
- plan/job 关联。

Repository 使用 `session.merge()`，同一 ID 再次保存会覆盖该记录。

## 16. 下游模块依赖

### Question Bank

题库在最终 `PresentationPlan` 保存后生成，因此自动续页产生的新 slide id 和 order 会进入题目定位逻辑。

### Classroom

课堂流程按 slide id/order 关联讲解、追问和互动。修改续页、删除页面或重新编号时，需要同步运行课堂相关测试。

### Video

视频服务按 slide id 或 slide order 查找预览图，并将对应 `speaker_script` 作为音频/讲解输入。PPT 页数和预览图页数不一致会导致视频生成失败。

## 17. 已知问题和技术债

### P0：参考文献污染教学内容

content 模块仍允许 `reference` 知识单元进入 section，并可能把 source excerpts 直接拼进 teaching narrative。参考文献页应默认标记为 `reference_only`，只作为引用元数据或附录页来源。

### P0：缺少逐份成品的渲染—修复闭环

项目做过布局样例渲染检查，但每次用户生成 PPT 时还没有自动执行：

```text
真实 PPTX 渲染 -> 视觉检测 -> 问题页修复 -> 再次导出
```

这是与本地 PPTX skill 稳定性差距最大的部分。

### P1：默认布局仍以文字和形状为主

确定性路径当前很少把真实 source image、chart、table 和 formula 自动装入布局。恢复视觉丰富度时，应采用受控 exhibit slots，而不是重新让 LLM 自由生成全部坐标。

### P1：16 个布局标识不是 16 套独立模板

部分布局族共享底层构图分支。后续应为每个布局建立明确槽位、容量、替代布局和视觉快照。

### P1：文字测量仍是估算

planner 使用字符视觉长度、字号和行距估算高度；PIL 预览和 PowerPoint 使用不同字体引擎。需要统一字体资源，或在真实 PPTX 渲染后验证文本溢出。

### P1：单个超长 key point 不会语义拆句

容量切分只在 key point 边界进行。单条超长段落应先基于句子和语义切分，再进入页面容量算法。

### P2：完整讲稿不是原生 PowerPoint notes

讲稿保存在 JSON 和数据库中。若交付场景要求在 PowerPoint “备注”面板查看，需要补充 notes XML 写入能力并添加兼容性测试。

### P2：计划任务状态不持久化

PresentationPlanJob 当前仅存在内存中。多实例部署、进程重启和长任务恢复需要数据库或正式任务队列。

### P2：保留了未启用的自由场景生成代码

`_generate_scene_with_repair()`、`_build_scene_messages()` 和 `_normalize_scene()` 仍保留。接手人需要决定：

- 将其作为图片、图表、公式等特殊页面的受控例外路径；或
- 删除未使用代码，减少维护面。

不建议重新把它设为所有页面的默认路径。

## 18. 建议的后续实施顺序

### 第一阶段：内容卫生和容量引擎

1. 过滤 reference-only 内容；
2. 增加单条长文本的语义拆分；
3. 为每个 slot 建立真实字符/字号容量；
4. 明确“扩框 -> 换布局 -> 拆页 -> 报错”的顺序；
5. 添加“不丢失任何实质性 key point”的属性测试。

### 第二阶段：受控视觉展品

1. 为 evidence、result、formula、image 等页面定义 exhibit slot；
2. 接入 source image；
3. 为真实数据生成 chart/table；
4. 为公式建立独立排版；
5. 每页限制一个主要 exhibit，并增加解释/so-what 区域。

### 第三阶段：渲染 QA 闭环

1. 在统一字体环境生成真实 PPTX 截图；
2. 程序检测越界、重叠、最小间距和低对比度；
3. 可选使用视觉模型判断对齐、重心和可读性；
4. 把问题转成结构化 repair actions；
5. 最多自动修复 1–2 轮；
6. 保存 QA 报告和最终通过状态。

### 第四阶段：工程化

1. 持久化 plan jobs；
2. 接入任务队列；
3. 建立 16 个布局的视觉回归快照；
4. 在 CI 中渲染代表性中文、英文、公式、图片和长文本样例；
5. 增加真实 PowerPoint/LibreOffice 兼容性基线。

## 19. 测试与本地验证

项目 Python 虚拟环境位于：

```text
apps/api/.venv
```

本地启动：

```bash
./scripts/dev.sh
```

或只启动 API：

```bash
source apps/api/.venv/bin/activate
uvicorn metaclass.main:app --reload
```

默认地址为 `http://127.0.0.1:8000`，接口文档为 `/docs`。LLM 配置参考 `.env.example` 中的 `METACLASS_LLM_*`；本地无真实密钥时使用 fake provider。

已有 `content_id` 时，最小 API 链路示例：

```bash
curl -X POST \
  'http://127.0.0.1:8000/api/v1/learning-contents/<content_id>/presentation-plan-jobs'

curl \
  'http://127.0.0.1:8000/api/v1/presentation-plan-jobs/<plan_job_id>'

curl \
  'http://127.0.0.1:8000/api/v1/presentation-plan-jobs/<plan_job_id>/result'

curl -X POST \
  'http://127.0.0.1:8000/api/v1/presentation-plans/<plan_id>/ppt-jobs'

curl \
  'http://127.0.0.1:8000/api/v1/ppt-jobs/<ppt_job_id>'

curl \
  'http://127.0.0.1:8000/api/v1/ppt-jobs/<ppt_job_id>/artifact'
```

材料上传、解析和 `LearningContent` 创建方式见根目录 `README.md` 和 OpenAPI `/docs`。

运行 presentation 相关 Schema/Planner 测试：

```bash
apps/api/.venv/bin/python -m pytest apps/api/tests/test_schemas.py -q
```

运行完整 API 测试：

```bash
apps/api/.venv/bin/python -m pytest apps/api/tests -q
```

运行静态检查：

```bash
apps/api/.venv/bin/ruff check \
  apps/api/src/metaclass/modules/presentation \
  apps/api/tests/test_schemas.py
```

修改布局后至少检查：

- 所有元素仍在 `0–1` 画布范围；
- 标题和正文安全区不冲突；
- 18 pt 正文最小字号仍成立；
- 长文字不会出现 `...` 或 `…` 静默截断；
- 超容量要点生成续页且顺序不变；
- slide id/order 连续且唯一；
- 题库仍覆盖所有最终页面；
- PPTX 页数、预览图数量和 plan slide 数一致；
- 下载文件在 PowerPoint 中可以打开和编辑。

这些测试主要覆盖 Schema、规划器、容量切分、布局边界和服务契约，不等于真实视觉质量保证。当前没有 PowerPoint 像素级回归、统一字体渲染 CI 或逐份成品自动修复测试。

## 20. 常见修改入口

| 需求 | 优先修改位置 |
| --- | --- |
| 修改 LLM PPT 内容要求 | `planner.py::_build_content_messages` |
| 修复兜底讲稿 | `planner.py::_fallback_plan` 和 content 上游 |
| 新增布局 | `layout_registry.py::LAYOUT_REGISTRY` 和 `build_fallback_elements` |
| 修改布局选择 | `layout_registry.py::select_fallback_layout` |
| 修改容量/续页 | `layout_registry.py::split_points_for_layout`、`planner.py::_generate_scenes` |
| 修改字号和安全区 | `layout_constraints.py` |
| 修改主题颜色 | `brand_palette.py` |
| 修改 PPTX 元素实现 | `skill_adapter.py::_add_scene_element` |
| 修改页面预览 | `skill_adapter.py::_render_slide_images` / `_render_scene_preview` |
| 写入原生讲稿备注 | `skill_adapter.py`，需要新增 notes XML 支持 |
| 修改 API/任务状态 | `api.py`、`service.py`、`repository.py` |

## 21. 接手前建议阅读顺序

1. `schemas.py`
2. `service.py`
3. `planner.py::generate` 和 `_generate_scenes`
4. `layout_registry.py`
5. `skill_adapter.py::prepare_request`、`_render_basic_pptx`、`_render_slide_images`
6. `apps/api/tests/test_schemas.py` 中 presentation 相关测试
7. content 模块的 `LearningContent` 生成链路

接手人首先应确认一个架构原则：LLM 可以负责语义判断和内容编辑，但页面几何、容量和最终可读性必须由可验证的确定性代码和真实渲染 QA 兜底。
