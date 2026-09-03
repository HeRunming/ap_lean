#!/usr/bin/env bash
# 快速启动脚本

set -e

echo "=== DataFlow AgentTeams 启动 ==="
echo

# 检查环境变量
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "错误: ANTHROPIC_API_KEY 未设置"
    echo "请创建 config/.env 文件并设置 API key"
    echo "参考 config/.env.example"
    exit 1
fi

# 安装依赖
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

echo "激活虚拟环境..."
source venv/bin/activate

echo "安装依赖..."
pip install -q -e .

echo
echo "✓ 环境准备完成"
echo
echo "使用方法:"
echo "  python -m runtime.orchestrator --task examples/math_clean_synthesis_task.json"
echo "  python -m runtime.orchestrator --task examples/task.json --dry-run"
echo
