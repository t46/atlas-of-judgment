#!/bin/zsh
set -uo pipefail

repo="${0:A:h:h}"
run_dir="$repo/data/analysis/iclr/reviewer-logic-direct-qwen-retry-24k-v1"

if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  print -u2 "DASHSCOPE_API_KEY must be injected once by the launcher"
  exit 2
fi

cd "$repo"
while true; do
  .venv/bin/python scripts/qwen_reviewer_logic_direct.py run \
    --output "$run_dir" \
    --workers 64 \
    --progress-every 100
  status=$?
  if (( status == 0 )); then
    exit 0
  fi
  print -u2 "retry runner exited with status $status; durable state retained; restarting in 15s"
  sleep 15
done
