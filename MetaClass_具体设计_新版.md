# MetaClass 具体设计（新版）

> 版本说明：本版在原有「学习端 Learner Studio + 教师/创作端 Presenter Studio + 共享底座 Shared Core」结构基础上，根据 OpenMAIC / MAIC 论文的设计思路进行了重整。核心修改是：AI 互动课堂不再被设计成“几个 Agent 自由聊天”，而是设计为 **Read → Plan → Run** 的可控课堂结构。
>
> - **Read**：解析 PPT/PDF 或知识点，生成 PageMetadata 与 LearningContent。
> - **Plan**：基于 LearningContent 生成 ClassroomPlan 和 T9eachingAction。
> - **Run**：由 ClassroomController 调度 TeacherAgent、ClassmateAgent、TAAgent、EvaluatorAgent 执行课堂。

---

# 一、项目定位与总体结构

## 1.1 项目定位

**MetaClass：基于课程材料与知识点生成的双端智能教学平台**

本项目面向两个核心场景：

1. **学习端 Learner Studio**：帮助学习者基于已有 PPT/PDF 或直接输入知识点，生成讲解文档、PPT 讲解视频、AI 互动课堂，并获得学习诊断与总结。
2. **教师 / 创作端 Presenter Studio**：帮助教师、学生汇报者、答辩者理解和组织 PPT，生成逐页讲稿、可能提问、结构诊断，并基于已有材料重构生成风格统一的新 PPT。

系统底层共享同一套课程材料理解能力，包括 PPT/PDF 解析、PageMetadata 生成、LearningContent 构建、PPT 生成、视频生成、课堂计划生成和 AI 课堂运行。

一句话总结：

> 学习端解决“怎么学”，教师/创作端解决“怎么讲、怎么准备”，两端共享同一个课程材料理解底座。

---



## 1.2 总体产品结构

```Plain
PPT/PDF / 知识点输入
        ↓
Shared Core
- PageMetadata
- LearningContent
- ClassroomPlan
- TeachingAction
        ↓
┌───────────────┬───────────────┐
│ 学习端          │ 教师/创作端     │
│ Learner Studio │ Presenter Studio │
└───────────────┴───────────────┘
```

```text
MetaClass
├── 学习端 Learner Studio
│   ├── 入口 A：上传 PPT/PDF 学习
│   │   ├── 解析课程材料
│   │   ├── 生成讲解文档
│   │   ├── 生成 PPT 讲解视频
│   │   ├── 生成连续讲解课堂
│   │   ├── 生成 AI 互动课堂
│   │   └── 学习诊断与总结
│   │
│   └── 入口 B：输入知识点学习
│       ├── 材料库检索相关内容
│       ├── 生成学习大纲
│       ├── 生成讲解文档
│       ├── 生成 PPT
│       ├── 生成讲解视频
│       ├── 生成连续讲解课堂
│       ├── 生成 AI 互动课堂
│       └── 学习诊断与总结
│
├── 教师 / 创作端 Presenter Studio
│   ├── PPT/PDF 逐页解析
│   ├── 逐页讲稿生成
│   ├── 可能提问预测
│   ├── 汇报结构诊断
│   └── 基于已有材料生成风格统一的新 PPT
│
└── 共享底座 Shared Core
    ├── 材料库 Material Store
    ├── PageMetadata 页面元数据
    ├── LearningContent 统一学习内容
    ├── ClassroomPlan 课堂规划
    ├── TeachingAction 教学动作
    ├── LLM Provider
    ├── TTS Provider
    ├── PPT 生成服务
    ├── 视频生成服务
    └── AI 课堂运行服务
```

---



## 1.3 设计参考：OpenMAIC / MAIC 的启发

本项目参考 MAIC 的三个关键设计思想：

### 1. Read / Plan 两阶段课程准备

MAIC 将教师端课程准备分为两个阶段：

```text
Read Stage：
读懂课程材料，抽取每页 slide 的文本内容、视觉内容、页面描述和知识结构。

Plan Stage：
基于结构化课程材料生成教学动作和课堂 Agent，例如 ShowFile、ReadScript、AskQuestion。
```

MetaClass 对应为：

```text
Read：
PPT/PDF / 知识点输入 → PageMetadata → LearningContent

Plan：
LearningContent → ClassroomPlan → TeachingAction

Run：
ClassroomSession + ClassroomController + Agents + StudentState
```



### 2. TeachingAction 教学动作

AI 课堂不应该只是多个 Agent 自由聊天，而应该有可执行、可控制的教学动作。

本项目中的教学动作包括：

```text
ShowSlide：展示当前 PPT 页面
ReadScript：教师讲解当前页面
AskQuestion：教师提问
ClassmateQuestion：AI 同学提问
StartQuiz：开始随堂小测
AnswerUser：回答用户问题
Summarize：助教总结
MoveNext：进入下一页 / 下一场景
ReviewPrevious：回到上一页
Encourage：鼓励与情感支持
```



### 3. ClassroomController 课堂控制器

课堂需要一个控制器判断：

```text
下一步谁说话？
执行什么动作？
是否进入下一页？
是否启动小测？
用户不懂时是否换个例子？
是否需要助教总结？
```

因此，本项目引入 `ClassroomController`，它可以先用规则实现，后续再升级为 LLM Manager Agent。

---



# 二、共享底座 Shared Core 设计

共享底座不直接面向用户展示，它是后端核心能力层。学习端和教师端都依赖这一层。

---



## 2.1 共享底座能力列表

```text
1. 原始文件管理
2. PPT/PDF 页面解析
3. 页面图片渲染
4. PageMetadata 生成
5. 知识点抽取与检索
6. TopicLearningPackage 生成
7. LearningContent 构建
8. ClassroomPlan 生成
9. TeachingAction 生成
10. PPT 自动生成
11. 视频讲解生成
12. AI 课堂运行
13. 教师端讲稿 / 提问 / 结构诊断生成
```

---



## 2.2 核心抽象一：Material

`Material` 表示用户上传的原始文件。

```json
{
  "material_id": "mat_001",
  "filename": "logistic_regression.pptx",
  "file_type": "pptx",
  "page_count": 18,
  "storage_path": "data/raw/mat_001/logistic_regression.pptx",
  "status": "uploaded",
  "created_at": "2026-07-06T20:00:00"
}
```

字段说明：


| 字段           | 含义                                   |
| ------------ | ------------------------------------ |
| material_id  | 原始材料唯一 ID                            |
| filename     | 用户上传的文件名                             |
| file_type    | pptx / pdf / txt 等                   |
| page_count   | 页数                                   |
| storage_path | 本地存储路径                               |
| status       | uploaded / parsing / parsed / failed |
| created_at   | 创建时间                                 |


---



## 2.3 核心抽象二：PageMetadata

`PageMetadata` 是对每页 PPT/PDF 的结构化理解结果。它对应 MAIC 中 slide page + description + knowledge-aware section 的思想。

```json
{
  "page_id": "page_003",
  "material_id": "mat_001",
  "page_number": 3,
  "title": "逻辑回归的基本思想",
  "raw_text": "逻辑回归用于二分类任务……",
  "summary": "本页介绍逻辑回归如何将线性模型输出转化为概率。",
  "knowledge_points": ["逻辑回归", "二分类", "Sigmoid 函数"],
  "prerequisites": ["线性模型", "概率", "二分类任务"],
  "page_type": "概念讲解",
  "page_role": "从分类任务背景过渡到逻辑回归模型思想的核心页面",
  "difficulty": "基础",
  "image_path": "data/processed/mat_001/images/page_003.png",
  "image_description": "页面中包含 Sigmoid 曲线示意图，用于说明概率映射。",
  "teaching_focus": [
    "为什么分类问题需要概率输出",
    "Sigmoid 函数的作用",
    "逻辑回归和线性回归的区别"
  ],
  "possible_questions": [
    "为什么不能直接用线性回归做分类？",
    "Sigmoid 输出为什么可以理解成概率？"
  ],
  "script_seed": "讲解时建议先从二分类例子引入，再解释 Sigmoid 如何把输出映射到 0 到 1。"
}
```



### PageMetadata 的用途

```text
学习端：
- 生成学习讲解文档
- 生成 PPT 讲解视频
- 生成 AI 互动课堂
- 生成小测题
- 支撑学习诊断

教师 / 创作端：
- 生成逐页讲稿
- 预测老师/听众可能提问
- 汇报结构诊断
- 检索相关内容并生成新 PPT
```

---



## 2.4 核心抽象三：LearningContent

`LearningContent` 是视频生成和 AI 课堂的统一输入对象。不管内容来自上传 PPT/PDF、输入知识点后 AI 生成，还是教师端重构生成的新 PPT，最终都要转成 `LearningContent`。

```json
{
  "content_id": "content_001",
  "source_type": "uploaded_material",
  "source_id": "mat_001",
  "title": "逻辑回归",
  "course_structure": [
    {
      "section_id": "sec_001",
      "title": "分类问题背景",
      "page_ids": ["page_001", "page_002"],
      "knowledge_points": ["分类任务", "概率输出"]
    },
    {
      "section_id": "sec_002",
      "title": "逻辑回归模型",
      "page_ids": ["page_003", "page_004"],
      "knowledge_points": ["逻辑回归", "Sigmoid 函数"]
    }
  ],
  "pages": [
    {
      "page_id": "page_003",
      "title": "逻辑回归的基本思想",
      "content": "本页介绍逻辑回归如何用于二分类问题……",
      "summary": "逻辑回归通过 Sigmoid 函数将线性输出映射为概率。",
      "knowledge_points": ["逻辑回归", "Sigmoid 函数"],
      "speaker_notes": "先讲分类问题，再引出概率输出。"
    }
  ]
}
```

后端设计原则：

> 视频生成服务、AI 课堂服务、学习诊断服务都只依赖 LearningContent，而不直接依赖 PPT/PDF 或 Topic 原始输入。

---



## 2.5 核心抽象四：TopicLearningPackage

当用户不上传 PPT，只输入知识点时，系统生成 `TopicLearningPackage`。

```json
{
  "topic_project_id": "topic_001",
  "topic": "最大似然估计",
  "goal": "通俗理解并能做简单题",
  "duration": 10,
  "style": "通俗讲解",
  "outline": [
    {
      "section_id": "sec_001",
      "title": "为什么需要参数估计",
      "goal": "理解从数据中估计模型参数的动机",
      "key_points": ["数据", "模型", "参数"]
    },
    {
      "section_id": "sec_002",
      "title": "似然的直观含义",
      "goal": "用生活例子解释似然",
      "key_points": ["似然", "参数", "观测数据"]
    }
  ],
  "document_id": "doc_001",
  "generated_ppt_id": "ppt_001",
  "learning_content_id": "content_002"
}
```

---



## 2.6 核心抽象五：ClassroomPlan

`ClassroomPlan` 表示一节 AI 课堂的预生成课堂计划。它不随用户交互变化，是课堂运行前的结构化脚本。

```json
{
  "plan_id": "plan_001",
  "content_id": "content_001",
  "title": "逻辑回归互动课堂",
  "mode_options": ["continuous", "interactive"],
  "sections": [
    {
      "section_id": "sec_002",
      "title": "逻辑回归模型",
      "scene_ids": ["scene_003", "scene_004"]
    }
  ],
  "scenes": [
    {
      "scene_id": "scene_003",
      "scene_type": "lecture",
      "page_id": "page_003",
      "title": "Sigmoid 函数的作用",
      "learning_objective": "理解 Sigmoid 如何把线性输出转为概率",
      "action_ids": ["action_001", "action_002", "action_003"]
    }
  ]
}
```

---



## 2.7 核心抽象六：TeachingAction

`TeachingAction` 是 AI 课堂中的原子动作，参考 MAIC 中 `T = (type, value)` 的设计。

### 示例 1：ShowSlide

```json
{
  "action_id": "action_001",
  "plan_id": "plan_001",
  "scene_id": "scene_003",
  "type": "ShowSlide",
  "agent": "System",
  "order": 1,
  "value": {
    "page_id": "page_003",
    "image_path": "data/processed/mat_001/images/page_003.png"
  }
}
```



### 示例 2：ReadScript

```json
{
  "action_id": "action_002",
  "plan_id": "plan_001",
  "scene_id": "scene_003",
  "type": "ReadScript",
  "agent": "TeacherAgent",
  "order": 2,
  "value": {
    "script": "这一页我们来看 Sigmoid 函数。它可以把任意实数映射到 0 到 1 之间，因此可以把模型输出解释为概率。"
  }
}
```



### 示例 3：AskQuestion

```json
{
  "action_id": "action_003",
  "plan_id": "plan_001",
  "scene_id": "scene_003",
  "type": "AskQuestion",
  "agent": "TeacherAgent",
  "order": 3,
  "value": {
    "question": "为什么线性回归的输出不能直接作为概率？",
    "expected_answer": "因为线性回归输出可能小于 0 或大于 1，而概率必须在 0 到 1 之间。"
  }
}
```

---



# 三、学习端 Learner Studio 设计

学习端提供两条入口：

```text
路径 A：上传 PPT/PDF 学习
路径 B：输入知识点学习
```

两条路径最终都可以生成：

```text
讲解文档
PPT 讲解视频
连续讲解课堂
AI 互动课堂
学习诊断与总结
```

---



## 3.1 学习端首页

```text
你想如何开始学习？

[上传课程材料]
上传 PPT/PDF，AI 基于已有材料生成讲解文档、讲解视频或互动课堂。

[输入学习主题]
输入一个知识点，AI 自动生成讲解文档、PPT、视频课程或互动课堂。
```

---



## 3.2 路径 A：上传 PPT/PDF 学习



### A1. 用户上传材料

用户上传：

```text
PPTX / PDF
```

后端执行：

```text
1. 保存文件到 data/raw
2. 创建 Material 记录
3. 解析文本
4. 渲染页面图片
5. 调用 LLM 生成 PageMetadata
6. 生成 LearningContent
```

前端展示：

```text
文件名
页数
主要知识点
每页摘要
推荐学习方式
```

---



### A2. 用户选择学习方式

解析完成后，学习端给出并列选项：

```text
[查看讲解文档]
[生成讲解视频]
[连续讲解课堂]
[互动课堂]
```

区别：

```text
讲解文档：适合快速复习。
讲解视频：适合被动观看。
连续讲解课堂：AI 教师连续讲解，用户可以随时打断提问。
互动课堂：AI 教师、AI 同学、AI 助教、小测共同参与。
```

---



### A3. 生成讲解文档

讲解文档面向学生学习，不是逐页讲稿的简单拼接。

结构：

```text
1. 学习目标
2. 核心知识点概览
3. 分页讲解
4. 重点概念解释
5. 常见误区
6. 自测题
7. 总结与复习
```

`LearningDocument` 示例：

```json
{
  "document_id": "doc_001",
  "content_id": "content_001",
  "title": "逻辑回归学习讲义",
  "sections": [
    {
      "title": "什么是逻辑回归",
      "content": "逻辑回归是一种用于二分类任务的模型……",
      "related_pages": ["page_003"],
      "key_points": ["二分类", "概率输出"]
    }
  ],
  "quiz": [
    {
      "question": "为什么逻辑回归输出可以解释为概率？",
      "answer": "因为 Sigmoid 函数将任意实数映射到 0 到 1 之间。"
    }
  ]
}
```

---



### A4. 生成讲解视频

视频生成流程：

```text
LearningContent
↓
ClassroomPlanService 生成 ReadScript 类型 TeachingAction
↓
VideoScriptAgent 将 ReadScript 改写为适合配音的讲稿
↓
RenderService 获取每页图片
↓
TTSService 生成逐页音频
↓
SubtitleService 生成 SRT 字幕
↓
VideoService 用 ffmpeg/moviepy 合成视频
↓
输出 MP4
```

视频参数：

```json
{
  "content_id": "content_001",
  "style": "课堂讲解型",
  "duration_mode": "standard",
  "voice": "female",
  "subtitle": true
}
```

`VideoJob` 是视频生成任务对象，用于记录生成进度，因为视频生成是一个较慢的异步过程。

```json
{
  "video_id": "video_001",
  "content_id": "content_001",
  "status": "processing",
  "progress": 45,
  "current_step": "generating_audio",
  "script_path": "data/videos/video_001/script.json",
  "audio_dir": "data/videos/video_001/audio",
  "subtitle_path": "data/videos/video_001/subtitles.srt",
  "video_path": null,
  "error_message": null
}
```

前端通过状态接口轮询：

```text
GET /api/videos/{video_id}/status
```

完成后返回：

```json
{
  "video_id": "video_001",
  "status": "finished",
  "progress": 100,
  "video_url": "/static/videos/video_001/output.mp4"
}
```

第一版视频效果：

```text
静态 PPT 页面
AI 配音
底部字幕
简单淡入淡出
```

暂不做数字人、复杂动画、口型同步。

---



### A5. 连续讲解课堂

连续讲解课堂介于“视频”和“互动课堂”之间。

特点：

```text
AI 教师按照 ClassroomPlan 连续执行 ReadScript 动作。
AI 同学默认不频繁插话。
用户可以随时暂停、提问、要求回到上一页、要求讲简单点。
```

适合场景：

```text
用户想快速听完一节课，但保留随时打断提问的能力。
```

---



### A6. AI 互动课堂

互动课堂会穿插：

```text
教师讲解
AI 同学提问
用户讨论
随堂小测
助教总结
评价诊断
```

它的详细设计见第四部分。

---



## 3.3 路径 B：输入知识点学习



### B1. 用户输入学习需求

表单：

```json
{
  "topic": "最大似然估计",
  "goal": "通俗理解并能做简单题",
  "duration": 10,
  "style": "通俗讲解",
  "outputs": ["document", "ppt", "video", "classroom"]
}
```

---



### B2. 材料库检索

用户输入知识点后，系统先检索共享材料库：

```text
用户输入知识点
↓
RetrievalService 检索 PageMetadata
↓
如果找到相关材料：
    使用已有材料 + AI 补充生成学习内容
如果没有找到：
    完全由 AI 生成学习大纲、讲义和 PPT
↓
统一转成 LearningContent
```

---



### B3. 生成学习大纲

后端调用 `TopicOutlineAgent`：

```json
{
  "topic": "最大似然估计",
  "outline": [
    {
      "title": "为什么需要参数估计",
      "goal": "理解从数据中估计模型参数的动机"
    },
    {
      "title": "似然的直观含义",
      "goal": "用抛硬币例子解释似然"
    },
    {
      "title": "最大似然估计的核心思想",
      "goal": "理解选择最能解释观测数据的参数"
    },
    {
      "title": "常见误区",
      "goal": "区分概率、似然和参数"
    }
  ]
}
```

---



### B4. 生成讲解文档

后端调用 `DocumentAgent`，生成结构化讲义：

```text
1. 学习目标
2. 概念解释
3. 直观例子
4. 公式说明
5. 常见误区
6. 小测验
7. 总结
```

---



### B5. 生成 PPT

后端调用 `PPTAgent` 生成 PPT JSON，再用 `python-pptx` 生成 PPTX。

```json
{
  "title": "最大似然估计入门",
  "slides": [
    {
      "slide_number": 1,
      "layout": "title",
      "title": "最大似然估计",
      "bullets": ["从数据中估计参数的一种方法"],
      "speaker_notes": "这一页先说明本节课要解决什么问题。"
    },
    {
      "slide_number": 2,
      "layout": "content",
      "title": "为什么需要参数估计？",
      "bullets": [
        "模型中有未知参数",
        "数据可以帮助我们推断参数",
        "参数决定模型如何解释现象"
      ],
      "speaker_notes": "用抛硬币中硬币正面概率未知的例子引入。"
    }
  ]
}
```

生成后保存：

```text
data/generated/ppt/topic_001.pptx
```

同时转成 `LearningContent`，供视频和课堂复用。

---



### B6. 生成视频 / 连续讲解课堂 / AI 互动课堂

这三类输出都复用 `LearningContent`：

```text
TopicLearningPackage
↓
Generated PPT
↓
LearningContent
↓
讲解视频 / 连续讲解课堂 / AI 互动课堂
```

---



# 四、AI 互动课堂设计

本项目的 AI 互动课堂参考 OpenMAIC 的思想，但做轻量化实现。

核心结构：

```text
AI 互动课堂 =
ClassroomPlan
+ TeachingActions
+ ClassroomSession
+ Agents
+ StudentState
+ ClassroomController
```

---



## 4.1 ClassroomPlan 与 ClassroomSession 的区别

```text
ClassroomPlan：
课前生成的课堂计划，不随用户交互变化。

ClassroomSession：
用户真实上课时的运行状态，会随着用户互动和学习诊断不断更新。
```

---



## 4.2 ClassroomPlan

```json
{
  "plan_id": "plan_001",
  "content_id": "content_001",
  "title": "逻辑回归互动课堂",
  "scenes": [
    {
      "scene_id": "scene_003",
      "scene_type": "lecture",
      "title": "Sigmoid 函数的作用",
      "page_id": "page_003",
      "learning_objective": "理解 Sigmoid 如何把线性输出转为概率",
      "action_ids": ["action_001", "action_002", "action_003"]
    }
  ]
}
```

---



## 4.3 ClassroomSession

```json
{
  "session_id": "classroom_001",
  "plan_id": "plan_001",
  "content_id": "content_001",
  "current_scene_id": "scene_003",
  "current_action_id": "action_002",
  "mode": "interactive",
  "status": "active",
  "created_at": "2026-07-06T20:30:00"
}
```

---



## 4.4 Scene 类型

第一版支持 5 类 Scene：

```text
LectureScene
- 教师 Agent 讲解当前页或当前知识点。

DiscussionScene
- AI 同学提出问题，用户可以参与讨论。

QuizScene
- 系统出 1-3 道随堂小测，EvaluatorAgent 批改。

SummaryScene
- TAAgent 总结本节内容并给复习建议。

ReviewScene
- 针对用户薄弱点回顾前置知识。
```

---



## 4.5 TeachingAction 类型

第一版支持：

```text
ShowSlide
ReadScript
AskQuestion
ClassmateQuestion
StartQuiz
AnswerUser
Summarize
MoveNext
ReviewPrevious
Encourage
```

---



## 4.6 Agent 设计



### 4.6.1 TeacherAgent

职责：

```text
执行 ReadScript / AskQuestion / AnswerUser
基于当前页讲解
回答用户问题
根据用户理解程度换一种讲法
必要时举例或类比
```

输入：

```json
{
  "scene": {},
  "action": {},
  "page": {},
  "student_state": {},
  "user_input": "我不懂为什么要用 Sigmoid"
}
```

输出：

```json
{
  "role": "teacher",
  "content": "可以这样理解：线性模型的输出可能是任意实数，但概率必须在 0 到 1 之间……",
  "next_question": "你能说说为什么概率不能大于 1 吗？"
}
```

---



### 4.6.2 ClassmateAgent

借鉴 MAIC 中 AI classmates 的设计，但改成更适合中文课堂的角色。

```text
BasicStudent：
问基础概念、定义、符号、前置知识。

DeepThinker：
提出更深层的为什么，以及和其他知识的联系。

NoteTaker：
阶段性总结本页重点，帮助用户记忆。

ExamStudent：
问考点、题型、易错点。
```

MVP 可以先实现：

```text
BasicStudent
DeepThinker
NoteTaker
```

输出示例：

```json
[
  {
    "agent": "BasicStudent",
    "content": "老师，Sigmoid 是不是只是把数缩放到 0 到 1？"
  },
  {
    "agent": "DeepThinker",
    "content": "为什么这里一定要用 Sigmoid，而不是别的函数？"
  }
]
```

---



### 4.6.3 TAAgent

职责：

```text
执行 Summarize
整理课堂笔记
提炼重点
提炼易错点
给复习建议
必要时提醒课堂不要跑题
```

输出：

```json
{
  "summary": "本轮主要讲了 Sigmoid 函数为什么适合表示概率。",
  "key_points": ["输出范围为 0 到 1", "适合二分类概率建模"],
  "mistakes": ["不要把 Sigmoid 简单理解为普通归一化"],
  "review_suggestion": "建议接下来通过函数图像和数值例子巩固。"
}
```

---



### 4.6.4 EvaluatorAgent

职责：

```text
批改用户回答
判断掌握度
识别误区
建议下一步教学策略
```

输出：

```json
{
  "score": 0.45,
  "diagnosis": "用户理解了概率输出的需求，但没有说清 Sigmoid 的映射机制。",
  "misconceptions": [
    "把 Sigmoid 理解为普通归一化函数"
  ],
  "next_action": "give_example"
}
```

---



### 4.6.5 ClassroomController

`ClassroomController` 是课堂运行核心。第一版可以用规则实现，后续再升级为 LLM Manager Agent。

职责：

```text
观察当前课堂状态
判断用户输入类型
决定下一步 TeachingAction
决定哪个 Agent 发言
决定是否进入下一页 / 小测 / 回顾 / 总结
```

输入：

```json
{
  "current_scene": {},
  "current_action": {},
  "dialogue_history": [],
  "student_state": {},
  "user_input": "我不懂为什么要用 Sigmoid",
  "mode": "interactive"
}
```

输出：

```json
{
  "next_action_type": "AnswerUser",
  "agent": "TeacherAgent",
  "reason": "用户表达不理解，需要教师换一种方式解释",
  "stay_on_current_scene": true
}
```

---



## 4.7 两种课堂模式



### continuous 模式

```text
AI 教师按教学动作连续讲解。
用户可以随时暂停、提问、要求返回上一页。
AI 同学默认不频繁插话。
适合用户想高效听课。
```



### interactive 模式

```text
每一页穿插同学提问、小测、助教总结。
用户更频繁参与。
适合深度学习和复习。
```

---



## 4.8 StudentState 学习状态

```json
{
  "session_id": "classroom_001",
  "current_knowledge_point": "Sigmoid 函数",
  "covered_pages": ["page_001", "page_002", "page_003"],
  "mastery": {
    "逻辑回归": 0.65,
    "Sigmoid 函数": 0.45,
    "最大似然估计": 0.30
  },
  "misconceptions": [
    {
      "knowledge_point": "Sigmoid 函数",
      "description": "把 Sigmoid 理解为普通归一化函数",
      "evidence": "用户回答：它就是把数变成0到1"
    }
  ],
  "quiz_results": [
    {
      "quiz_id": "quiz_001",
      "score": 0.5,
      "feedback": "概念部分正确，但缺少函数映射解释。"
    }
  ],
  "engagement": {
    "question_count": 3,
    "quiz_attempts": 2,
    "last_active_time": "2026-07-06T20:45:00"
  },
  "next_suggestion": "建议用图像和数值例子继续学习 Sigmoid。"
}
```

前端显示：

```text
当前知识点
掌握度条
主要误区
小测结果
下一步建议
课堂总结
```

---



## 4.9 AI 课堂前端界面

```text
ClassroomPage
├── 左侧：课程目录 / Scene 列表
│   ├── 第 1 页：问题背景
│   ├── 第 2 页：逻辑回归思想
│   ├── Quiz：随堂小测
│   └── Summary：本节总结
│
├── 中间：AI 课堂舞台
│   ├── 当前 PPT 页面
│   ├── TeacherAgent 气泡
│   ├── ClassmateAgent 气泡
│   ├── TAAgent 总结卡片
│   └── Quiz 卡片
│
├── 右侧：学习状态
│   ├── 当前知识点
│   ├── 掌握度
│   ├── 主要误区
│   ├── 小测结果
│   └── 下一步建议
│
└── 底部：用户控制区
    ├── 输入框
    ├── 讲简单点
    ├── 举个例子
    ├── 出一道题
    ├── 回到上一页
    ├── 下一页
    └── 总结一下
```

---



# 五、教师 / 创作端 Presenter Studio 设计

教师 / 创作端面向备课、课程汇报、开题答辩和结题展示。

核心功能：

```text
1. PPT 逐页解析与讲稿生成
2. 可能提问预测
3. 汇报结构诊断
4. 基于已有材料生成风格统一的新 PPT
```

所有生成结果都应该支持用户查看、修改、删除、重新生成和确认保存。

---



## 5.1 功能 A：PPT 逐页解析与讲稿生成

输入：

```text
PPT / PDF
```

输出：

```text
每页摘要
本页核心知识点
本页在汇报中的作用
本页讲稿
页间过渡语
```

讲稿生成输入应该包含：

```text
当前页 PageMetadata
前一页 summary
后一页 summary
整份 PPT 主题
目标受众
汇报时长
```

输出示例：

```json
{
  "page_id": "page_006",
  "title": "最大似然估计",
  "summary": "本页介绍逻辑回归参数学习的基本思想。",
  "presentation_role": "从模型形式过渡到参数估计的关键页面。",
  "script": "这一页我们进入逻辑回归的参数估计问题。前面我们已经知道模型可以输出概率，那么接下来要解决的问题是参数如何确定……",
  "transition": "理解模型如何输出概率后，下一步我们来看参数如何学习。"
}
```

---



## 5.2 功能 B：可能提问预测

系统根据整份 PPT 生成：

```text
老师可能问的问题
听众可能听不懂的问题
容易被追问的细节
建议回答
```

问题类型：

```text
基础理解题
理论追问题
方法比较题
应用扩展题
质疑挑战题
```

输出示例：

```json
{
  "questions": [
    {
      "question": "为什么逻辑回归不用最小二乘，而使用最大似然估计？",
      "type": "理论追问",
      "difficulty": "中等",
      "suggested_answer": "因为逻辑回归建模的是二分类概率输出，更自然的概率假设是伯努利分布，因此常通过最大化似然或最小化交叉熵来估计参数。"
    }
  ]
}
```

---



## 5.3 功能 C：汇报结构诊断

诊断维度：

```text
逻辑是否清晰
前后衔接是否自然
是否缺少背景
是否缺少总结
哪些页面重复
哪些页面过难
预计时间是否超时
```

输出示例：

```json
{
  "overall_comment": "整体结构较完整，但第 5-7 页公式密度较高，建议增加直观例子。",
  "missing_parts": ["缺少总结页"],
  "redundant_parts": ["第 5 页和第 6 页内容略重复"],
  "difficult_pages": [6, 7],
  "transition_issues": [
    "第 4 页到第 5 页缺少从模型思想到公式推导的过渡"
  ],
  "problems": [
    {
      "page": 6,
      "issue": "最大似然估计直接进入公式，缺少直观解释。",
      "suggestion": "加入抛硬币例子。"
    }
  ],
  "estimated_duration": "12 min",
  "recommended_duration": "10 min"
}
```

---



## 5.4 功能 D：基于已有材料生成风格统一的新 PPT

这个功能不要理解为“拼接 PPT 页面”，而是：

> 基于已有材料的内容重构生成 PPT。

原因：

```text
不同 PPT 直接拼接风格不统一。
页面级检索可能不够准确。
原页面不一定适合新的汇报目标。
```

更合理的流程：

```text
用户输入汇报需求
↓
RetrievalService 检索相关 PageMetadata
↓
返回候选内容
↓
用户手动选择 / 排序
↓
OutlineAgent 生成汇报大纲
↓
PPTAgent 生成统一风格 PPT JSON
↓
python-pptx 生成 PPTX
↓
PresenterScriptAgent 生成逐页讲稿
```

输入：

```json
{
  "topic": "逻辑回归 10 分钟课程汇报",
  "audience": "本科生",
  "requirements": "包含背景、模型思想、参数估计和案例",
  "duration": 10
}
```

输出：

```json
{
  "new_ppt_id": "ppt_rebuild_001",
  "outline": [
    "问题背景",
    "模型思想",
    "Sigmoid 函数",
    "参数估计",
    "案例总结"
  ],
  "ppt_path": "data/generated/presenter/ppt_rebuild_001.pptx",
  "script_path": "data/generated/presenter/ppt_rebuild_001_script.json"
}
```

MVP 可以先做：

```text
检索候选页
用户选择
生成大纲
生成 PPT JSON
用统一模板生成 PPTX
```

暂不做复杂 PPT 编辑器。

---



# 六、后端总体设计



## 6.1 技术栈建议

```text
后端：FastAPI
前端：React + Vite
数据库：SQLite，后续可换 PostgreSQL
文件存储：本地 data/ 目录
向量检索：Chroma / FAISS
PPT 解析：python-pptx
PDF 解析：pdfplumber / PyMuPDF
PPT 渲染：LibreOffice headless + PyMuPDF
PPT 生成：python-pptx
TTS：edge-tts / 第三方 TTS API
视频合成：ffmpeg / moviepy
LLM 调用：统一封装 llm_service.py
异步任务：FastAPI BackgroundTasks，后续可换 Celery / RQ
```

MVP 最稳：

```text
FastAPI + SQLite + 本地文件 + Chroma + python-pptx + ffmpeg
```

---



## 6.2 后端目录结构

```text
backend/
├── main.py
├── config.py
├── api/
│   ├── material.py
│   ├── learner.py
│   ├── topic.py
│   ├── video.py
│   ├── classroom.py
│   ├── presenter.py
│   └── diagnosis.py
│
├── services/
│   ├── material_service.py
│   ├── parser_service.py
│   ├── metadata_service.py
│   ├── learning_content_service.py
│   ├── topic_service.py
│   ├── document_service.py
│   ├── ppt_generate_service.py
│   ├── retrieval_service.py
│   ├── render_service.py
│   ├── tts_service.py
│   ├── subtitle_service.py
│   ├── video_service.py
│   ├── classroom_plan_service.py
│   ├── teaching_action_service.py
│   ├── classroom_runtime_service.py
│   ├── classroom_controller.py
│   ├── diagnosis_service.py
│   ├── presenter_service.py
│   └── llm_service.py
│
├── agents/
│   ├── topic_outline_agent.py
│   ├── document_agent.py
│   ├── ppt_agent.py
│   ├── video_script_agent.py
│   ├── teacher_agent.py
│   ├── classmate_agent.py
│   ├── ta_agent.py
│   ├── evaluator_agent.py
│   ├── presenter_script_agent.py
│   ├── question_agent.py
│   └── structure_agent.py
│
├── models/
│   ├── material.py
│   ├── page_metadata.py
│   ├── learning_content.py
│   ├── topic_project.py
│   ├── generated_ppt.py
│   ├── video_job.py
│   ├── classroom_plan.py
│   ├── teaching_action.py
│   ├── classroom_session.py
│   ├── diagnosis.py
│   └── presenter.py
│
├── db/
│   ├── database.py
│   └── schema.sql
│
└── utils/
    ├── file_utils.py
    ├── json_utils.py
    ├── ffmpeg_utils.py
    └── schema_validator.py
```

---



## 6.3 数据目录结构

```text
data/
├── raw/
│   └── mat_001/
│       └── source.pptx
│
├── processed/
│   └── mat_001/
│       ├── pages.json
│       ├── metadata.json
│       ├── images/
│       │   ├── page_001.png
│       │   └── page_002.png
│       └── content.json
│
├── generated/
│   ├── topics/
│   │   └── topic_001/
│   │       ├── outline.json
│   │       ├── document.json
│   │       ├── generated.pptx
│   │       └── content.json
│   │
│   └── presenter/
│       └── project_001/
│           ├── questions.json
│           ├── structure_analysis.json
│           └── rebuilt.pptx
│
├── videos/
│   └── video_001/
│       ├── script.json
│       ├── slides/
│       ├── audio/
│       ├── subtitles.srt
│       └── output.mp4
│
└── classrooms/
    └── classroom_001/
        ├── plan.json
        ├── actions.json
        ├── messages.json
        └── state.json
```

---



# 七、数据库表设计



## 7.1 materials

```sql
CREATE TABLE materials (
  material_id TEXT PRIMARY KEY,
  filename TEXT,
  file_type TEXT,
  storage_path TEXT,
  page_count INTEGER,
  status TEXT,
  created_at TEXT
);
```

---



## 7.2 page_metadata

```sql
CREATE TABLE page_metadata (
  page_id TEXT PRIMARY KEY,
  material_id TEXT,
  page_number INTEGER,
  title TEXT,
  summary TEXT,
  raw_text TEXT,
  knowledge_points TEXT,
  page_type TEXT,
  difficulty TEXT,
  image_path TEXT,
  metadata_json TEXT,
  created_at TEXT
);
```

---



## 7.3 learning_contents

```sql
CREATE TABLE learning_contents (
  content_id TEXT PRIMARY KEY,
  source_type TEXT,
  source_id TEXT,
  title TEXT,
  content_json TEXT,
  created_at TEXT
);
```

---



## 7.4 topic_projects

```sql
CREATE TABLE topic_projects (
  topic_project_id TEXT PRIMARY KEY,
  topic TEXT,
  goal TEXT,
  duration INTEGER,
  style TEXT,
  outline_json TEXT,
  document_json TEXT,
  generated_ppt_path TEXT,
  learning_content_id TEXT,
  created_at TEXT
);
```

---



## 7.5 video_jobs

```sql
CREATE TABLE video_jobs (
  video_id TEXT PRIMARY KEY,
  content_id TEXT,
  status TEXT,
  progress INTEGER,
  current_step TEXT,
  video_path TEXT,
  config_json TEXT,
  error_message TEXT,
  created_at TEXT,
  updated_at TEXT
);
```

---



## 7.6 classroom_plans

```sql
CREATE TABLE classroom_plans (
  plan_id TEXT PRIMARY KEY,
  content_id TEXT,
  title TEXT,
  plan_json TEXT,
  created_at TEXT
);
```

---



## 7.7 teaching_actions

```sql
CREATE TABLE teaching_actions (
  action_id TEXT PRIMARY KEY,
  plan_id TEXT,
  scene_id TEXT,
  action_type TEXT,
  agent_name TEXT,
  value_json TEXT,
  action_order INTEGER,
  created_at TEXT
);
```

---



## 7.8 classroom_sessions

```sql
CREATE TABLE classroom_sessions (
  session_id TEXT PRIMARY KEY,
  plan_id TEXT,
  content_id TEXT,
  current_scene_id TEXT,
  current_action_id TEXT,
  mode TEXT,
  state_json TEXT,
  created_at TEXT,
  updated_at TEXT
);
```

---



## 7.9 classroom_messages

```sql
CREATE TABLE classroom_messages (
  message_id TEXT PRIMARY KEY,
  session_id TEXT,
  role TEXT,
  agent_name TEXT,
  content TEXT,
  created_at TEXT
);
```

---



## 7.10 presenter_projects

```sql
CREATE TABLE presenter_projects (
  project_id TEXT PRIMARY KEY,
  material_id TEXT,
  title TEXT,
  script_json TEXT,
  questions_json TEXT,
  structure_analysis_json TEXT,
  rebuilt_ppt_path TEXT,
  created_at TEXT
);
```

---



# 八、API 设计



## 8.1 通用材料接口



### 上传材料

```http
POST /api/materials/upload
```

返回：

```json
{
  "material_id": "mat_001",
  "status": "uploaded"
}
```

---



### 解析材料

```http
POST /api/materials/{material_id}/parse
```

返回：

```json
{
  "material_id": "mat_001",
  "status": "parsed",
  "page_count": 18,
  "content_id": "content_001"
}
```

---



### 获取页面 metadata

```http
GET /api/materials/{material_id}/pages
```

---



## 8.2 学习端：上传材料路径



### 基于材料生成讲解文档

```http
POST /api/learner/materials/{material_id}/document
```

---



### 基于材料生成视频

```http
POST /api/learner/materials/{material_id}/video
```

请求：

```json
{
  "style": "课堂讲解型",
  "duration_mode": "standard",
  "voice": "female",
  "subtitle": true
}
```

---



### 基于材料创建 ClassroomPlan

```http
POST /api/learner/materials/{material_id}/classroom-plan
```

---



### 基于 ClassroomPlan 创建课堂 Session

```http
POST /api/classrooms/sessions
```

请求：

```json
{
  "plan_id": "plan_001",
  "mode": "interactive"
}
```

---



## 8.3 学习端：输入知识点路径



### 创建 topic 学习项目

```http
POST /api/learner/topics
```

请求：

```json
{
  "topic": "最大似然估计",
  "goal": "通俗理解并能做简单题",
  "duration": 10,
  "style": "通俗讲解"
}
```

---



### 生成讲解文档

```http
POST /api/learner/topics/{topic_project_id}/document
```

---



### 生成 PPT

```http
POST /api/learner/topics/{topic_project_id}/ppt
```

---



### 生成视频

```http
POST /api/learner/topics/{topic_project_id}/video
```

---



### 创建 ClassroomPlan

```http
POST /api/learner/topics/{topic_project_id}/classroom-plan
```

---



## 8.4 视频接口



### 查询视频状态

```http
GET /api/videos/{video_id}/status
```

返回：

```json
{
  "video_id": "video_001",
  "status": "finished",
  "progress": 100,
  "video_url": "/static/videos/video_001/output.mp4"
}
```

---



### 下载视频

```http
GET /api/videos/{video_id}/download
```

---



## 8.5 AI 课堂接口



### 获取课堂详情

```http
GET /api/classrooms/{session_id}
```

---



### 单轮交互

```http
POST /api/classrooms/{session_id}/turn
```

请求：

```json
{
  "user_input": "我不懂为什么要用 Sigmoid",
  "mode": "question"
}
```

返回：

```json
{
  "decision": {},
  "response": {},
  "state": {}
}
```

---



### 用户主动触发课堂动作

```http
POST /api/classrooms/{session_id}/action
```

请求：

```json
{
  "action": "explain_simpler",
  "user_input": "讲简单一点"
}
```

可选 action：

```text
continue
pause
next_page
previous_page
explain_simpler
give_example
start_quiz
summarize
ask_question
```

---



### 提交小测答案

```http
POST /api/classrooms/{session_id}/quiz/{quiz_id}/submit
```

---



### 获取学习诊断

```http
GET /api/classrooms/{session_id}/diagnosis
```

---



## 8.6 教师 / 创作端接口



### 创建 Presenter 项目

```http
POST /api/presenter/projects
```

---



### 生成逐页讲稿

```http
POST /api/presenter/projects/{project_id}/script
```

---



### 生成可能提问

```http
POST /api/presenter/projects/{project_id}/questions
```

---



### 汇报结构诊断

```http
POST /api/presenter/projects/{project_id}/structure-analysis
```

---



### 基于已有材料生成新 PPT

```http
POST /api/presenter/projects/{project_id}/rebuild-ppt
```

---



# 九、服务层设计



## 9.1 material_service.py

负责：

```text
保存上传文件
创建 Material
获取 Material
管理文件路径
```

---



## 9.2 parser_service.py

负责：

```text
PPTX 文本抽取
PDF 文本抽取
PPT/PDF 页面渲染
提取图片路径
```

---



## 9.3 metadata_service.py

负责：

```text
调用 LLM 生成 PageMetadata
做 JSON schema 校验
失败重试
保存 metadata
```

---



## 9.4 learning_content_service.py

负责：

```text
将 PageMetadata 转成 LearningContent
将 TopicLearningPackage 转成 LearningContent
给视频和课堂提供统一输入
```

---



## 9.5 topic_service.py

负责：

```text
创建知识点学习项目
检索材料库相关 PageMetadata
生成学习大纲
管理 TopicLearningPackage
```

---



## 9.6 document_service.py

负责：

```text
生成学习讲解文档
生成自测题
生成复习建议
```

---



## 9.7 ppt_generate_service.py

负责：

```text
LLM 生成 PPT JSON
python-pptx 生成 PPTX
应用统一模板
转成 LearningContent
```

---



## 9.8 video_service.py

负责：

```text
组织视频生成任务
调用 render_service
调用 video_script_agent
调用 tts_service
调用 subtitle_service
调用 ffmpeg
更新 video_job 状态
```

---



## 9.9 classroom_plan_service.py

负责：

```text
根据 LearningContent 生成 ClassroomPlan
生成 LectureScene / DiscussionScene / QuizScene / SummaryScene / ReviewScene
保存 plan
```

---



## 9.10 teaching_action_service.py

负责：

```text
根据 ClassroomPlan 和 LearningContent 生成 TeachingAction
管理 action 顺序
根据 action_id 返回当前动作
```

---



## 9.11 classroom_runtime_service.py

负责：

```text
创建 ClassroomSession
获取当前 session
保存消息历史
更新当前 scene / action
保存 StudentState
```

---



## 9.12 classroom_controller.py

负责：

```text
观察当前 ClassroomSession
判断用户输入类型
决定下一步 TeachingAction
决定哪个 Agent 响应
```

---



## 9.13 diagnosis_service.py

负责：

```text
更新 StudentState
维护 mastery
维护 misconceptions
维护 quiz_results
生成 next_suggestion
```

---



## 9.14 presenter_service.py

负责：

```text
生成讲稿
生成可能提问
结构诊断
检索 PageMetadata
生成新 PPT
```

---



## 9.15 retrieval_service.py

负责：

```text
PageMetadata embedding
按主题检索相关页面
返回候选页面
```

---



# 十、关键处理流程



## 10.1 上传 PPT/PDF → 生成 LearningContent

```python
def parse_material(material_id):
    material = material_service.get(material_id)

    raw_pages = parser_service.extract_pages(material.storage_path)
    page_images = parser_service.render_pages(material.storage_path)

    metadata_list = []
    for page in raw_pages:
        metadata = metadata_service.generate_metadata(
            raw_text=page.raw_text,
            image_path=page_images[page.page_number]
        )
        metadata_list.append(metadata)

    content = learning_content_service.from_page_metadata(
        material_id=material_id,
        metadata_list=metadata_list
    )

    return content
```

---



## 10.2 输入知识点 → 生成文档 + PPT + LearningContent

```python
def create_topic_learning_package(topic, goal, duration, style):
    related_pages = retrieval_service.search_pages(topic)

    outline = topic_outline_agent.run(
        topic=topic,
        related_pages=related_pages,
        goal=goal,
        duration=duration,
        style=style
    )

    document = document_agent.run(topic, outline, style)
    ppt_json = ppt_agent.run(topic, outline, document, style)

    ppt_path = ppt_generate_service.create_pptx(ppt_json)

    content = learning_content_service.from_generated_ppt(
        topic=topic,
        ppt_json=ppt_json,
        ppt_path=ppt_path
    )

    return {
        "outline": outline,
        "document": document,
        "ppt_path": ppt_path,
        "content_id": content.content_id
    }
```

---



## 10.3 LearningContent → 视频

```python
def generate_video(content_id, config):
    content = learning_content_service.get(content_id)

    plan = classroom_plan_service.get_or_create_plan(content_id)

    scripts = video_script_agent.run(
        content=content,
        teaching_actions=plan.read_script_actions,
        config=config
    )

    images = render_service.get_or_render_images(content)

    audios = []
    for script in scripts:
        audio_path = tts_service.text_to_speech(
            script["text"],
            config["voice"]
        )
        audios.append(audio_path)

    subtitle_path = subtitle_service.create_srt(scripts, audios)

    video_path = video_service.compose(
        images=images,
        audios=audios,
        subtitles=subtitle_path
    )

    return video_path
```

---



## 10.4 LearningContent → ClassroomPlan

```python
def create_classroom_plan(content_id):
    content = learning_content_service.get(content_id)

    plan = classroom_plan_service.generate_plan(content)

    actions = teaching_action_service.generate_actions(
        plan=plan,
        content=content
    )

    classroom_plan_service.save_plan(plan, actions)

    return plan
```

---



## 10.5 ClassroomPlan → ClassroomSession

```python
def create_classroom_session(plan_id, mode):
    plan = classroom_plan_service.get(plan_id)

    first_scene = plan["scenes"][0]
    first_action_id = first_scene["action_ids"][0]

    session = classroom_runtime_service.create_session(
        plan_id=plan_id,
        content_id=plan["content_id"],
        mode=mode,
        initial_state={
            "mastery": {},
            "misconceptions": [],
            "covered_pages": [],
            "current_scene_id": first_scene["scene_id"],
            "current_action_id": first_action_id
        }
    )

    return session
```

---



## 10.6 AI 课堂单轮交互

```python
def classroom_turn(session_id, user_input):
    session = classroom_runtime_service.get_session(session_id)
    state = session.state
    current_action = teaching_action_service.get(session.current_action_id)

    decision = classroom_controller.decide(
        session=session,
        state=state,
        current_action=current_action,
        user_input=user_input
    )

    if decision.agent == "TeacherAgent":
        response = teacher_agent.run(decision, state, user_input)

    elif decision.agent == "ClassmateAgent":
        response = classmate_agent.run(decision, state)

    elif decision.agent == "TAAgent":
        response = ta_agent.run(decision, state)

    elif decision.agent == "EvaluatorAgent":
        response = evaluator_agent.run(decision, state, user_input)

    else:
        response = system_action_runner.run(decision)

    new_state = classroom_runtime_service.update_state(
        session=session,
        decision=decision,
        response=response,
        user_input=user_input
    )

    return {
        "decision": decision,
        "response": response,
        "state": new_state
    }
```

---



# 十一、Prompt 输出约束

所有 LLM 输出都必须走 JSON schema / Pydantic 校验，不合格就 retry。

---



## 11.1 PageMetadata Prompt 输出

```json
{
  "title": "",
  "summary": "",
  "knowledge_points": [],
  "prerequisites": [],
  "page_type": "",
  "page_role": "",
  "difficulty": "",
  "image_description": "",
  "teaching_focus": [],
  "possible_questions": [],
  "script_seed": ""
}
```

---



## 11.2 ClassroomPlan Prompt 输出

```json
{
  "title": "",
  "sections": [
    {
      "section_id": "",
      "title": "",
      "scene_ids": []
    }
  ],
  "scenes": [
    {
      "scene_id": "",
      "scene_type": "lecture",
      "title": "",
      "page_id": "",
      "learning_objective": "",
      "action_ids": []
    }
  ]
}
```

---



## 11.3 TeachingAction Prompt 输出

```json
{
  "actions": [
    {
      "action_id": "",
      "scene_id": "",
      "type": "ReadScript",
      "agent": "TeacherAgent",
      "value": {
        "script": ""
      },
      "order": 1
    },
    {
      "action_id": "",
      "scene_id": "",
      "type": "AskQuestion",
      "agent": "TeacherAgent",
      "value": {
        "question": "",
        "expected_answer": ""
      },
      "order": 2
    }
  ]
}
```

---



## 11.4 EvaluatorAgent 输出

```json
{
  "score": 0.0,
  "diagnosis": "",
  "misconceptions": [],
  "next_action": ""
}
```

---



## 11.5 ClassroomController 输出

```json
{
  "next_action_type": "AnswerUser",
  "agent": "TeacherAgent",
  "reason": "用户表示不理解，需要教师换一种方式解释",
  "stay_on_current_scene": true
}
```

---



# 十二、前端页面设计

```text
HomePage
- 选择 Learner Studio / Presenter Studio

LearnerHomePage
- 上传 PPT/PDF
- 输入知识点

MaterialParsePage
- 展示解析结果
- 每页摘要
- 主要知识点
- 按钮：讲解文档 / 视频 / 连续讲解课堂 / 互动课堂

TopicInputPage
- 输入学习主题
- 选择目标、风格、时长
- 按钮：生成文档 / PPT / 视频 / 课堂

VideoLecturePage
- 视频配置
- 生成进度
- 视频预览
- 下载视频
- 查看讲稿

ClassroomPage
- 左侧 Scene / PPT 目录
- 中间 AI 课堂舞台
- 右侧学习状态
- 底部用户输入框和课堂控制按钮

PresenterPage
- PPT 页面列表
- 当前页解析
- 逐页讲稿
- 可能提问
- 结构诊断
- 生成新 PPT
```

---



# 十三、MVP 版本



## 第一阶段：共享底座 + 视频

```text
1. 上传 PPT/PDF
2. 抽取每页文本
3. 渲染页面图片
4. 生成 PageMetadata
5. 生成 LearningContent
6. 生成 ClassroomPlan 中的 ReadScript 动作
7. 基于 ReadScript 生成简单讲解视频
8. 输入知识点生成讲解文档
9. 输入知识点生成简单 PPT
```

---



## 第二阶段：轻量 AI 互动课堂

```text
1. 基于 LearningContent 创建 ClassroomPlan
2. 生成 TeachingActions
3. 创建 ClassroomSession
4. TeacherAgent 执行 ReadScript / AnswerUser
5. ClassmateAgent 执行 ClassmateQuestion
6. TAAgent 执行 Summarize
7. EvaluatorAgent 执行 Quiz / Diagnose
8. ClassroomController 用规则控制下一步动作
9. 学习状态面板展示
```

---



## 第三阶段：教师 / 创作端

```text
1. 教师端逐页讲稿
2. 可能提问预测
3. 结构诊断
4. 检索 PageMetadata
5. 根据已有材料生成风格统一的新 PPT
```

---



## 暂时不做

```text
数字人
复杂 3D 小镇
真实白板绘图
复杂仿真
PBL 项目式学习
长期学习画像
高保真 PPT 编辑器
```

---



# 十四、实现优先级建议



## 后端优先级

```text
1. Material 上传与本地存储
2. PPT/PDF 文本解析
3. 页面图片渲染
4. PageMetadata 生成
5. LearningContent 生成
6. 视频生成
7. Topic 输入生成文档与 PPT
8. ClassroomPlan / TeachingAction
9. ClassroomSession / Controller
10. Presenter Studio
```

---



## 前端优先级

```text
1. HomePage
2. LearnerHomePage
3. MaterialParsePage
4. VideoLecturePage
5. TopicInputPage
6. ClassroomPage
7. PresenterPage
```

---



# 十五、最终项目亮点

```text
1. 双端结构：
   学习端解决“怎么学”，教师/创作端解决“怎么讲”。

2. 统一内容底座：
   上传 PPT/PDF、输入知识点、教师端重构 PPT 都统一转成 LearningContent。

3. 参考 OpenMAIC 的 Read-Plan-Run 结构：
   先解析材料，再生成课堂规划和教学动作，最后由 Controller 调度 Agent。

4. 可控 AI 互动课堂：
   不是多个 Agent 自由聊天，而是 ClassroomPlan + TeachingAction + ClassroomController。

5. PPT 视频讲解生成：
   将 PPT 页面、讲稿、TTS、字幕、视频合成整合为自动讲解视频。

6. 教师/创作端实用：
   支持讲稿生成、提问预测、结构诊断和基于已有材料重构新 PPT。
```
