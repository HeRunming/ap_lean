# DataFlow AgentTeams

面向 DataFlow Pipeline 编写与验收的多智能体系统，基于 AgentTeams 协同框架，使用 Claude API 驱动。

## 🎯 系统特点

- **6个专业Agent**：Orchestrator（主控）+ 5个职能Agent（调研、映射、开发、验证、审查）
- **AgentTeams协同**：状态机 + 消息总线 + 共享上下文
- **端到端闭环**：任务接收 → 拆解 → 执行 → 验证 → 审批 → 证据沉淀
- **Claude API驱动**：每个Agent一个独立Claude会话
- **可观测**：完整的trace、log、metrics和evidence链
- **审批机制**：低风险自动批准，高风险人工审批
- **可复用Skill**：核心能力抽象为标准Skill

## 🏗️ 系统架构

```
外部任务输入
    ↓
Orchestrator（主控编排）
    ↓
ResearchPlanner（调研规划）
    ↓
FieldMapper（字段映射）
    ↓
PipelineBuilder（代码生成）
    ↓
Validator（质量验证） ←→ PipelineBuilder（修复循环）
    ↓
Reviewer（安全审查）
    ↓
人工审批/自动部署
    ↓
证据沉淀 + 经验复用
```

## 📦 快速开始

### 1. 安装

```bash
# 配置环境变量
cp config/.env.example config/.env
# 编辑 config/.env，填入你的 ANTHROPIC_API_KEY

# 运行安装脚本
bash setup.sh
```

### 2. 运行示例

**Dry Run（不调用API）**：
```bash
python -m runtime.orchestrator \
  --task examples/math_clean_synthesis_task.json \
  --dry-run
```

**真实运行**：
```bash
source config/.env
python -m runtime.orchestrator \
  --task examples/math_clean_synthesis_task.json
```

### 3. 查看结果

```bash
# 查看执行轨迹
cat evidence/<run_id>.json

# 查看消息记录
cat evidence/<run_id>_messages.json
```

更多详情见 [QUICKSTART.md](QUICKSTART.md)

## 📚 文档

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - 系统架构设计
- **[AGENT_IDENTITY.md](AGENT_IDENTITY.md)** - Agent身份清单（比赛提交）
- **[QUICKSTART.md](QUICKSTART.md)** - 快速开始指南
- **[agents/contracts/](agents/contracts/)** - Agent契约（system prompt）
- **[skills/](skills/)** - 可复用Skill定义

## 🤖 Agent清单

| Agent | 角色 | 职责 |
|---|---|---|
| **Orchestrator** | 主控 | 任务拆解、编排、监控 |
| **ResearchPlanner** | 架构师 | 调研、算子拆解、计划 |
| **FieldMapper** | Schema专家 | 字段映射、冲突检测 |
| **PipelineBuilder** | 开发者 | 代码生成、算子实现 |
| **Validator** | 质量工程师 | 测试验证、质量评分 |
| **Reviewer** | 审查员 | 安全/合规/成本审查 |

## 🔄 协同机制

### 任务拆解
- Orchestrator接收任务 → 识别类型 → 生成执行DAG

### 上下文传递
- **共享状态（RunState）**：全局状态对象，记录task、outputs、events、evidence_ids
- **消息总线（MessageBus）**：Agent间异步消息，用于警告、阻塞、澄清
- **Evidence链**：每个Agent产生evidence_id，下游可追溯

### 协同执行
- 顺序执行：ResearchPlanner → FieldMapper → PipelineBuilder → Validator → Reviewer
- 修复循环：Validator失败 → repair_hints → PipelineBuilder修复 → 重新验证
- 阻塞机制：FieldMapper检测冲突 → 阻塞PipelineBuilder → 请求人工澄清

### 状态追踪
- 状态机：received → planned → mapped → drafted → validated → reviewed → completed/failed
- 事件流：每个状态转换记录timestamp、actor、payload
- Agent状态：每个Agent独立状态（running/completed/failed）

## 🛠️ Skill抽象

每个核心能力抽象为可复用Skill，包含：

- 名称、用途、输入/输出schema
- 调用条件和依赖工具
- 失败处理机制
- 安全边界和权限要求
- 复用价值说明

示例Skill：
- `research_plan` - 调研与算子拆解
- `field_alignment` - 字段映射与转换
- `pipeline_builder` - Pipeline代码生成
- `validator` - 质量验证与测试
- `reviewer` - 安全审查与审批

## 🔒 安全与审批

### 风险分级
- **low**: 自动批准
- **medium**: 质量分高且无critical风险自动批准
- **high/critical**: 必须人工审批

### 审批条件
**自动批准**：
- 风险low/medium + Validator通过 + 无critical风险

**人工审批**：
- 风险high/critical
- 涉及PII、生产删除、高成本
- 存在critical/high风险

**拒绝**：
- critical风险无缓解方案
- 严重违规
- Validator质量分<0.5

### 回滚机制
- 版本化回滚：保留previous_version，一键回滚
- 自动生成回滚预案
- 审计日志永久保存

## 📊 可观测性

### Trace
- 每个Agent调用生成trace_id
- 记录输入、输出、耗时、状态

### Log
- 结构化日志（JSON格式）
- 包含run_id、agent、action、timestamp

### Metrics
- Agent执行成功率
- 平均延迟和成本
- 质量分分布

### Evidence
- 完整的执行证据链
- 存储在evidence/目录
- 可追溯、可审计

## 🔧 配置

修改 `config/config.yaml` 自定义：

```yaml
claude:
  model: "claude-opus-5"
  max_tokens: 4096

execution:
  max_retries: 3
  timeout: 300

quality:
  min_validator_score: 0.5

cost:
  default_budget: 100.0
```

## 📁 目录结构

```
dataflow-agent-teams/
├── ARCHITECTURE.md          架构设计
├── AGENT_IDENTITY.md        Agent身份清单
├── README.md                本文件
├── QUICKSTART.md            快速开始
├── agents/
│   └── contracts/           Agent契约（system prompt）
├── runtime/
│   ├── orchestrator.py      主控编排器
│   ├── claude_client.py     Claude API客户端
│   ├── state.py            共享状态管理
│   ├── message_bus.py      消息总线
│   └── config.py           配置管理
├── skills/                  Skill定义
├── evidence/                执行证据
├── examples/                示例任务
└── config/                  配置文件
```

## 🎓 示例任务

### 数学问题清洗与合成

```json
{
  "kind": "数学问题数据集清洗与合成",
  "prompt": "搭建pipeline：validate → filter → synthesize(n=2) → generate_reasoning → ngram_dedup",
  "input_contract": {
    "question": "string",
    "answer": "string",
    "id": "string"
  },
  "operators": [
    "validate_math_question",
    "filter_invalid",
    "llm_synthesize_n=2",
    "llm_generate_reasoning_trace",
    "ngram_deduplicate"
  ],
  "risk": "medium"
}
```

### 订单宽表ETL

```json
{
  "kind": "工单",
  "title": "订单宽表延迟并新增customer_tier",
  "source": "kafka.orders.v2",
  "target": "dws_order_customer",
  "risk": "high",
  "constraints": {
    "sla_minutes": 15,
    "pii": true
  }
}
```

## 🏆 比赛要求对应

| 比赛要求 | 实现方式 |
|---|---|
| 至少3个不同职能Agent | ✅ 6个Agent，职责清晰 |
| AgentTeams协同设计基点 | ✅ 状态机 + 消息总线 + 共享上下文 |
| Agent Identity清单 | ✅ AGENT_IDENTITY.md |
| 角色编排 | ✅ Orchestrator主控DAG调度 |
| 任务拆解 | ✅ ResearchPlanner生成执行计划 |
| 上下文传递 | ✅ RunState + MessageBus + Evidence链 |
| 协同执行与状态追踪 | ✅ 状态机 + 事件流 + Agent状态 |
| Skill能力抽象 | ✅ 5个核心Skill，标准I/O |
| MCP工具集成 | ✅ 可选MCP（Schema Registry、DataFlow API、RAG） |
| 结果验证 | ✅ Validator专职质量门禁 |
| 执行证据沉淀 | ✅ traces/logs/metrics/reports |
| 审批与回滚 | ✅ 分级审批 + 版本化回滚 |
| 经验沉淀 | ✅ Runbook + Skill复用 + 知识库 |

## 🤝 贡献

欢迎提交issue和PR。

## 📄 License

MIT License

---

**项目版本**：v0.1.0  
**最后更新**：2026-09-02  
**适配比赛**：AgentTeams多智能体协同挑战赛
