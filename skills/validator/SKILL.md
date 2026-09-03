# validator
用途：静态检查、样例回放、数据质量、SLA/成本评估。输入 draft；输出 `{checks, verdict, repair_hints, evidence_ids}`。失败不修改草稿，回传 Builder。
