#!/usr/bin/env bash
# Verify the compose infra is healthy: Postgres answers, PostGIS + pgRouting are
# installed (after migrations), Redis answers PING. Exit non-zero on any failure.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] && set -a && source .env && set +a

PG_USER="${POSTGRES_USER:-arco}"
PG_DB="${POSTGRES_DB:-arco}"
COMPOSE="${COMPOSE:-docker compose}"
fail=0

status() { $COMPOSE ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk -v s="$1" '$1==s {print $2}'; }

echo "== container health =="
for svc in postgres redis; do
  # containers may still be starting right after `docker compose up -d`
  for _ in $(seq 1 30); do
    h="$(status "$svc")"
    [[ "$h" == "healthy" ]] && break
    sleep 2
  done
  printf '  %-10s %s\n' "$svc" "${h:-not running}"
  [[ "$h" == "healthy" ]] || fail=1
done

echo "== blackboard-migrate (one-shot) =="
mig_state=""
for _ in $(seq 1 60); do
  mig_state="$($COMPOSE ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' 2>/dev/null \
    | awk '$1=="blackboard-migrate" {print $2" "$3}')"
  [[ "$mig_state" == exited* ]] && break
  sleep 2
done
printf '  %s\n' "${mig_state:-not run (start it with: docker compose up blackboard-migrate)}"
[[ "$mig_state" == "exited 0" ]] || fail=1

echo "== postgres extensions =="
if ext="$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -At -c \
  "SELECT extname||' '||extversion FROM pg_extension WHERE extname IN ('postgis','pgrouting') ORDER BY 1" 2>/dev/null)"; then
  if [[ -z "$ext" ]]; then
    echo "  (none yet - run 'make migrate' or 'docker compose up blackboard-migrate')"
    fail=1
  else
    echo "$ext" | sed 's/^/  /'
    echo "$ext" | grep -q '^postgis '   || { echo "  postgis missing";   fail=1; }
    echo "$ext" | grep -q '^pgrouting ' || { echo "  pgrouting missing"; fail=1; }
  fi
else
  echo "  psql failed"; fail=1
fi

echo "== redis =="
if pong="$($COMPOSE exec -T redis redis-cli ping 2>/dev/null)" && [[ "$pong" == "PONG" ]]; then
  echo "  PONG"
else
  echo "  no PONG"; fail=1
fi

echo "== blackboard api =="
bb_port="${BLACKBOARD_PORT:-8001}"
if [[ "$(status blackboard)" == "" ]]; then
  echo "  not running (optional in Phase 1; start with: docker compose up -d blackboard)"
else
  for _ in $(seq 1 30); do
    [[ "$(status blackboard)" == "healthy" ]] && break
    sleep 2
  done
  if body="$(curl -fsS "http://localhost:${bb_port}/health" 2>/dev/null)"; then
    echo "  $body"
    echo "$body" | grep -q '"status":"ok"' || { echo "  status not ok"; fail=1; }
  else
    echo "  GET /health failed on port ${bb_port}"; fail=1
  fi
fi

echo "== base-station api =="
bs_port="${BASE_STATION_PORT:-8002}"
if [[ "$(status base-station)" == "" ]]; then
  echo "  not running (optional; start with: docker compose up -d base-station)"
else
  for _ in $(seq 1 30); do
    [[ "$(status base-station)" == "healthy" ]] && break
    sleep 2
  done
  if body="$(curl -fsS "http://localhost:${bs_port}/health" 2>/dev/null)"; then
    echo "  $body"
    echo "$body" | grep -q '"status":"ok"' || { echo "  status not ok"; fail=1; }
  else
    echo "  GET /health failed on port ${bs_port}"; fail=1
  fi
fi

echo "== agents api =="
ag_port="${AGENTS_PORT:-8003}"
if [[ "$(status agents)" == "" ]]; then
  echo "  not running (optional; start with: docker compose up -d agents)"
else
  for _ in $(seq 1 30); do
    [[ "$(status agents)" == "healthy" ]] && break
    sleep 2
  done
  if body="$(curl -fsS "http://localhost:${ag_port}/health" 2>/dev/null)"; then
    echo "  $body"
    echo "$body" | grep -q '"status":"ok"' || { echo "  status not ok"; fail=1; }
  else
    echo "  GET /health failed on port ${ag_port}"; fail=1
  fi
fi

if (( fail )); then echo "healthcheck: FAIL"; exit 1; fi
echo "healthcheck: OK"
