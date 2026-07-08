# MetaClass - PPT

---

## 第 1 页｜封面

### 页面标题

**MetaClass**  
（基于课程材料与知识点生成的双端智能教学平台？）

### 副标题

面向学习者与教师/汇报者的 AI 讲解视频生成与多智能体互动课堂系统

### 讲稿要点

各位老师好，我们的项目题目是 MetaClass。这个项目希望面向两个实际场景：一是学生拿到 PPT、PDF 或某个知识点后，如何更高效地学习；二是教师或汇报者在备课、答辩和项目展示前，如何更高效地准备讲稿、提问预测和汇报材料。我们希望最终实现一个双端智能教学平台，同时支持 AI 讲解视频生成和多智能体互动课堂。

---

## 第 2 页｜项目背景

### 页面标题

项目背景：静态材料难以支撑动态学习与高效汇报

### 页面内容

在高校课程学习、课程汇报、开题答辩和项目展示中，PPT/PDF 是最常见的知识载体，但目前仍存在以下问题：

1. **课程材料静态化，学习过程缺少互动**
  学生拿到 PPT/PDF 后，往往只能被动阅读，缺少讲解、提问、反馈和个性化诊断。
2. **汇报材料复用成本高**
  很多课程汇报、项目展示和答辩材料中存在大量可复用内容，例如研究背景、方法介绍、数据说明、实验结果、理论概念等。但这些内容通常分散在不同 PPT、PDF 或历史文档中，用户需要手动查找、复制、拼接和重新排版，过程耗时且容易风格不统一。
3. **教师/汇报者准备成本高**
  在备课或汇报前，用户需要理解每页材料的作用，撰写讲稿，设计页间过渡，预测老师或听众可能提出的问题，并检查整体逻辑结构，准备过程较为繁琐。
4. **现有 AI 工具多停留在文档问答**
  常见工具可以回答材料相关问题，但较少能够将课程材料进一步转化为讲解视频、互动课堂、结构化讲稿或可复用的新课件。



### 讲稿要点

我们观察到，PPT 和 PDF 在课程学习和汇报准备中非常常见，但它们本质上仍然是静态材料。学生看课件时往往缺少老师讲解、即时问答和学习反馈。另一方面，教师或学生做汇报时，也需要花大量时间写讲稿、设计衔接、预测问题。还有一个很实际的问题是，很多历史材料其实可以复用，比如背景介绍、方法流程、实验结果等，但这些内容分散在不同文件里，手动查找和拼接非常费时间。因此，我们希望把这些静态材料转化为结构化、可讲解、可互动、可复用的学习与汇报资源。

---

## 第 3 页｜参考系统：OpenMAIC

### 页面标题

参考系统：OpenMAIC 的 Read–Plan–Run 思路

### 页面内容

OpenMAIC 不是普通文档问答系统，而是从 topic/document 出发生成 AI 互动课堂。

其核心流程可以概括为：

1. **Read：课程理解**
  解析课程材料，抽取文本、图像内容、页面描述和知识结构。
2. **Plan：课堂规划**
  将讲解、展示、提问、小测等教学过程抽象为可执行的 TeachingAction。
3. **Run：课堂运行**
  由 Session Controller / Manager Agent 根据课堂状态调度 Teacher、Assistant、Classmate 等 Agent。

我们借鉴：

- Scene：课堂场景
- TeachingAction：教学动作
- ClassroomController：课堂控制器
- Multi-Agent：教师、同学、助教、评价 Agent

### 讲稿要点

我们主要参考了 OpenMAIC 的设计思想。它的关键不是简单做一个聊天机器人，而是先读懂课程材料，再生成课堂规划，最后让多个智能体在课堂里协同。我们把它概括成 Read、Plan、Run 三个阶段。我们的项目不会完全复刻 OpenMAIC 的复杂系统，而是做轻量化实现：保留课程理解、教学动作、课堂控制器和多 Agent 协同这些核心思想。

---

## 第 4 页｜项目目标

### 页面标题

项目目标：面向学习与汇报的双端智能教学平台

### 页面内容

本项目目标是构建一个双端智能教学平台：

### 1. 学习端 Learner Studio：解决“怎么学”

- 上传 PPT/PDF 后生成讲解文档、讲解视频和 AI 互动课堂
- 输入知识点后自动生成学习大纲、讲解文档、PPT、视频和课堂
- 记录学习过程，生成掌握度、误区和复习建议



### 2. 教师 / 创作端 Presenter Studio：解决“怎么讲”

- PPT/PDF 逐页解析
- 逐页讲稿生成
- 老师/听众可能提问预测
- 汇报结构诊断
- 基于历史材料生成风格统一的新 PPT



### 3. 共享底座 Shared Core

- 材料理解
- 内容生成
- 视频生成
- 课堂规划与运行



### 讲稿要点

我们的项目不是只服务学生，也不是只服务老师，而是分成学习端和教师/创作端。学习端主要解决学生怎么学，包括生成讲解视频、互动课堂和学习诊断。教师端主要解决怎么讲，包括讲稿、提问预测、结构诊断和材料复用。两端共享同一个底层能力，也就是对课程材料的理解、结构化和内容生成。

---



## 第 5 页｜系统总体结构与技术路线图



### 页面标题

系统总体结构：共享底座 + 双端应用 + 课堂运行层

### 页面内容

建议放系统架构图，并在图中突出四层：

1. **输入层**
  - PPT / PDF / 讲义 / 历史汇报
  - 学习主题 / 知识点 / 汇报需求
2. **共享底座 Shared Core**
  - Material Store
  - PageMetadata
  - LearningContent
  - ClassroomPlan
  - TeachingAction
3. **双端应用层**
  - Learner Studio
  - Presenter Studio
4. **Runtime Layer**
  - ClassroomController
  - TeacherAgent
  - ClassmateAgents
  - TAAgent
  - EvaluatorAgent
  - StudentState



### 图修改建议

当前系统架构图已经合理，但可以再补三点：

- 在 Shared Core 中把 **ClassroomPlan + TeachingAction** 单独突出，说明这是从内容到课堂的桥梁。
- 在 Learner Studio 中给 **视频讲解生成** 单独标一个小流程：`ReadScript → TTS → Subtitle → MP4`。
- 在 Runtime Layer 中标出 **Controller 不是普通 Agent，而是调度器**，负责根据用户输入和课堂状态选择下一步动作。



### 讲稿要点

这页是系统的整体结构。输入层既支持上传材料，也支持直接输入知识点或汇报需求。中间的共享底座负责把原始材料转化成结构化中间层，比如 PageMetadata 和 LearningContent。之后学习端和教师端都基于这个底座展开。对于 AI 互动课堂，我们进一步把 LearningContent 转成 ClassroomPlan 和 TeachingAction，最后由 Runtime Layer 中的 ClassroomController 调度不同 Agent 完成讲解、提问、小测和总结。

---



## 第 6 页｜共享底座设计



### 页面标题

共享底座：从静态材料到统一学习内容

### 页面内容



### 核心数据对象

1. **Material**
  - 原始 PPT/PDF 文件
  - 文件路径、页数、上传状态
2. **PageMetadata**
  - 每页标题
  - 页面摘要
  - 知识点
  - 图片说明
  - 页面类型
  - 讲解重点
  - 可能问题
3. **LearningContent**
  - 统一学习内容
  - 来源可以是上传材料，也可以是 AI 生成的知识点内容
  - 后续视频生成和课堂生成都依赖它
4. **ClassroomPlan**
  - 一节 AI 课堂的整体规划
  - 包括若干 Scene 和对应的教学目标
5. **TeachingAction**
  - 课堂中可执行的原子动作
  - 如展示页面、教师讲解、同学提问、随堂小测、助教总结



### 讲稿要点

共享底座的核心是统一中间层。用户上传的是 PPT 或 PDF，但后端不会直接把文件交给 Agent，而是先解析成 PageMetadata。多个 PageMetadata 会进一步组织成 LearningContent。这样不管内容来自上传材料还是用户输入知识点，后续都可以用同一套视频生成和课堂生成逻辑。为了支持 AI 课堂，我们还会从 LearningContent 生成 ClassroomPlan 和 TeachingAction。

---



## 第 7 页｜学习端：上传材料学习



### 页面标题

学习端路径 A：上传 PPT/PDF 后生成学习体验

### 页面内容

用户上传 PPT/PDF 后，系统执行：

1. 保存材料
2. 解析文本与页面图像
3. 生成 PageMetadata
4. 生成 LearningContent
5. 用户选择学习方式



### 可选择的学习方式

- **查看讲解文档**
- **生成讲解视频**
- **连续讲解课堂**
- **多智能体互动课堂**



### 页面按钮

```text
[查看讲解文档]
[生成讲解视频]
[连续讲解课堂]
[互动课堂]
```



### 讲稿要点

上传材料路径主要面向已有课程 PPT 和 PDF。系统会先解析每页内容，生成页面摘要、知识点和讲解重点。之后用户可以选择不同学习方式。如果只想快速复习，可以看讲解文档；如果想被动观看，可以生成视频；如果想边听边问，可以进入连续讲解课堂；如果想有同学提问、小测和助教总结，就进入互动课堂。

---



## 第 8 页｜学习端：输入知识点学习



### 页面标题

学习端路径 B：无需上传材料，直接输入知识点

### 页面内容

用户可以直接输入：

> 最大似然估计 / 逻辑回归 / Transformer / ANOVA

系统生成：

1. **学习大纲**
2. **讲解文档**
3. **简单 PPT**
4. **讲解视频**
5. **AI 互动课堂**



### 具体流程

```text
知识点输入
↓
TopicLearningPackage
↓
讲解文档 / PPT JSON
↓
LearningContent
↓
视频生成 / 课堂生成
```



### 实现方式

- `TopicOutlineAgent`：生成学习大纲
- `DocumentAgent`：生成讲解文档
- `PPTAgent`：生成 PPT JSON
- `python-pptx`：根据 PPT JSON 生成 PPTX
- 生成后的 PPT 再转为 LearningContent，复用视频和课堂能力



### 讲稿要点

第二条路径是不需要上传材料的。比如学生输入“最大似然估计”，系统会先生成一个学习大纲，再生成讲解文档和简单 PPT。这个 PPT 也会转成 LearningContent，因此它可以继续生成讲解视频或 AI 互动课堂。这样系统既支持材料驱动学习，也支持主题驱动学习。

---



## 第 9 页｜AI 互动课堂总体设计



### 页面标题

AI 互动课堂：不是自由聊天，而是可控课堂过程

### 页面内容

AI 互动课堂的核心流程：

```text
LearningContent
↓
ClassroomPlan
↓
TeachingAction
↓
ClassroomController
↓
TeacherAgent / ClassmateAgents / TAAgent / EvaluatorAgent
↓
StudentState 更新
```



### 课堂角色

- **TeacherAgent**
  - 讲解当前页面或知识点
  - 回答用户问题
  - 根据理解程度换一种讲法
- **ClassmateAgents**
  - BasicStudent：问基础概念
  - DeepThinker：提出深入问题
  - NoteTaker：整理重点
  - ExamStudent：关注考点和题型
- **TAAgent**
  - 总结本轮内容
  - 提炼易错点
  - 给出复习建议
- **EvaluatorAgent**
  - 批改回答
  - 识别误区
  - 更新掌握度
- **ClassroomController**
  - 决定下一步谁说话
  - 决定执行哪个 TeachingAction
  - 控制课堂不跑偏



### 讲稿要点

AI 互动课堂是我们项目的重点。我们不是让多个 Agent 自由聊天，而是把课堂过程拆成计划和动作。ClassroomPlan 决定这节课讲哪些内容，TeachingAction 决定每一步具体做什么，比如展示页面、教师讲解、同学提问或开始小测。ClassroomController 会根据当前场景、用户输入和学习状态决定下一步动作。这样课堂既有互动性，又不会失控。

---



## 第 10 页｜TeachingAction 设计



### 页面标题

TeachingAction：把课堂行为拆成可执行动作

### 页面内容



### 为什么需要 TeachingAction？

如果直接让 Agent 聊天，容易出现：

- 内容跑偏
- 多个 Agent 抢话
- 教学节奏不可控
- 前端难以渲染

因此我们将课堂过程拆成可执行动作：

```text
TeachingAction = {
  type: 动作类型,
  agent: 执行者,
  value: 动作内容,
  target: 作用对象,
  order: 执行顺序
}
```



### 第一版支持的动作


| 动作                | 执行者              | 作用          |
| ----------------- | ---------------- | ----------- |
| ShowSlide         | System           | 展示当前 PPT 页面 |
| ReadScript        | TeacherAgent     | 教师讲解        |
| AskQuestion       | TeacherAgent     | 教师向学生提问     |
| ClassmateQuestion | ClassmateAgent   | AI 同学提问     |
| StartQuiz         | System/Evaluator | 启动随堂小测      |
| AnswerUser        | TeacherAgent     | 回答用户问题      |
| Summarize         | TAAgent          | 总结本页重点      |
| MoveNext          | Controller       | 进入下一页       |
| ReviewPrevious    | Controller       | 回顾前置内容      |




### 示例

```json
{
  "action_id": "action_003",
  "scene_id": "scene_002",
  "type": "AskQuestion",
  "agent": "TeacherAgent",
  "value": {
    "question": "为什么线性回归输出不能直接作为概率？",
    "expected_answer": "因为线性回归输出可能小于0或大于1，而概率必须在0到1之间。"
  },
  "order": 3
}
```



### 讲稿要点

这页解释 TeachingAction。它是我们借鉴 OpenMAIC 后做出的核心抽象。TeachingAction 的好处是把复杂课堂拆成小动作，每个动作都有类型、执行者和内容。比如展示 PPT 是一个动作，教师讲解是一个动作，同学提问也是一个动作。这样前端可以根据 action type 渲染不同组件，后端也可以根据 action type 决定调用哪个 Agent。

---



## 第 11 页｜ClassroomController 运行机制



### 页面标题

ClassroomController：负责课堂节奏与 Agent 调度

### 页面内容



### 输入

ClassroomController 每轮读取：

- 当前 Scene
- 当前 TeachingAction
- 用户输入
- 历史对话
- StudentState
- 当前课堂模式



### 输出

决定下一步：

- 谁说话？
- 执行什么动作？
- 是否停留在当前页？
- 是否进入小测？
- 是否总结或进入下一页？



### 规则示例

```text
用户说“不懂”
→ TeacherAgent 执行 AnswerUser，并换一种讲法

用户说“举个例子”
→ TeacherAgent 执行 ExplainWithExample

用户完成小测
→ EvaluatorAgent 批改并更新 StudentState

一页讲解完成
→ TAAgent Summarize
→ Controller 判断是否 MoveNext
```



### 两种课堂模式

1. **Continuous Mode**
  - AI 教师连续讲解
  - 用户可以随时打断提问
  - AI 同学较少插话
2. **Interactive Mode**
  - 穿插同学提问、小测和助教总结
  - 更适合深入学习和复习



### 讲稿要点

ClassroomController 是为了保证课堂节奏。它不是简单的聊天 Agent，而更像一个调度器。每一轮它都会看当前讲到哪里、用户说了什么、学生掌握情况如何，然后决定下一步动作。比如用户说“我不懂”，它就不会直接进入下一页，而是让 TeacherAgent 换一种方式解释。如果用户完成小测，就调用 EvaluatorAgent 批改并更新 StudentState。

---



## 第 12 页｜视频讲解生成设计



### 页面标题

视频讲解生成：从 LearningContent 到 MP4

### 页面内容



### 生成链路

```text
LearningContent
↓
生成 ReadScript / VideoScript
↓
PPT/PDF 页面渲染成图片
↓
TTS 生成逐页音频
↓
字幕切分生成 SRT
↓
ffmpeg/moviepy 合成 MP4
```



### 模块设计

- **VideoScriptAgent**
  - 根据每页 PageMetadata 生成适合口播的讲稿
  - 可复用 TeachingAction 中的 ReadScript
- **RenderService**
  - PPT/PDF 页面转图片
  - 输出 `page_001.png`, `page_002.png`
- **TTSService**
  - 将逐页讲稿转为语音
  - 输出 `page_001.mp3`
- **SubtitleService**
  - 根据讲稿切句生成字幕
  - 输出 `subtitles.srt`
- **VideoService**
  - 将图片、音频、字幕合成为 MP4
  - 使用 `ffmpeg` 或 `moviepy`



### 第一版视频效果

- 静态 PPT 页面
- AI 配音
- 底部字幕
- 简单淡入淡出
- 不做数字人、口型同步和复杂动画



### 讲稿要点

视频生成是另一个重要功能。我们的思路不是调用视频生成大模型，而是用更可控、更轻量的方式实现。系统先把 PPT 或 PDF 页面渲染成图片，再基于 LearningContent 生成逐页口播讲稿，然后用 TTS 转语音，用字幕模块生成 SRT，最后用 ffmpeg 或 moviepy 合成 MP4。这样只需要一个 LLM API 和一个 TTS 能力，不需要注册很多多模态 API。

---



## 第 13 页｜视频任务 VideoJob 与缓存设计



### 页面标题

具体实现：VideoJob 管理长任务与中间文件

### 页面内容

生成视频耗时较长，因此采用 VideoJob 记录任务状态。

### VideoJob 示例

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



### 前端交互

```text
用户点击生成视频
↓
后端创建 VideoJob
↓
前端轮询 /api/videos/{video_id}/status
↓
显示进度条
↓
生成完成后返回 video_url
```



### 缓存策略

- 已生成 PageMetadata：不重复解析
- 已生成讲稿：不重复调用 LLM
- 已生成音频：不重复 TTS
- 已生成 MP4：直接返回下载链接



### 讲稿要点

因为视频生成可能需要几十秒甚至几分钟，所以不能让前端一直卡住。我们会创建 VideoJob 来记录当前任务状态，例如正在生成讲稿、正在生成音频、正在合成视频。前端只需要轮询状态接口并展示进度条。同时，为了节约 API 调用和生成时间，我们会缓存中间结果，避免每次点击都重新生成。

---



## 第 14 页｜教师 / 创作端设计



### 页面标题

教师 / 创作端：辅助备课、汇报与材料复用

### 页面内容

面向备课、课程汇报、开题答辩和结题展示。

### 核心功能

1. **PPT/PDF 逐页解析**
  - 每页摘要
  - 核心知识点
  - 页面作用
2. **逐页讲稿生成**
  - 当前页讲解词
  - 页间过渡语
  - 汇报时长估计
3. **可能提问预测**
  - 老师可能问什么
  - 听众可能听不懂什么
  - 建议回答
4. **汇报结构诊断**
  - 是否缺少背景
  - 是否缺少总结
  - 是否前后衔接不自然
  - 哪些页面重复或过难
5. **材料复用与新 PPT 生成**
  - 检索历史 PageMetadata
  - 用户选择相关内容
  - 重新生成风格统一的新 PPT



### 讲稿要点

教师端重点不是学习，而是帮助用户讲清楚。比如上传一份答辩 PPT 后，系统可以逐页生成讲稿和过渡语，也可以预测老师可能追问的问题。对于历史材料复用，我们不是简单拼接旧 PPT 页面，而是先从旧材料中抽取 PageMetadata，再根据新主题重新组织内容，最后生成风格统一的新 PPT。

---



## 第 15 页｜具体实现设计与技术栈



### 页面标题

具体实现：轻量化 Web 原型

### 页面内容



### 前端

- React + Vite
- 页面：
  - HomePage
  - LearnerPage
  - MaterialParsePage
  - VideoPage
  - ClassroomPage
  - PresenterPage



### 后端

- FastAPI
- 核心接口：
  - `/api/materials/upload`
  - `/api/materials/{id}/parse`
  - `/api/learner/topics`
  - `/api/videos/{id}/status`
  - `/api/classrooms/{id}/turn`
  - `/api/presenter/projects/{id}/questions`



### AI 与多媒体

- LLMProvider：统一封装大模型调用
- TTSProvider：统一封装语音合成
- python-pptx：PPT 解析与生成
- PyMuPDF / pdfplumber：PDF 解析
- ffmpeg / moviepy：视频合成



### 数据存储

- SQLite：记录材料、任务、课堂 session
- JSON 文件：存储 PageMetadata、LearningContent、ClassroomPlan、TeachingAction
- 本地 data 目录：存储 PPT、图片、音频、字幕和视频



### 讲稿要点

技术实现上，我们采用比较轻量的前后端分离方案。前端用 React + Vite，后端用 FastAPI。AI 能力通过统一的 LLMProvider 调用，避免把代码绑定到某一个模型平台。TTS 也单独封装。数据层不做复杂数据库，MVP 阶段主要用 JSON 文件存中间结果，SQLite 只记录任务状态和索引。这样既能保证结构清楚，也能控制开发量。

---



## 第 16 页｜关键接口与数据流



### 页面标题

关键数据流：材料解析、视频生成、课堂交互

### 页面内容



### 1. 上传材料数据流

```text
POST /api/materials/upload
↓
保存文件
↓
POST /api/materials/{id}/parse
↓
生成 PageMetadata
↓
生成 LearningContent
```



### 2. 视频生成数据流

```text
POST /api/learner/materials/{id}/video
↓
创建 VideoJob
↓
生成讲稿 / 音频 / 字幕 / MP4
↓
GET /api/videos/{video_id}/status
```



### 3. AI 课堂数据流

```text
POST /api/learner/materials/{id}/classroom
↓
生成 ClassroomPlan + TeachingAction
↓
创建 ClassroomSession
↓
POST /api/classrooms/{session_id}/turn
↓
Controller 调度 Agent
↓
更新 StudentState
```



### 讲稿要点

这页主要说明系统怎么跑。上传材料后，系统先生成 PageMetadata 和 LearningContent。生成视频时，后端创建 VideoJob，前端通过 status 接口拿进度。进入课堂时，后端先生成 ClassroomPlan 和 TeachingAction，再创建 ClassroomSession。用户每次提问都会调用 turn 接口，由 Controller 决定下一步调用哪个 Agent，并更新学习状态。

---



## 第 17 页｜可行性、难点与解决方案



### 页面标题

可行性与难点

### 页面内容



### 可行性

- 多数多媒体处理可以本地实现，不依赖大量外部 API
- 只需要 1 个 LLM API，最多加 1 个 TTS API
- 采用 JSON 驱动中间层，降低数据库复杂度
- 三人可按前端、后端、AI Agent 分工并行开发



### 主要难点


| 难点              | 解决方案                                         |
| --------------- | -------------------------------------------- |
| PPT/PDF 解析不稳定   | 限定输入格式，优先支持标准 PPT/PDF                        |
| LLM 输出 JSON 不规范 | 使用 JSON Schema / Pydantic 校验和重试              |
| 视频生成链路长         | 使用 VideoJob、缓存和分步生成                          |
| AI 课堂容易发散       | 使用 TeachingAction 和 ClassroomController 控制流程 |
| 学习状态评估不精确       | MVP 只做轻量掌握度和误区诊断                             |




### 讲稿要点

这个项目的难点主要在三个方面：材料解析、视频生成和 AI 课堂控制。我们的解决思路是轻量化和可控化。材料解析先支持标准格式；LLM 输出用 JSON Schema 校验；视频用任务队列和缓存；AI 课堂不让 Agent 自由发挥，而是通过 TeachingAction 和 Controller 控制。

---



## 第 18 页｜进度安排与预期成果



### 页面标题

项目进度与预期成果

### 页面内容



### 时间安排：7.8—7.22

- **7.8**：开题与项目初始化
- **7.9—7.10**：材料上传与 PageMetadata
- **7.11—7.12**：知识点生成文档和 PPT
- **7.13—7.14**：视频生成
- **7.15—7.17**：AI 互动课堂
- **7.18—7.19**：教师 / 创作端
- **7.20—7.21**：系统联调与 Demo 准备
- **7.22**：最终提交与展示



### 预期成果

- 上传 PPT/PDF 后生成页面解析
- 生成讲解视频
- 生成 AI 互动课堂
- 输入知识点生成讲解文档和 PPT
- 教师端生成讲稿、提问预测和结构诊断
- 完成一个可运行 Web Demo



### 结尾总结

> MetaClass 不是普通文档问答系统，而是参考 OpenMAIC 的 Read–Plan–Run 思路，将课程材料或知识点转化为结构化学习内容，再生成视频讲解和可控的多智能体互动课堂，同时服务学生学习与教师/汇报者表达准备。



### 讲稿要点

最后是我们的进度安排。从 7 月 8 日到 7 月 22 日，我们会先完成材料上传和 PageMetadata，然后做知识点生成文档和 PPT，再做视频生成，之后重点实现 AI 互动课堂，最后完成教师端和系统联调。最终希望展示一个完整 Demo：上传 PPT 后可以看到解析结果、生成讲解视频、进入 AI 课堂；输入知识点后可以生成讲解文档和 PPT；教师端可以生成讲稿、提问预测和结构诊断。
