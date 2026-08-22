# MathForm statement provider

MathForm-8B is a statement autoformalizer. It belongs in the bounded statement
lane; it does not replace the LeanFlow proof agent.

## Remote server

Choose an actually idle GPU and keep the model cache on the data disk:

```bash
MATHFORM_GPU_ID=5 MATHFORM_CACHE_ROOT=/data/hrm/mathform-cache \
  scripts/serve-mathform-vllm.sh
```

The launcher refuses GPUs with less than 20 GiB free memory or more than 5%
utilization. It binds only to remote loopback. From the client machine, forward
the endpoint instead of exposing it publicly:

```bash
ssh -N -L 18080:127.0.0.1:18080 remote-lean-host
```

Register the forwarded endpoint as a named custom provider in LeanFlow config:

```yaml
custom_providers:
  - name: mathform-remote
    base_url: http://127.0.0.1:18080/v1
    api_key: EMPTY
```

## Campaign routing

Keep statement generation and semantic judgment independent. The preferred
generator may fall back without changing the judge:

```bash
python -m leanflow_cli.formalization.corpus_campaign_runner \
  FateXWork/Questions/campaign.json \
  --project-root . --execute --bounded-statements --reserve-usd 1 \
  --provider openai-codex --model gpt-5.5 \
  --statement-planner-provider openai-codex \
  --statement-planner-model gpt-5.5 \
  --statement-provider mathform-remote --statement-model MathForm-8B \
  --statement-fallback-provider openai-codex \
  --statement-fallback-model gpt-5.5 \
  --statement-judge-provider openai-codex \
  --statement-judge-model gpt-5.5 \
  --statement-candidates 8 --statement-candidate-workers 4
```

The planner defaults to the campaign provider and its configured default model;
the explicit planner flags above make the routing auditable. The generator uses
`--statement-provider`; transport/model failure invokes the configured fallback
once. The source-fidelity judge always uses `--statement-judge-provider`, so a
specialized generator never approves its own statement.

Candidate generation and Lean compilation are parallelized, duplicate Lean
files are removed before compilation, and semantic review stops at the first
PASS. Use `--statement-candidates 8` only for a local/unmetered endpoint. Keep
the default value `1` while the generator is a paid API: the campaign reserve
limits total recorded cost but cannot cancel already concurrent requests.
