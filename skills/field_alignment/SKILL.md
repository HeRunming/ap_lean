# field_alignment
用途：建立 source→target 字段映射和转换表达式。输入 schema/样例/业务词典；输出 mapping、confidence、conflicts、evidence_ids。
调用条件：plan 确定输入输出后。依赖 schema-registry MCP、RAG glossary。失败：低置信度或类型不兼容返回阻塞；安全：不访问生产写接口。
