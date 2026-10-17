#!/usr/bin/env bash
# Run a local FalkorDB inside WSL using the server bundled with the falkordblite wheel.
PORT=${PORT:-6380}
# Usage (from Windows):  wsl -d Ubuntu -u root -- bash scripts/falkordb-wsl.sh [start|stop|status]
set -euo pipefail
PKG=$(python3 -c "import redislite, os; print(os.path.dirname(redislite.__file__))")
DATA=/root/falkordb-data
mkdir -p "$DATA"
case "${1:-start}" in
  start)
    if "$PKG/bin/redis-cli" -p ${PORT:-6380} ping >/dev/null 2>&1; then echo "already running"; exit 0; fi
    "$PKG/bin/redis-server" --port ${PORT:-6380} --bind 0.0.0.0 --protected-mode no \
      --loadmodule "$PKG/bin/falkordb.so" --dir "$DATA" --save 60 1 --appendonly yes \
      --daemonize yes --logfile "$DATA/falkordb.log"
    sleep 1; "$PKG/bin/redis-cli" -p ${PORT:-6380} ping ;;
  stop)   "$PKG/bin/redis-cli" -p ${PORT:-6380} shutdown nosave || true ;;
  status) "$PKG/bin/redis-cli" -p ${PORT:-6380} ping && "$PKG/bin/redis-cli" -p ${PORT:-6380} GRAPH.LIST ;;
esac
