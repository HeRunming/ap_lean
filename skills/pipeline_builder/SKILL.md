# pipeline_builder
用途：按 plan+mapping 生成 DataFlow DSL 草稿和 diff。依赖 pipeline-editor MCP；失败可幂等重试并保留版本；安全边界：sandbox only，发布需 Reviewer approval。
