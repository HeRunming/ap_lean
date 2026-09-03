# Core Skills

| Skill | 用途 | 依赖 | 失败处理 | 安全边界 |
|---|---|---|---|---|
| `research_plan` | 调研资料并拆解算子 DAG | RAG/MCP catalog | 证据不足→澄清 | 只读 |
| `field_alignment` | 字段语义对齐与转换 | schema registry、样例 | 冲突→阻塞 | 不写生产 |
| `pipeline_builder` | 生成隔离 Pipeline 草稿 | DataFlow MCP | 编译失败→修复循环 | 仅 sandbox |
| `validator` | 静态/回放/质量/成本验收 | runner、metrics | 失败→回传 Builder | 只读 |
| `reviewer` | 风险、权限、审批、回滚 | IAM、审计、版本 API | 高风险→人工审批 | deny-by-default |

每个子目录中的 `SKILL.md` 是可复用能力定义，统一输入输出为 JSON，并要求输出 evidence 引用。
