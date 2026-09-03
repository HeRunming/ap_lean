# reviewer
用途：审查 PII、权限、预算和变更风险，生成 approval/rollback plan。高风险动作必须人工确认；所有决策写审计日志，拒绝则回滚到 previous_version。
