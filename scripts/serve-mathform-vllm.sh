#!/usr/bin/env bash
set -euo pipefail

# Serve MathForm on one explicitly selected, otherwise idle GPU.  Keep model
# weights on a data volume: the server's root filesystem may be too small.
gpu_id="${MATHFORM_GPU_ID:-}"
model_id="${MATHFORM_MODEL_ID:-openbmb/MathForm-8B}"
served_name="${MATHFORM_SERVED_NAME:-MathForm-8B}"
port="${MATHFORM_PORT:-18080}"
cache_root="${MATHFORM_CACHE_ROOT:-/data/hrm/mathform-cache}"
minimum_free_mib="${MATHFORM_MIN_FREE_MIB:-20000}"

if [[ -z "${gpu_id}" ]]; then
  echo "MATHFORM_GPU_ID is required; select an idle GPU explicitly." >&2
  exit 2
fi
if ! [[ "${gpu_id}" =~ ^[0-9]+$ ]]; then
  echo "MATHFORM_GPU_ID must be a non-negative integer." >&2
  exit 2
fi
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi is unavailable." >&2
  exit 2
fi
if ! command -v vllm >/dev/null 2>&1; then
  echo "vllm is unavailable in the active environment." >&2
  exit 2
fi

gpu_row="$({ nvidia-smi --query-gpu=index,memory.free,utilization.gpu --format=csv,noheader,nounits; } | awk -F, -v wanted="${gpu_id}" '$1 + 0 == wanted {gsub(/ /, "", $2); gsub(/ /, "", $3); print $2 " " $3}')"
if [[ -z "${gpu_row}" ]]; then
  echo "GPU ${gpu_id} does not exist." >&2
  exit 2
fi
read -r free_mib utilization <<<"${gpu_row}"
if (( free_mib < minimum_free_mib || utilization > 5 )); then
  echo "GPU ${gpu_id} is not idle enough: free=${free_mib}MiB utilization=${utilization}%." >&2
  exit 3
fi

mkdir -p "${cache_root}"
export CUDA_VISIBLE_DEVICES="${gpu_id}"
export HF_HOME="${cache_root}/huggingface"
export VLLM_CACHE_ROOT="${cache_root}/vllm"

exec vllm serve "${model_id}" \
  --served-model-name "${served_name}" \
  --host 127.0.0.1 \
  --port "${port}" \
  --dtype bfloat16 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.90
