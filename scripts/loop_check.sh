#!/usr/bin/env bash
# Cursor `stop` hook: keeps a role agent looping on its IN_PROGRESS task until the
# task's exit command passes, the per-task iteration cap is hit, or the session cap
# is hit. Opt-in: only active when `.cursor/autoloop.enabled` exists (gitignored), so
# the orchestrator / reviewer chats are never forced to loop.
#
# Reads hook JSON on stdin (ignored except for an optional conversation id), writes
# `{"followup_message": "..."}` on stdout to continue, or nothing to let the agent stop.
#
# Caps:
#   PER_TASK_CAP  - matches the loop protocol in .cursor/rules/00-global.mdc (6)
#   SESSION_CAP   - hard ceiling on follow-ups per session (25) so a stuck agent
#                   cannot burn credits forever. hooks.json also sets loop_limit=25.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 0

PER_TASK_CAP=6
SESSION_CAP=25
STATE_DIR="${ARCO_LOOP_STATE_DIR:-$ROOT/.cursor/.loop_state}"
ENABLE_FLAG="$ROOT/.cursor/autoloop.enabled"
TASKS_FILE="${ARCO_TASKS_FILE:-$ROOT/TASKS.md}"   # override only for testing the hook

input="$(cat || true)"

[[ -f "$ENABLE_FLAG" ]] || exit 0
[[ -f "$TASKS_FILE" ]] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

mkdir -p "$STATE_DIR"

session_id="$(printf '%s' "$input" | jq -r '.conversation_id // .session_id // "default"' 2>/dev/null || echo default)"
session_file="$STATE_DIR/session-${session_id}.count"
session_count=0
[[ -f "$session_file" ]] && session_count="$(cat "$session_file")"
if (( session_count >= SESSION_CAP )); then
  exit 0
fi

# First IN_PROGRESS row in the task table. Columns:
# | id | owner | depends_on | task | exit command | status | evidence |
row="$(grep -E '^\|' "$TASKS_FILE" | awk -F'|' '$7 ~ /IN_PROGRESS/ {print; exit}')"
[[ -n "$row" ]] || exit 0

task_id="$(printf '%s' "$row" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')"
exit_cmd="$(printf '%s' "$row" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $6); print $6}' | sed -e 's/^`//' -e 's/`$//')"
[[ -n "$exit_cmd" ]] || exit 0

task_file="$STATE_DIR/task-${task_id}.count"
task_count=0
[[ -f "$task_file" ]] && task_count="$(cat "$task_file")"
if (( task_count >= PER_TASK_CAP )); then
  # Cap hit: tell the agent once to mark BLOCKED, then stop nudging.
  if [[ ! -f "$task_file.capped" ]]; then
    touch "$task_file.capped"
    echo $((session_count + 1)) > "$session_file"
    jq -nc --arg t "$task_id" \
      '{followup_message: ("Iteration cap (6) reached for task " + $t + ". Set it to BLOCKED in TASKS.md, write what you tried and what you suspect under BLOCKERS in HANDOFFS.md, then move to your next unblocked task or stop.")}'
  fi
  exit 0
fi

log_file="$STATE_DIR/task-${task_id}.last.log"
if bash -lc "$exit_cmd" > "$log_file" 2>&1; then
  rm -f "$task_file" "$task_file.capped"
  exit 0
fi

echo $((task_count + 1)) > "$task_file"
echo $((session_count + 1)) > "$session_file"
tail_out="$(tail -n 25 "$log_file" | cut -c1-400)"

jq -nc --arg t "$task_id" --arg c "$exit_cmd" --arg o "$tail_out" --arg n "$((task_count + 1))" \
  '{followup_message: ("Exit criteria for task " + $t + " still failing (iteration " + $n + "/6). Command: `" + $c + "`. Last output:\n" + $o + "\n\nDiagnose the root cause in one line, fix the cause (not the symptom), re-run the exit command, and log the iteration in TASKS.md.")}'
exit 0
