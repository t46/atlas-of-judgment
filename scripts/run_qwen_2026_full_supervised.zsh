#!/bin/zsh
set -u

repo="/Users/s30825/unktok/dev/ml-top-conf-review-analysis"
run_dir="$repo/data/analysis/iclr/review-logic-qwen-2026-full"
state="$run_dir/state.sqlite3"
item_id="<your-1password-item-id>"

cd "$repo" || exit 1
export DASHSCOPE_API_KEY="$(
  op item get "$item_id" --vault unktok --format json |
    jq -r '.fields[] | select(.id=="credential") | .value'
)"
if [[ -z "$DASHSCOPE_API_KEY" ]]; then
  print -u2 "Qwen API key is empty"
  exit 1
fi

restart_count=0
while (( restart_count < 5 )); do
  prepared="$(sqlite3 "$state" "SELECT COUNT(*) FROM requests WHERE status='prepared';")"
  if [[ "$prepared" == "0" ]]; then
    print "No prepared requests remain; supervisor stopping."
    exit 0
  fi
  print "Starting Qwen runner with $prepared prepared requests remaining."
  uv run python scripts/qwen_review_logic_batch.py run-realtime \
    --output "$run_dir" --workers 32 --progress-every 500
  exit_code=$?
  if (( exit_code == 0 )); then
    print "Runner exited successfully."
    exit 0
  fi
  restart_count=$((restart_count + 1))
  print -u2 "Runner exited with $exit_code; restart $restart_count/5 in 30 seconds."
  sleep 30
done

print -u2 "Supervisor exhausted five restarts; manual inspection required."
exit 1
