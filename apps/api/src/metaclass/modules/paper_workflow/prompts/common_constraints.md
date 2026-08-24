你正在执行 MetaClass 论文工作流的一个受控阶段。

必须完整遵循本阶段指定 Skill 的 SKILL.md。MetaClass 约束只规定本次任务的输入、输出、证据边界和保存位置，不替代 Skill 的专业分析流程。

通用约束：
1. 只分析当前论文，不联网补充其他论文或事实。
2. 不修改 source/、resolved_request.json、Prompt 或 Skill 目录中的任何文件。
3. 不读取或写入 Obsidian、HOME 下的笔记目录、全局知识库或其他 Job。
4. 不执行 Skill 中的下载、知识图谱更新、Obsidian 保存和全局配置读取步骤。
5. 不安装依赖，不执行 pip、npm、brew 或系统包安装。
6. 论文事实、数字、局限和图表解释必须关联 paper_source.json 中的 source_ref。
7. 输出只能写入本阶段明确指定的 output/ 目录。
8. 不把本机绝对路径写入 Markdown 或 JSON。
9. 完成前检查必需文件存在、JSON 可解析、引用可以反查。

