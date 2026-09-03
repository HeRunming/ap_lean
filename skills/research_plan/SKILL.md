# research_plan
用途：从任务、历史 Runbook 和算子目录生成可执行 DAG。
输入：`{task, schemas?, history?}`；输出：`{plan, assumptions, evidence_ids, risks}`。
调用条件：收到新工单/告警且缺少可信执行计划时。依赖：RAG 检索、DataFlow operator-catalog MCP。
失败：无证据或工具超时则 `needs_clarification`，指数退避最多 3 次。安全：只读、禁止写数据。
复用：任何批流一体任务均可复用；结果写入共享上下文供 FieldMapper/Builder 使用。
