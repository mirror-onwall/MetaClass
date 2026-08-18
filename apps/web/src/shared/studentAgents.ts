import atmosphereAvatar from "../assets/agents/atmosphere-regulator.png";
import conceptConfusedAvatar from "../assets/agents/concept-confused.png";
import deepThinkerAvatar from "../assets/agents/deep-thinker.png";
import foundationWeakAvatar from "../assets/agents/foundation-weak.png";
import noteTakerAvatar from "../assets/agents/note-taker.png";
import practicalApplierAvatar from "../assets/agents/practical-applier.png";
import researcherAvatar from "../assets/agents/researcher.png";
import silentObserverAvatar from "../assets/agents/silent-observer.png";
import type { StudentAgentType } from "./types";

export const studentAgentChoices: Array<{
  type: StudentAgentType;
  name: string;
  description: string;
  studentName: string;
  gender: string;
  profile: string;
  avatar: string;
}> = [
  { type: "classroom_atmosphere_regulator", name: "课堂气氛调节者", description: "活跃氛围，用类比打开话题", studentName: "凡凡", gender: "男", profile: "善于把抽象概念换成生活里的小例子，也会鼓励不敢发言的同学加入讨论。", avatar: atmosphereAvatar },
  { type: "deep_thinker", name: "深度思考者", description: "追问原因、边界与反例", studentName: "浩浩", gender: "男", profile: "习惯从前提、条件和反例出发追问，喜欢把一个结论推到更深的边界处。", avatar: deepThinkerAvatar },
  { type: "note_taker", name: "课堂笔记员", description: "提炼重点，整理可复习笔记", studentName: "婧婧", gender: "女", profile: "会把讲解整理为定义、例子和易错点三类笔记，擅长在阶段结束时复述重点。", avatar: noteTakerAvatar },
  { type: "researcher", name: "研究型同学", description: "连接应用场景与研究方法", studentName: "涵涵", gender: "女", profile: "对研究问题和方法格外敏感，常会把当前知识点连接到实验设计和真实研究场景。", avatar: researcherAvatar },
  { type: "foundation_weak", name: "基础薄弱型同学", description: "提出基础问题，帮助发现学习门槛", studentName: "琪琪", gender: "男", profile: "愿意直接说出没听懂的地方，容易卡在前置概念，需要清晰的分步解释和小例子。", avatar: foundationWeakAvatar },
  { type: "silent_observer", name: "沉默观察型同学", description: "低频发言，在关键处表达困惑", studentName: "跳跳", gender: "女", profile: "平时安静地观察课堂节奏，通常在被邀请或出现关键困惑时，给出简短但真实的反馈。", avatar: silentObserverAvatar },
  { type: "concept_confused", name: "概念混淆型同学", description: "暴露典型误解，触发辨析讲解", studentName: "昊昊", gender: "男", profile: "容易把相近概念放在一起理解，但正好能暴露典型误解，推动老师进行对比辨析。", avatar: conceptConfusedAvatar },
  { type: "practical_applier", name: "实践应用型同学", description: "关注怎么用、在哪里用", studentName: "包包", gender: "女", profile: "最关心知识怎样落到真实任务里，会追问具体做法、使用条件和可操作的步骤。", avatar: practicalApplierAvatar },
];

export const defaultStudentAgentTypes: StudentAgentType[] = studentAgentChoices.map(
  (student) => student.type,
);
