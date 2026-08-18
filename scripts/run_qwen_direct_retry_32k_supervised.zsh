#!/bin/zsh
set -uo pipefail

repo="${0:A:h:h}"
run_dir="$repo/data/analysis/iclr/reviewer-logic-direct-qwen-retry-32k-v1"
item_id="<your-1password-item-id>"

cd "$repo"

# Resolve the credential once for the lifetime of this supervisor. Worker
# requests reuse the in-memory environment and never call 1Password directly.
export DASHSCOPE_API_KEY="$(
  op item get "$item_id" --vault unktok --format json |
    jq -r '.fields[] | select(.id=="credential") | .value'
)"
if [[ -z "$DASHSCOPE_API_KEY" || "$DASHSCOPE_API_KEY" == "null" ]]; then
  print -u2 "Qwen API key is empty"
  exit 2
fi

while true; do
  prepared="$(sqlite3 "$run_dir/state.sqlite3" \
    "SELECT COUNT(*) FROM requests WHERE status='prepared';")"
  if [[ "$prepared" == "0" ]]; then
    print "No prepared requests remain; supervisor stopping."
    exit 0
  fi
  print "Starting 32k Qwen retry with $prepared prepared requests remaining."
  uv run python scripts/qwen_reviewer_logic_direct.py run \
    --output "$run_dir" \
    --workers 64 \
    --progress-every 50
  exit_status=$?
  if (( exit_status == 0 )); then
    exit 0
  fi
  print -u2 "retry runner exited with status $exit_status; durable state retained; restarting in 15s"
  sleep 15
done
