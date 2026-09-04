# Stage 3：只修复大纲证据引用

上一次大纲的 JSON 结构已经合法，但存在少量 claim、asset、source ref 或核心 claim 覆盖错误。读取 `validation_errors.json`、`paper_analysis.json`、`figures.json`、`paper_source.json`、现有大纲与证据 JSON。

只修复 Validator 指出的证据映射：删除不存在的引用，或从权威输入中选择真实存在且能支持该页论点的 claim、asset 和 source ref。不得创造新 ID，不得改变论文数字，不得扩大 claim，不得修改页面数量、顺序、section、主要论点或叙事结构，不修改 `outline.md`。

修复后覆盖当前 output 中的 `presentation_outline.json` 和 `slide_evidence.json`，保持逐页顺序完全一致，并重新读取确认两者符合对应 Schema。
