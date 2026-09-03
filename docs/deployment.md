# DataFlow AgentTeams - 部署与运维指南

## 部署架构

### 单机部署（开发/测试）

```
┌─────────────────────────────────────┐
│  DataFlow AgentTeams                │
│                                     │
│  ┌──────────────────────────────┐  │
│  │  Orchestrator                │  │
│  │  (主控进程)                   │  │
│  └──────────────────────────────┘  │
│                                     │
│  ┌──────────────────────────────┐  │
│  │  Agent Sessions              │  │
│  │  (Claude API 会话池)         │  │
│  └──────────────────────────────┘  │
│                                     │
│  ┌──────────────────────────────┐  │
│  │  Evidence Store              │  │
│  │  (文件系统)                   │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
           │
           ↓
    Claude API
    (https://01tree.ai/claudecode)
```

### 生产部署（可选）

```
┌─────────────────────────────────────┐
│  Load Balancer                      │
└─────────────────────────────────────┘
           │
    ┌──────┴──────┐
    ↓             ↓
┌─────────┐  ┌─────────┐
│ Worker1 │  │ Worker2 │  (多实例)
└─────────┘  └─────────┘
    │             │
    └──────┬──────┘
           ↓
┌─────────────────────────────────────┐
│  Shared State (Redis)               │
└─────────────────────────────────────┘
           │
           ↓
┌─────────────────────────────────────┐
│  Evidence Store (S3/OSS)            │
└─────────────────────────────────────┘
```

## 环境要求

### 硬件
- **CPU**: 2+ cores
- **内存**: 4GB+
- **磁盘**: 10GB+ (evidence存储)

### 软件
- **Python**: 3.9+
- **网络**: 访问Claude API (https://01tree.ai/claudecode)
- **可选**: Redis (生产环境共享状态)

## 安装步骤

### 1. 下载代码

```bash
cd /Users/blackbox/dataflow-agent-teams
```

### 2. 配置环境变量

```bash
cp config/.env.example config/.env
```

编辑 `config/.env`:

```bash
# 必需
ANTHROPIC_API_KEY=sk-ant-oat01-<your-key-here>
ANTHROPIC_BASE_URL=https://01tree.ai/claudecode

# 可选
LOG_LEVEL=INFO
EVIDENCE_DIR=evidence
```

### 3. 安装依赖

```bash
bash setup.sh
```

或手动：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

## 运行

### 开发模式

**Dry Run（测试）**:
```bash
python -m runtime.orchestrator \
  --task examples/math_clean_synthesis_task.json \
  --dry-run
```

**真实运行**:
```bash
source config/.env
python -m runtime.orchestrator \
  --task examples/math_clean_synthesis_task.json
```

### 生产模式

使用进程管理器（如systemd、supervisor）:

**systemd service示例** (`/etc/systemd/system/dataflow-agent.service`):

```ini
[Unit]
Description=DataFlow AgentTeams
After=network.target

[Service]
Type=simple
User=dataflow
WorkingDirectory=/opt/dataflow-agent-teams
Environment="PATH=/opt/dataflow-agent-teams/venv/bin"
EnvironmentFile=/opt/dataflow-agent-teams/config/.env
ExecStart=/opt/dataflow-agent-teams/venv/bin/python -m runtime.orchestrator --task /var/tasks/current.json
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务:
```bash
sudo systemctl enable dataflow-agent
sudo systemctl start dataflow-agent
sudo systemctl status dataflow-agent
```

## 监控

### 日志

查看运行日志:
```bash
# 标准输出
python -m runtime.orchestrator --task task.json 2>&1 | tee run.log

# systemd
journalctl -u dataflow-agent -f
```

### Evidence追踪

```bash
# 查看最新的执行记录
ls -lht evidence/*.json | head -5

# 查看具体执行
cat evidence/<run_id>.json | jq .

# 查看消息记录
cat evidence/<run_id>_messages.json | jq .
```

### 健康检查

```bash
# 运行系统测试
python test_system.py
```

### Metrics（可选）

收集关键指标：
- Agent执行成功率
- 平均执行时间
- API调用成本
- 质量分分布

可集成Prometheus/Grafana进行可视化。

## 成本控制

### API成本

Claude Opus 5 定价（参考）:
- 输入: $15/M tokens
- 输出: $75/M tokens

**估算单次任务成本**:
- ResearchPlanner: ~500 tokens input + 2000 tokens output ≈ $0.16
- FieldMapper: ~300 tokens input + 1000 tokens output ≈ $0.08
- PipelineBuilder: ~1000 tokens input + 3000 tokens output ≈ $0.24
- Validator: ~2000 tokens input + 1500 tokens output ≈ $0.14
- Reviewer: ~1500 tokens input + 1000 tokens output ≈ $0.10

**单次任务总成本**: ~$0.72

### 成本优化

1. **使用更便宜的模型**（特定Agent）:
```yaml
agents:
  validator:
    model: "claude-sonnet-4"  # 更便宜
```

2. **减少重试次数**:
```yaml
execution:
  max_retries: 1  # 降低到1
```

3. **设置预算上限**:
```yaml
cost:
  default_budget: 10.0  # USD
```

4. **批量处理**:
多个任务合并，减少API调用次数。

## 扩展

### 添加自定义Agent

1. 创建契约文件: `agents/contracts/my_agent.md`
2. 在Orchestrator中注册: 修改 `STAGES` 列表
3. 实现Agent逻辑

### 集成MCP工具

在 `config/config.yaml` 启用MCP:

```yaml
mcp:
  enabled: true
  tools:
    schema_registry:
      endpoint: "http://localhost:8001"
    dataflow_api:
      endpoint: "http://localhost:8002"
```

实现MCP适配器: `runtime/mcp_adapter.py`

### 替换状态存储

生产环境使用Redis:

```python
# runtime/state.py
class RedisRunState(RunState):
    def __init__(self, redis_client, run_id):
        self.redis = redis_client
        self.run_id = run_id
        # Load from Redis
        
    def save(self, path=None):
        # Save to Redis
        self.redis.set(f"run:{self.run_id}", json.dumps(asdict(self)))
```

## 故障排查

### API调用失败

**症状**: "Connection error" or "API key invalid"

**检查**:
1. `ANTHROPIC_API_KEY` 是否正确
2. `ANTHROPIC_BASE_URL` 是否可访问
3. 网络连接: `curl https://01tree.ai/claudecode`

### Agent执行超时

**症状**: "Agent execution timeout"

**解决**:
```yaml
execution:
  timeout: 600  # 增加到10分钟
```

### 内存不足

**症状**: "Out of memory"

**解决**:
1. 增加系统内存
2. 限制并发Agent数量
3. 清理旧evidence文件

### Evidence文件过大

**解决**:
```bash
# 清理30天前的evidence
find evidence/ -name "*.json" -mtime +30 -delete

# 压缩归档
tar czf evidence_$(date +%Y%m).tar.gz evidence/*.json
rm evidence/*.json
```

## 安全

### API密钥管理

1. **不要提交到git**:
```bash
# 确保.gitignore包含
config/.env
```

2. **使用环境变量**:
```bash
export ANTHROPIC_API_KEY=sk-...
```

3. **生产环境使用密钥管理服务** (AWS Secrets Manager, Vault等)

### 数据安全

1. **PII保护**: Reviewer会检测PII字段
2. **访问控制**: 限制evidence目录访问权限
3. **审计日志**: 所有操作记录到audit_log

## 备份与恢复

### 备份

```bash
# 备份配置
tar czf backup_config_$(date +%Y%m%d).tar.gz config/

# 备份evidence
tar czf backup_evidence_$(date +%Y%m%d).tar.gz evidence/
```

### 恢复

```bash
# 恢复配置
tar xzf backup_config_20260902.tar.gz

# 恢复evidence
tar xzf backup_evidence_20260902.tar.gz
```

## 性能调优

### 并行化

当前版本Agent顺序执行，可优化为：

```python
# 并行执行独立Agent
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {
        executor.submit(execute_agent, "ResearchPlanner"): "ResearchPlanner",
        executor.submit(execute_agent, "FieldMapper"): "FieldMapper",
    }
```

### 缓存

缓存重复的plan和mapping:

```python
import hashlib

def get_cached_plan(task_hash):
    cache_file = f"cache/plans/{task_hash}.json"
    if Path(cache_file).exists():
        return json.loads(Path(cache_file).read_text())
    return None
```

## 联系与支持

- **文档**: 查看 README.md、ARCHITECTURE.md
- **测试**: 运行 `python test_system.py`
- **问题**: 提交 GitHub issue

---

**版本**: v0.1.0  
**更新**: 2026-09-02
