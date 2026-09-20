#!/usr/bin/env python3
"""验证依赖放松逻辑：proof可以在dependency的statement完成后就开始，无需等待proof完成"""

from leanflow_cli.formalization.corpus_campaign import (
    _batch_dependencies_ready,
    _batch_soft_dependencies_ready,
)


def test_proof_can_run_with_statement_only_deps():
    """核心场景：proof stage的batch依赖只完成了statement的其他batch"""

    batch = {"id": "proof_batch", "dependency_labels": ["lemma_a", "lemma_b"]}

    # 场景1: 依赖的lemma都只完成了statement，proof还没做
    label_statuses = {
        "lemma_a": "statements_completed",
        "lemma_b": "statements_completed",
    }

    ready = _batch_dependencies_ready(
        batch, stage="proofs", label_statuses=label_statuses  # 这是个proof batch
    )

    print("✓ 场景1: proof batch的依赖只有statements_completed")
    print(f"  依赖状态: {label_statuses}")
    print(f"  proof batch可以开始? {ready}")
    assert ready, "Expected proof to be ready when deps have statements_completed"

    # 场景2: 一个依赖还在pending
    label_statuses["lemma_a"] = "pending"
    ready = _batch_dependencies_ready(batch, stage="proofs", label_statuses=label_statuses)
    print("\n✓ 场景2: 有依赖还在pending")
    print(f"  依赖状态: {label_statuses}")
    print(f"  proof batch可以开始? {ready}")
    assert not ready, "Expected proof to wait when a dep is pending"

    # 场景3: 依赖都完成了proof（当然也可以）
    label_statuses = {
        "lemma_a": "proofs_completed",
        "lemma_b": "statements_completed",
    }
    ready = _batch_dependencies_ready(batch, stage="proofs", label_statuses=label_statuses)
    print("\n✓ 场景3: 依赖混合了proofs_completed和statements_completed")
    print(f"  依赖状态: {label_statuses}")
    print(f"  proof batch可以开始? {ready}")
    assert ready, "Expected proof to be ready with mixed completion states"

    # 场景4: soft dependencies也遵循同样规则
    ready = _batch_soft_dependencies_ready(batch, stage="proofs", label_statuses=label_statuses)
    print("\n✓ 场景4: soft dependencies也只需要statements_completed")
    print(f"  soft依赖可用? {ready}")
    assert ready, "Expected soft deps to follow same rule"

    print("\n" + "=" * 60)
    print("✅ 所有验证通过！")
    print("proof现在可以在依赖的statement完成后立即开始，")
    print("无需等待依赖的proof完成。")
    print("=" * 60)


if __name__ == "__main__":
    test_proof_can_run_with_statement_only_deps()
