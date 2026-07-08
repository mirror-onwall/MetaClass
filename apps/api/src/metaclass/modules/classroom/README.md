# Classroom

`ClassroomPlan`、类型化 `TeachingAction`、`ClassroomSession`、规则控制器、课堂事件流与运行时角色。

第一版动作集合：`SHOW_PAGE`、`EXPLAIN`、`ASK_QUIZ`、`WAIT_STUDENT`、`GIVE_FEEDBACK`、`REMEDIATE`、`END`。

计划是不可变模板；用户提问等运行时动作追加为 session event，不回写原始计划。
