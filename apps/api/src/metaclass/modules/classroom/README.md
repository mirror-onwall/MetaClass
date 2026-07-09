# Classroom

`ClassroomPlan`、类型化 `TeachingAction`、`ClassroomSession`、规则控制器、课堂事件流与运行时角色。

第一版动作集合：`SHOW_PAGE`、`EXPLAIN`、`ASK_QUIZ`、`PROBE`、`WAIT_STUDENT`、`GIVE_FEEDBACK`、`REMEDIATE`、`SUMMARIZE`、`REVIEW`、`END`。

`agents/` 里放课堂角色的最小实现：`TeacherAgent` 负责编排讲解与回答问题，`EvaluatorAgent` 负责把小测结果转成证据，`StudentRosterAgent` 负责创建默认学生智能体状态。

`controller.py` 是本项目对应 OpenMAIC director 的位置：它根据 `ClassroomState` 调用 LLM provider，决定下一轮由 teacher、student、evaluator 还是 end 接管。默认 fake LLM 也返回同样的 `ControllerDecision` 结构，便于本地测试；接真实模型后无需改业务接口。

计划是不可变模板；用户提问等运行时动作追加为 session event，不回写原始计划。
