#!/usr/bin/env bash
# Démarre ComfyUI (port 8188) puis Daytona Studio (port 7860).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"
COMFY_DIR="${COMFY_DIR:?Lance d abord install/setup.sh}"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

if ! curl -s http://127.0.0.1:8188/system_stats >/dev/null; then
  echo "==> Démarrage de ComfyUI…"
  (cd "$COMFY_DIR" && nohup python main.py --listen 0.0.0.0 --port 8188 ${COMFY_ARGS:-} > "$REPO_DIR/comfyui.log" 2>&1 &)
  for _ in $(seq 1 120); do curl -s http://127.0.0.1:8188/system_stats >/dev/null && break; sleep 2; done
fi
echo "==> ComfyUI : http://localhost:8188  (logs : comfyui.log)"
echo "==> Daytona Studio : http://localhost:7860"
cd "$REPO_DIR/studio" && COMFY_URL=http://127.0.0.1:8188 python app.py
