#!/usr/bin/env bash
# `make demo`: the full Lahaina scenario end to end with no manual steps.
#
#   1. docker compose up -d --wait   (PostGIS, Redis, blackboard, agents, base-station,
#                                     benchmark, simulator; + edge-capture if DEMO_VISION=1)
#   2. scripts/healthcheck.sh
#   3. mesh relay (:MESH_RELAY_PORT) + Next.js dashboard (:DASHBOARD_PORT) in the background
#   4. load the scenario from t=0, set DEMO_SPEED, play
#   5. open the dashboard in the browser (presentation mode when DEMO_PRESENT=1)
#   6. follow the scenario clock until it finishes, then print what the agents did
#
# Everything is driven over the public HTTP APIs - the same calls the dashboard makes.
# `make demo-stop` stops the background processes; `make down` stops the containers.
# Env: ARCO_DEFAULT_SCENARIO (lahaina_2023), DEMO_SPEED (30), DEMO_PRESENT (0),
#      DEMO_VISION (0), DEMO_OPEN (1), DEMO_FOLLOW (1), DASHBOARD_PORT (3000),
#      MESH_RELAY_PORT (8787), SIMULATOR_URL/BLACKBOARD_URL/AGENTS_URL overrides.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# .env fills in anything not already set in the environment (so `DASHBOARD_PORT=3300 make
# demo` wins over the file).
if [[ -f .env ]]; then
  while IFS='=' read -r k v; do
    [[ $k =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    [[ -z ${!k+x} ]] && export "$k=$v"
  done < <(grep -E '^[A-Z_][A-Z0-9_]*=' .env || true)
fi

SCENARIO="${ARCO_DEFAULT_SCENARIO:-lahaina_2023}"
SPEED="${DEMO_SPEED:-30}"
PRESENT="${DEMO_PRESENT:-0}"
VISION="${DEMO_VISION:-0}"
OPEN="${DEMO_OPEN:-1}"
FOLLOW="${DEMO_FOLLOW:-1}"
DASHBOARD_PORT="${DASHBOARD_PORT:-3000}"
MESH_RELAY_PORT="${MESH_RELAY_PORT:-8787}"
SIM="${SIMULATOR_URL:-http://localhost:${SIMULATOR_PORT:-8004}}"
BB="${BLACKBOARD_URL:-http://localhost:${BLACKBOARD_PORT:-8001}}"
AGENTS="${AGENTS_URL:-http://localhost:${AGENTS_PORT:-8003}}"
COMPOSE="${COMPOSE:-docker compose}"
RUN_DIR="data/cache/demo"
mkdir -p "$RUN_DIR"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die()  { printf '\033[1;31mdemo: %s\033[0m\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is required"; }
need curl; need python3; need pnpm

json() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

wait_http() { # url seconds [needle]
  local url=$1 secs=$2 needle=${3:-} i body
  for ((i = 0; i < secs; i++)); do
    if body=$(curl -fsS "$url" 2>/dev/null); then
      [[ -z $needle || $body == *"$needle"* ]] && return 0
    fi
    sleep 1
  done
  return 1
}

start_bg() { # name pidfile logfile command...
  local name=$1 pid=$2 log=$3; shift 3
  if [[ -f $pid ]] && kill -0 "$(cat "$pid")" 2>/dev/null; then
    note "$name already running (pid $(cat "$pid"))"; return 0
  fi
  # New session (setsid) so the process survives make / the terminal going away.
  nohup python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@" \
    >"$log" 2>&1 </dev/null &
  echo $! >"$pid"
  disown || true
  note "$name started (pid $!, log $log)"
}

# ---------------------------------------------------------------------------- 1. stack
say "1/6 compose stack"
if [[ $VISION == 1 ]]; then
  $COMPOSE --profile vision up -d --wait
else
  $COMPOSE up -d --wait
fi
say "2/6 healthcheck"
./scripts/healthcheck.sh

# ------------------------------------------------------------------- 3. relay + dashboard
say "3/6 mesh relay + dashboard"
start_bg "mesh relay" "$RUN_DIR/relay.pid" "$RUN_DIR/relay.log" \
  env MESH_RELAY_PORT="$MESH_RELAY_PORT" pnpm --filter @arco/mobile relay
# Pick a free port if DASHBOARD_PORT is held by something that is not an ARCO dashboard.
port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
if ! port_free "$DASHBOARD_PORT" && [[ $(curl -fsS "http://localhost:$DASHBOARD_PORT/" 2>/dev/null) != *ARCO* ]]; then
  for ((p = DASHBOARD_PORT + 1; p < DASHBOARD_PORT + 20; p++)); do
    if port_free "$p"; then note "port $DASHBOARD_PORT is taken by another process; using $p"; DASHBOARD_PORT=$p; break; fi
  done
fi
start_bg "dashboard" "$RUN_DIR/dashboard.pid" "$RUN_DIR/dashboard.log" \
  env DASHBOARD_PORT="$DASHBOARD_PORT" NEXT_PUBLIC_MESH_RELAY_WS="ws://localhost:${MESH_RELAY_PORT}" \
      NEXT_PUBLIC_DEFAULT_SCENARIO="$SCENARIO" pnpm --filter @arco/dashboard dev
DASH_URL="http://localhost:${DASHBOARD_PORT}"
wait_http "$DASH_URL" 120 "ARCO" || die "no ARCO dashboard on $DASH_URL - port taken or build failed (see $RUN_DIR/dashboard.log)"
note "dashboard up at $DASH_URL"

# ----------------------------------------------------------------------- 4. play scenario
say "4/6 load + play $SCENARIO at ${SPEED}x"
curl -fsS -X POST "$SIM/pause" >/dev/null
state=$(curl -fsS -X POST "$SIM/load" -H 'content-type: application/json' -d "{\"scenario_id\":\"$SCENARIO\"}")
duration=$(echo "$state" | json 'd["duration_s"]')
name=$(echo "$state" | json 'd["scenario_name"]')
label=$(echo "$state" | json 'd["label"]')
curl -fsS -X POST "$SIM/seek"  -H 'content-type: application/json' -d '{"t":0}' >/dev/null
curl -fsS -X POST "$SIM/speed" -H 'content-type: application/json' -d "{\"speed\":$SPEED}" >/dev/null
curl -fsS -X POST "$SIM/chaos/cellular" -H 'content-type: application/json' -d '{"up":true,"reason":"demo start"}' >/dev/null
run_started=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))")
curl -fsS -X POST "$SIM/play" >/dev/null
note "$name - $label - ${duration}s of scenario time, ~$(python3 -c "print(round($duration/$SPEED))")s wall"

# --------------------------------------------------------------------------- 5. browser
say "5/6 open the dashboard"
url="$DASH_URL/"
[[ $PRESENT == 1 ]] && url="$DASH_URL/?present=1"
if [[ $OPEN == 1 ]]; then
  if command -v open >/dev/null 2>&1; then open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true
  else note "no browser opener found; visit $url"; fi
fi
note "$url   (Ctrl/Cmd+P toggles presentation mode)"

# ----------------------------------------------------------------------------- 6. follow
say "6/6 scenario clock"
if [[ $FOLLOW == 1 ]]; then
  while :; do
    st=$(curl -fsS "$SIM/state")
    t=$(echo "$st" | json 'd["t"]')
    playing=$(echo "$st" | json 'd["playing"]')
    cur=$(echo "$st" | json 'd["timeline_cursor"]')
    tot=$(echo "$st" | json 'd["timeline_total"]')
    h=$(curl -fsS "$AGENTS/health")
    routes=$(echo "$h" | json 'd["routes"]')
    gv=$(echo "$h" | json 'd["graph_version"]')
    printf '\r   t=%6.0f/%s s  timeline %s/%s  routes %s  graph v%s   ' "$t" "$duration" "$cur" "$tot" "$routes" "$gv"
    if python3 -c "import sys; sys.exit(0 if float('$t') >= float('$duration') else 1)"; then break; fi
    [[ $playing == False && $(python3 -c "print(float('$t') > 0)") == True ]] && break
    sleep 2
  done
  echo
  sleep 3
  say "what the agents did (this run only)"
  curl -fsS "$BB/agent-events?limit=5000" -o "$RUN_DIR/agent-events.json"
  python3 - "$RUN_DIR/agent-events.json" "$run_started" <<'PY'
import collections, json, sys
ev = [e for e in json.load(open(sys.argv[1])) if e["wall_t"] >= sys.argv[2]]
kinds = collections.Counter(e["kind"] for e in ev)
agents = collections.Counter(e["agent"] for e in ev)
print("   agent events:", dict(agents))
for k, v in sorted(kinds.items()):
    print(f"   {v:5d}  {k}")
sample = next((e for e in ev if e["kind"] == "routes.recomputed"), None)
if sample:
    print("   e.g. navigator:", sample["reasoning"][:160])
PY
  chk=$(curl -fsS "$SIM/check")
  note "expected blocked edges: $(echo "$chk" | json '"OK" if d["ok"] else "MISSING " + str(d["missing"][:10])') ($(echo "$chk" | json 'len(d["severed"])') severed)"
  note "agents: $(curl -fsS "$AGENTS/health" | json '"routes=%s graph_version=%s slm_enabled=%s consensus_reports_seen=%s errors=%s" % (d["routes"], d["graph_version"], d["slm_enabled"], d["consensus_reports_seen"], d["recent_errors"])')"
fi

say "done"
note "dashboard: $url"
note "stop background processes: make demo-stop   |   stop containers: make down"
