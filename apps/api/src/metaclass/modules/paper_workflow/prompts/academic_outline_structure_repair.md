# Stage 3：只修复 JSON 结构

上一次 `academic-pptx` 已经完成内容规划，但 `presentation_outline.json` 或 `slide_evidence.json` 不符合 MetaClass JSON 契约。读取 `validation_errors.json`、两份现有 JSON 和两份 `*_schema.json`。

只允许修复 JSON 语法、字段名、必填字段、枚举、数据类型、嵌套形状、slide 顺序表达和两个文件之间的结构对应关系。保留原有标题、论点、section 语义、claim、source ref、asset、定量值和 speaker note，不重新分析论文，不重写叙事，不修改 `outline.md`。

禁止通过虚构 claim、asset、source ref、数字或论文事实填补字段。修复后覆盖当前 output 中的 `presentation_outline.json` 和 `slide_evidence.json`，并重新读取确认两者分别符合对应 Schema。
