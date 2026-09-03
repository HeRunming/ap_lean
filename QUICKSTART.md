# DataFlow AgentTeams - 快速开始

本指南帮助你快速运行DataFlow AgentTeams系统。

## 前置要求

- Python 3.9+
- Claude API密钥

## 安装

1. **克隆/下载项目**（已完成）

2. **配置环境变量**

创建 `config/.env` 文件：

```bash
cp config/.env.example config/.env
```

编辑 `config/.env`，设置你的API密钥：

```bash
ANTHROPIC_API_KEY=sk-ant-oat01-your-key-here
ANTHROPIC_BASE_URL=https://01tree.ai/claudecode
```

3. **运行安装脚本**

```bash
bash setup.sh
```

这会创建虚拟环境并安装依赖。

## 运行示例

### Dry Run（不调用API）

```bash
python -m runtime.orchestrator \
  --task examples/math_clean_synthesis_task.json \
  --dry-run
```

这会展示任务拆解和状态迁移，不调用Claude API。

### 真实运行

```bash
# 确保环境变量已设置
source config/.env

# 运行
python -m runtime.orchestrator \
  --task examples/math_clean_synthesis_task.json
```

### 自定义任务

创建你的任务JSON：

```json
{
  "kind": "数据清洗",
  "title": "用户数据ETL",
  "prompt": "将原始用户数据清洗并加载到目标表",
  "input_contract": {
    "user_id": "int",
    "name": "string",
    "email": "string"
  },
  "operators": ["validate_email", "deduplicate", "transform"],
  "risk": "medium",
  "constraints": {
    "sla_minutes": 30,
    "pii": true
  }
}
```

运行：

```bash
python -m runtime.orchestrator --task my_task.json
```

## 查看结果

执行完成后会在 `evidence/` 目录生成证据文件：

```bash
# 查看执行轨迹
cat evidence/<run_id>.json

# 查看消息记录
cat evidence/<run_id>_messages.json
```

## 下一步

- 阅读 `ARCHITECTURE.md` 了解系统架构
- 阅读 `AGENT_IDENTITY.md` 了解各Agent职责
- 查看 `agents/contracts/` 了解Agent契约
- 修改 `config/config.yaml` 调整系统配置

## 常见问题

### API调用失败

检查：
1. `ANTHROPIC_API_KEY` 是否正确
2. `ANTHROPIC_BASE_URL` 是否可访问
3. 网络连接是否正常

### 依赖安装失败

```bash
# 手动安装
pip install anthropic httpx pyyaml python-dotenv
```

### 权限问题

```bash
chmod +x setup.sh
```

## 联系

遇到问题请查看日志或提交issue。
