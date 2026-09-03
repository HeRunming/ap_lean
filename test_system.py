#!/usr/bin/env python3
"""测试脚本 - 验证系统基本功能"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from runtime.config import Config
from runtime.state import RunState
from runtime.message_bus import MessageBus

def test_config():
    """测试配置加载"""
    print("测试配置加载...")
    config = Config("config/config.yaml")

    assert config.claude_model == "claude-opus-5"
    assert config.max_tokens == 4096
    assert config.max_retries == 3

    print("✓ 配置加载成功")

def test_state():
    """测试状态管理"""
    print("\n测试状态管理...")

    task = {"kind": "test", "title": "Test Task"}
    state = RunState(task=task)

    # Test event
    state.event("TestAgent", "test_action", {"key": "value"})
    assert len(state.events) == 1

    # Test agent status
    state.set_agent_status("TestAgent", "running")
    assert state.agent_states["TestAgent"] == "running"

    # Test error recording
    state.record_error("TestAgent", "test error", {"detail": "test"})
    assert len(state.errors) == 1

    # Test evidence
    state.add_evidence("evidence_123")
    assert "evidence_123" in state.evidence_ids

    print("✓ 状态管理成功")

def test_message_bus():
    """测试消息总线"""
    print("\n测试消息总线...")

    bus = MessageBus()

    # Send message
    msg_id = bus.send("Agent1", "Agent2", {"data": "test"})
    assert msg_id is not None

    # Get messages
    messages = bus.get_messages_for("Agent2")
    assert len(messages) == 1
    assert messages[0].from_agent == "Agent1"

    # Get latest
    latest = bus.get_latest_from("Agent1", "Agent2")
    assert latest is not None
    assert latest.content["data"] == "test"

    print("✓ 消息总线成功")

def test_orchestrator_dry_run():
    """测试编排器dry run"""
    print("\n测试编排器dry run...")

    from runtime.orchestrator import AgentOrchestrator

    config = Config("config/config.yaml")
    orchestrator = AgentOrchestrator(config)

    task = {
        "kind": "test",
        "title": "Test Pipeline",
        "prompt": "Test",
        "risk": "low"
    }

    state, evidence_path = orchestrator.run(task, dry_run=True)

    assert state.status in ["completed", "approval_required"]
    assert len(state.outputs) > 0
    assert evidence_path.exists()

    orchestrator.close()

    print("✓ 编排器dry run成功")
    print(f"  证据文件: {evidence_path}")

def main():
    """运行所有测试"""
    print("=" * 60)
    print("DataFlow AgentTeams - 系统测试")
    print("=" * 60)

    try:
        test_config()
        test_state()
        test_message_bus()
        test_orchestrator_dry_run()

        print("\n" + "=" * 60)
        print("✅ 所有测试通过!")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
