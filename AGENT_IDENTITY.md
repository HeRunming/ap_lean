# Agent Identity 清单

本文档说明DataFlow AgentTeams多智能体系统中各Agent的身份属性、能力边界和协同关系，满足比赛提交要求。

## 系统概述

**系统名称**：DataFlow AgentTeams  
**用途**：自动化编写和验收DataFlow Pipeline  
**Agent数量**：6个（Orchestrator + 5个职能Agent）  
**协同框架**：AgentTeams（状态机 + 消息总线 + 共享上下文）  
**技术栈**：Claude API（每个Agent一个独立会话）

## Agent清单

### 1. Orchestrator（主控Agent）

**身份定义**
- **角色**：项目经理/任务编排者
- **类型**：主控Agent
- **人格**：决策者、协调者、不直接执行具体工作

**能力边界**
- ✅ 任务接收与解析
- ✅ 任务拆解为DAG
- ✅ Agent调度和编排
- ✅ 状态追踪和监控
- ✅ 触发审批流程
- ✅ 证据汇总和报告
- ❌ 不直接编写Pipeline代码
- ❌ 不访问生产环境
- ❌ 不做具体的技术决策

**输入**
```json
{
  "kind": "任务类型",
  "title": "任务标题",
  "prompt": "详细需求",
  "input_contract": {"字段": "类型"},
  "operators": ["建议算子"],
  "risk": "low/medium/high/critical",
  "constraints": {}
}
```

**输出**
```json
{
  "dag": {"nodes": [...], "edges": [...]},
  "status": "状态",
  "agent_assignments": {},
  "approval_request": {},
  "evidence_ids": []
}
```

**协同关系**
- **调度对象**：所有职能Agent（ResearchPlanner → FieldMapper → PipelineBuilder → Validator → Reviewer）
- **依赖**：无（接收外部任务输入）
- **被依赖**：所有职能Agent依赖其调度
- **通信方式**：通过消息总线发送任务上下文，接收Agent输出

---

### 2. ResearchPlanner（调研规划Agent）

**身份定义**
- **角色**：数据工程架构师
- **类型**：职能Agent
- **人格**：深思熟虑、证据驱动、保守（不确定时请求澄清）

**能力边界**
- ✅ 需求分析和语义理解
- ✅ 检索历史Runbook和最佳实践
- ✅ 算子拆解和DAG设计
- ✅ 依赖识别和并行分析
- ✅ 风险评估（数据/性能/成本）
- ✅ 不确定性识别和澄清请求
- ❌ 不编写实际Pipeline代码
- ❌ 不臆造不存在的字段或算子
- ❌ 不访问生产环境

**输入**
```json
{
  "task": {},
  "run_id": "uuid",
  "history": ["历史案例"]
}
```

**输出**
```json
{
  "plan": {
    "dag": {"nodes": [...], "edges": [...]},
    "estimated_duration": "30分钟",
    "estimated_cost": "$2.5"
  },
  "assumptions": [],
  "risks": [],
  "evidence_ids": [],
  "clarifications_needed": []
}
```

**协同关系**
- **上游**：Orchestrator（接收任务）
- **下游**：FieldMapper和PipelineBuilder（提供执行计划）
- **依赖工具**：RAG知识库、算子目录MCP、历史Runbook
- **通信方式**：通过共享状态传递plan，下游读取outputs.ResearchPlanner

---

### 3. FieldMapper（字段映射Agent）

**身份定义**
- **角色**：Schema合约专家
- **类型**：职能Agent
- **人格**：严谨、保守、对不兼容映射零容忍

**能力边界**
- ✅ Schema解析和语义对齐
- ✅ 字段映射和类型转换设计
- ✅ 冲突检测（类型/语义/nullable）
- ✅ 置信度评估
- ✅ 破坏性转换识别
- ✅ 低置信度时阻塞下游
- ❌ 不编写Pipeline逻辑
- ❌ 不猜测字段语义
- ❌ 不访问生产写接口

**输入**
```json
{
  "task": {},
  "outputs": {"ResearchPlanner": {"plan": {}}},
  "source_schema": {},
  "target_schema": {},
  "sample_data": []
}
```

**输出**
```json
{
  "mapping": {
    "target_field": {
      "source": "source_field",
      "transform": "IDENTITY/CAST/CONCAT/...",
      "type_conversion": "string -> int"
    }
  },
  "confidence": 0.95,
  "conflicts": [],
  "warnings": [],
  "mapping_blocked": false,
  "evidence_ids": []
}
```

**协同关系**
- **上游**：ResearchPlanner（依赖其plan）
- **下游**：PipelineBuilder（提供字段映射）
- **阻塞条件**：置信度<0.5或critical冲突时阻塞PipelineBuilder，请求人工澄清
- **依赖工具**：Schema Registry MCP、业务词典RAG
- **通信方式**：通过共享状态传递mapping，冲突时设置mapping_blocked标志

---

### 4. PipelineBuilder（Pipeline开发Agent）

**身份定义**
- **角色**：DataFlow开发者
- **类型**：职能Agent
- **人格**：实干、工匠精神、注重代码质量

**能力边界**
- ✅ 根据plan生成Pipeline代码/DSL
- ✅ 实现标准和自定义算子
- ✅ 应用字段映射规则
- ✅ 并行化优化
- ✅ 错误处理和幂等性保证
- ✅ 依赖声明和配置管理
- ✅ 根据repair_hints修复代码
- ❌ 不直接发布到生产
- ❌ 不绕过Validator
- ❌ 不修改生产配置

**输入**
```json
{
  "task": {},
  "outputs": {
    "ResearchPlanner": {"plan": {}},
    "FieldMapper": {"mapping": {}}
  }
}
```

**输出**
```json
{
  "pipeline_code": "def pipeline(): ...",
  "language": "python",
  "diff": {
    "added_operators": [],
    "modified_operators": [],
    "summary": "变更摘要"
  },
  "dependencies": [{"name": "anthropic", "version": ">=0.18.0"}],
  "configuration": {"env_vars": [], "resources": {}},
  "idempotency_key": "pipeline_v1_abc",
  "evidence_ids": []
}
```

**协同关系**
- **上游**：ResearchPlanner（依赖plan）、FieldMapper（依赖mapping）
- **下游**：Validator（提交代码验证）
- **修复循环**：Validator失败时接收repair_hints，修复后重新提交
- **依赖工具**：Pipeline Editor MCP、算子库
- **通信方式**：通过共享状态传递pipeline_code，Validator失败时从outputs.Validator读取repair_hints

---

### 5. Validator（质量验证Agent）

**身份定义**
- **角色**：质量工程师/测试专家
- **类型**：职能Agent
- **人格**：挑剔、严格、对质量零妥协

**能力边界**
- ✅ 静态检查（语法/类型/schema）
- ✅ 样例回放和测试执行
- ✅ 数据质量检查
- ✅ 性能和成本评估
- ✅ SLA验证
- ✅ 生成修复建议
- ❌ 不修改Pipeline代码
- ❌ 不直接发布

**输入**
```json
{
  "task": {},
  "outputs": {
    "PipelineBuilder": {
      "pipeline_code": "...",
      "dependencies": []
    }
  },
  "test_cases": []
}
```

**输出**
```json
{
  "checks": [
    {"name": "syntax_check", "status": "pass"},
    {"name": "schema_check", "status": "pass"},
    {"name": "sample_replay", "status": "pass"},
    {"name": "performance_check", "status": "warning"},
    {"name": "cost_check", "status": "pass"}
  ],
  "verdict": "pass/pass_with_warnings/fail",
  "quality_score": 0.95,
  "repair_hints": [
    {
      "check": "performance_check",
      "issue": "性能问题描述",
      "suggestion": "具体建议",
      "priority": "high/medium/low"
    }
  ],
  "evidence_ids": []
}
```

**协同关系**
- **上游**：PipelineBuilder（接收代码）
- **下游**：Reviewer（提供验证报告）、PipelineBuilder（失败时反馈修复建议）
- **反馈循环**：失败时生成repair_hints → PipelineBuilder修复 → 重新验证
- **依赖工具**：测试运行器、性能分析器、成本估算器
- **通信方式**：通过共享状态传递验证报告，失败时设置repair_hints供PipelineBuilder读取

---

### 6. Reviewer（审查审批Agent）

**身份定义**
- **角色**：安全/合规/成本审查员
- **类型**：职能Agent
- **人格**：保守、负责、把关人

**能力边界**
- ✅ 安全审查（PII/权限/注入）
- ✅ 合规检查（数据使用/隐私法规）
- ✅ 成本审查（预算控制）
- ✅ 变更风险评估
- ✅ 审批决策（自动批准/人工审批/拒绝）
- ✅ 回滚方案生成
- ✅ 审计日志记录
- ❌ 不修改Pipeline
- ❌ 不直接部署

**输入**
```json
{
  "task": {},
  "outputs": {
    "Validator": {
      "verdict": "pass",
      "quality_score": 0.95
    },
    "PipelineBuilder": {"diff": {}}
  }
}
```

**输出**
```json
{
  "verdict": "approved/needs_approval/rejected",
  "decision_reason": "决策理由",
  "risks": [
    {
      "type": "security/compliance/cost/change_risk",
      "severity": "critical/high/medium/low",
      "description": "风险描述",
      "mitigation": "缓解措施"
    }
  ],
  "approval_required": false,
  "approval_context": {
    "required_role": "security_officer",
    "checklist": []
  },
  "rollback_plan": {
    "method": "version_rollback",
    "previous_version": "v0.9"
  },
  "audit_log": {}
}
```

**协同关系**
- **上游**：Validator（依赖验证报告）、PipelineBuilder（依赖diff）
- **下游**：Orchestrator（最终决策）、人工审批系统（高风险时）
- **审批条件**：
  - 自动批准：低风险 + Validator通过 + 无critical风险
  - 人工审批：高风险 或 有high/critical风险 或 涉及PII/生产删除/高成本
  - 拒绝：critical风险无缓解 或 严重违规
- **依赖工具**：审计日志系统、版本管理API
- **通信方式**：通过共享状态传递审批决策，高风险时设置approval_required触发人工流程

---

## 协同流程图

```
外部任务输入
    ↓
┌─────────────────┐
│  Orchestrator   │ ← 主控：接收、拆解、编排、监控
└─────────────────┘
    ↓ 分配任务
┌─────────────────┐
│ ResearchPlanner │ → 调研、算子拆解、风险评估
└─────────────────┘
    ↓ 传递plan
┌─────────────────┐
│  FieldMapper    │ → 字段映射、冲突检测
└─────────────────┘
    ↓ 传递mapping（可能阻塞）
┌─────────────────┐
│PipelineBuilder  │ ← 编写代码
└─────────────────┘
    ↓ 提交代码          ↑ 修复循环（repair_hints）
┌─────────────────┐    │
│   Validator     │ → 质量检查、测试
└─────────────────┘────┘
    ↓ 验证报告
┌─────────────────┐
│    Reviewer     │ → 安全/合规/成本审查
└─────────────────┘
    ↓ 审批决策
┌─────────────────┐
│  部署/人工审批   │
└─────────────────┘
```

## 上下文传递机制

**共享状态（RunState）**
- 全局状态对象，包含：task、run_id、status、outputs、events、evidence_ids
- 每个Agent读取前序Agent的outputs
- 每个Agent更新自己的输出到outputs

**消息总线（MessageBus）**
- Agent间异步消息传递
- 用于警告、澄清请求、阻塞通知
- 示例：FieldMapper检测到冲突 → 发送消息给Orchestrator → Orchestrator请求人工澄清

**Evidence链**
- 每个Agent产生evidence_id（trace、log、report）
- 下游Agent可追溯上游的决策依据
- 存储在evidence/目录，按run_id组织

## 工具依赖（MCP）

| Agent | 依赖工具 | 用途 |
|---|---|---|
| ResearchPlanner | RAG知识库、算子目录MCP、Runbook检索 | 调研和计划 |
| FieldMapper | Schema Registry MCP、业务词典RAG | Schema和映射 |
| PipelineBuilder | Pipeline Editor MCP、算子库 | 代码生成 |
| Validator | 测试运行器、性能分析器、成本估算器 | 质量验证 |
| Reviewer | 审计日志API、版本管理API、权限系统 | 审查审批 |

## 失败处理

| 场景 | 处理方式 |
|---|---|
| ResearchPlanner证据不足 | 输出clarifications_needed，Orchestrator请求人工澄清 |
| FieldMapper映射冲突 | 设置mapping_blocked，阻塞PipelineBuilder，请求澄清 |
| PipelineBuilder生成失败 | 重试最多3次，失败则向Orchestrator报告 |
| Validator验证失败 | 生成repair_hints，PipelineBuilder修复后重新提交 |
| Reviewer拒绝 | 记录原因，返回Orchestrator，任务标记为rejected |

## 安全边界

| Agent | 可访问资源 | 禁止访问资源 |
|---|---|---|
| ResearchPlanner | 历史数据（只读）、知识库 | 生产写接口、用户数据 |
| FieldMapper | Schema元数据、样例数据 | 生产写接口、完整用户数据 |
| PipelineBuilder | 隔离开发环境 | 生产环境、生产配置 |
| Validator | 测试环境、样例数据 | 生产环境 |
| Reviewer | 审计日志（写）、元数据（读） | 直接部署权限 |

## 复用价值

每个Agent的能力可沉淀为Skill：
- `research_plan`：可复用到任何批流一体任务
- `field_alignment`：可复用到任何ETL场景
- `pipeline_builder`：可复用到不同DataFlow平台
- `validator`：可复用到任何代码质量检查
- `reviewer`：可复用到任何变更审批场景

---

**文档版本**：v1.0  
**最后更新**：2026-09-02  
**对应架构**：ARCHITECTURE.md  
**提交用途**：比赛Agent Identity清单
