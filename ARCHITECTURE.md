# DataFlow AgentTeams - 架构设计

面向DataFlow Pipeline编写的多智能体系统，以AgentTeams协同框架为设计基点。

## 分层架构

```
任务输入（告警/工单/日志/账单/安全事件）
        ↓
① Orchestrator（主控Agent）         — 任务拆解、编排、状态追踪
        ↓ 
② ResearchPlanner（调研Agent）      — 调研、算子拆解、生成计划
        ↓
③ FieldMapper（字段对齐Agent）      — Schema映射、类型转换
        ↓
④ PipelineBuilder（Pipeline开发Agent） — 生成DataFlow代码
        ↓
⑤ Validator（验证Agent）            — 质量检查、测试
        ↓
⑥ Reviewer（审查Agent）             — 安全审查、审批
        ↓
执行证据沉淀 → 经验沉淀（可复用Skill）
```

## AgentTeams协同机制

### 角色编排
- **Orchestrator** 作为主控，负责任务分配和状态协调
- 每个职能Agent独立运行，通过共享状态和消息传递协同
- 支持并行执行（多个PipelineBuilder可并行处理不同算子）

### 任务拆解
- Orchestrator接收任务后，识别任务类型（pipeline构建、修复、优化等）
- 调用ResearchPlanner生成执行计划（算子DAG、依赖关系）
- 将plan拆分为可并行的子任务

### 上下文传递
- **共享状态**：`runtime/state.py` 维护全局RunState
- **消息队列**：Agent间通过结构化消息传递中间结果
- **Evidence链**：每个操作产生evidence_id，下游Agent可追溯

### 协同执行
- 每个Agent通过Claude API独立执行
- Orchestrator监控各Agent状态，处理依赖关系
- 支持失败重试、降级、回滚

### 状态追踪
- 状态机：received → planned → mapped → drafted → validated → reviewed → completed
- 事件流：每个状态转换记录时间戳、actor、payload
- 可观测性：实时dashboard、结构化日志、metrics

## Agent Identity清单

| Agent | 身份定义 | 能力边界 | 输入 | 输出 | 协同关系 |
|---|---|---|---|---|---|
| **Orchestrator** | 项目经理/任务编排者 | 不直接写代码，只编排和监控 | 企业任务、告警、工单 | DAG、状态、审批请求 | 调度全部Agent，处理Agent间依赖 |
| **ResearchPlanner** | 数据工程架构师 | 只读调研，不写生产代码 | 任务描述、历史Runbook、schema | plan.json、算子候选、风险评估 | 为FieldMapper/PipelineBuilder提供蓝图 |
| **FieldMapper** | Schema合约专家 | 字段映射，不写pipeline逻辑 | 源/目标schema、样例数据 | mapping.json、转换规则、冲突列表 | 阻塞PipelineBuilder直到映射确认 |
| **PipelineBuilder** | DataFlow开发者 | 写代码，只在隔离环境 | plan、mapping、算子规格 | pipeline代码、diff、依赖声明 | 接收plan和mapping，交付给Validator |
| **Validator** | 质量工程师 | 测试验证，只读执行 | pipeline draft、测试用例 | 测试报告、质量指标、修复建议 | 失败则反馈PipelineBuilder重做 |
| **Reviewer** | 安全/合规审查员 | 审查批准，可触发人工审批 | 验证报告、diff、风险等级 | 审批决策、回滚预案、审计记录 | 高风险必须人工确认，低风险自动批准 |

## 技术实现

### Claude API集成
- 使用用户提供的API中转：`https://01tree.ai/claudecode`
- 每个Agent一个独立的Claude对话会话
- 支持streaming和非streaming模式
- 错误重试和降级策略

### Skill抽象层
每个Skill包含：
- 名称、用途、输入/输出schema
- 调用条件和依赖工具
- 失败处理机制
- 安全边界和权限要求
- 复用价值说明

### MCP工具集成（可选）
- Schema Registry：读取数据源元数据
- DataFlow API：pipeline CRUD操作
- RAG知识库：历史案例检索
- 监控API：获取质量指标

### 可观测性
- **Trace**：每个Agent调用生成trace_id
- **Log**：结构化日志（JSON格式）
- **Metrics**：延迟、成功率、成本
- **Evidence**：每次执行的完整证据链

### 审批与回滚
- 高风险操作标记`approval_required`
- 支持人工审批workflow
- 自动回滚机制（基于版本快照）
- 审计日志永久保存

### 经验沉淀
- 成功案例保存为Runbook
- 常见模式抽取为可复用Skill
- 失败案例记录修复策略
- 定期复盘和知识库更新

## 目录结构

```
dataflow-agent-teams/
├── ARCHITECTURE.md          本文件
├── README.md                项目说明
├── AGENT_IDENTITY.md        Agent身份清单（比赛提交）
├── agents/
│   ├── contracts/           Agent契约（system prompt）
│   │   ├── orchestrator.md
│   │   ├── research_planner.md
│   │   ├── field_mapper.md
│   │   ├── pipeline_builder.md
│   │   ├── validator.md
│   │   └── reviewer.md
│   └── claude_adapter.py    Claude API适配器
├── skills/                  可复用Skill定义
│   ├── research_plan/
│   ├── field_alignment/
│   ├── pipeline_builder/
│   ├── validator/
│   └── reviewer/
├── runtime/
│   ├── orchestrator.py      主控编排器
│   ├── state.py            共享状态管理
│   ├── message_bus.py      Agent间消息传递
│   ├── claude_client.py    Claude API客户端
│   └── config.py           配置管理
├── mcp/                     MCP工具实现（可选）
│   ├── schema_registry.py
│   ├── dataflow_api.py
│   └── rag_knowledge.py
├── evidence/                执行证据
│   ├── traces/
│   ├── logs/
│   └── reports/
├── examples/                示例任务
│   ├── math_clean_synthesis_task.json
│   └── order_etl_task.json
├── config/
│   ├── config.yaml          系统配置
│   └── .env.example         环境变量模板
└── docs/                    文档
    ├── agent_identity.md    Agent身份详细说明
    ├── skill_catalog.md     Skill目录
    └── deployment.md        部署指南
```

## 运行示例

```bash
# 配置环境变量
export ANTHROPIC_BASE_URL=https://01tree.ai/claudecode
export ANTHROPIC_API_KEY=sk-ant-oat01-...

# 运行示例任务
python -m runtime.orchestrator --task examples/math_clean_synthesis_task.json

# 查看状态
python -m runtime.orchestrator --status <run_id>

# 查看证据
cat evidence/traces/<run_id>.json
```

## 与比赛要求的对应关系

| 比赛要求 | 实现方式 |
|---|---|
| 至少3个不同职能Agent | 6个Agent，职责清晰 |
| AgentTeams协同设计基点 | 状态机、消息总线、共享上下文 |
| 角色编排 | Orchestrator主控，DAG调度 |
| 任务拆解 | ResearchPlanner生成执行计划 |
| 上下文传递 | RunState + 消息队列 + evidence链 |
| 协同执行与状态追踪 | 状态机 + 事件流 + 可观测性 |
| Skill能力抽象 | 5个核心Skill，标准I/O |
| MCP工具集成 | Schema Registry、DataFlow API、RAG |
| 结果验证 | Validator专职质量门禁 |
| 执行证据沉淀 | traces/logs/metrics/reports |
| 审批与回滚 | Reviewer + 人工审批 + 版本化回滚 |
| 经验沉淀 | Runbook + Skill复用 + 知识库 |
