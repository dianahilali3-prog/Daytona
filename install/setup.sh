#!/usr/bin/env bash
# Installation complète de Daytona Studio sur une machine Linux avec GPU NVIDIA
# (RunPod, Vast.ai, Massed Compute, PC Linux…).
#
#   bash install/setup.sh                       # installe tout + télécharge tous les modèles (~150 Go)
#   FEATURES="recast parler" bash install/setup.sh   # seulement certaines fonctions
#   SKIP_MODELS=1 bash install/setup.sh         # sans télécharger les modèles
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${BASE:-$([ -d /workspace ] && echo /workspace || echo "$HOME")}"
COMFY_DIR="${COMFY_DIR:-$BASE/ComfyUI}"
VENV="${VENV:-$BASE/venv-daytona}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"

echo "==> ComfyUI : $COMFY_DIR"
if ! command -v ffmpeg >/dev/null; then
  (apt-get update && apt-get install -y ffmpeg git) || sudo apt-get install -y ffmpeg git
fi

if [ -d "$COMFY_DIR/.git" ]; then
  git -C "$COMFY_DIR" pull --ff-only
else
  git clone https://github.com/Comfy-Org/ComfyUI "$COMFY_DIR"
fi

python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -U pip wheel
pip install torch torchvision torchaudio --index-url "$TORCH_INDEX"
pip install -r "$COMFY_DIR/requirements.txt"

clone_node() {  # $1 = url
  local name; name="$(basename "$1")"
  if [ -d "$COMFY_DIR/custom_nodes/$name/.git" ]; then
    git -C "$COMFY_DIR/custom_nodes/$name" pull --ff-only || true
  else
    git clone --depth 1 "$1" "$COMFY_DIR/custom_nodes/$name"
  fi
  if [ -f "$COMFY_DIR/custom_nodes/$name/requirements.txt" ]; then
    pip install -r "$COMFY_DIR/custom_nodes/$name/requirements.txt"
  fi
}
clone_node https://github.com/Comfy-Org/ComfyUI-Manager          # gestionnaire de nœuds (interface ComfyUI)
clone_node https://github.com/filliptm/ComfyUI_Fill-ChatterBox    # voix : TTS multilingue + conversion de voix
clone_node https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite  # outils vidéo pour l'interface ComfyUI
clone_node https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler  # upscale vidéo (workflow 11)

pip install -r "$REPO_DIR/studio/requirements.txt"

# Les workflows apparaissent dans le menu Workflows de ComfyUI
mkdir -p "$COMFY_DIR/user/default/workflows/Daytona"
cp "$REPO_DIR"/workflows/*.json "$COMFY_DIR/user/default/workflows/Daytona/"

if [ "${SKIP_MODELS:-0}" != "1" ]; then
  # shellcheck disable=SC2086
  python "$REPO_DIR/install/download_models.py" --comfy "$COMFY_DIR" ${FEATURES:+--features $FEATURES}
fi

# .env : garde tes réglages (mot de passe, ntfy…) et met à jour les chemins
touch "$REPO_DIR/.env"
grep -vE '^(COMFY_DIR|VENV)=' "$REPO_DIR/.env" > "$REPO_DIR/.env.tmp" || true
{ echo "COMFY_DIR=$COMFY_DIR"; echo "VENV=$VENV"; cat "$REPO_DIR/.env.tmp"; } > "$REPO_DIR/.env"
rm -f "$REPO_DIR/.env.tmp"
grep -q '^export STUDIO_PASSWORD=' "$REPO_DIR/.env" || cat >> "$REPO_DIR/.env" <<'ENV'
# --- Accès depuis l'iPhone (modifie ces valeurs) ---
export STUDIO_PASSWORD=change-moi
# export NTFY_TOPIC=daytona-xxxxxxxx      # notifications push sur iPhone (app ntfy)
# export IDLE_STOP_MINUTES=30             # RunPod : arrête le Pod après 30 min sans activité
ENV
echo
echo "✅ Installation terminée. Lance :  bash install/start.sh"
