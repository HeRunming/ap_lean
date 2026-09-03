# Agent Contracts

每个文件都是独立 Codex Agent 的最小身份契约。运行器将任务上下文、历史事件和前序输出注入 `## Context`，要求 Agent 只返回 JSON，不得越权调用未声明工具。
