"""Télécharge les modèles nécessaires dans ComfyUI/models.

  python install/download_models.py --comfy /workspace/ComfyUI                   # tout
  python install/download_models.py --comfy ... --features recast parler        # seulement certaines fonctions
  python install/download_models.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

MANIFEST = Path(__file__).with_name("models.json")


def resolve(features: list[str], manifest: dict) -> list[str]:
    out: list[str] = []
    def add(f):
        for dep in manifest["features"][f].get("requires", []):
            add(dep)
        if f not in out:
            out.append(f)
    for f in features:
        if f not in manifest["features"]:
            sys.exit(f"Fonction inconnue : {f}. Choix : {', '.join(manifest['features'])}")
        add(f)
    return out


def download(url: str, dst: Path):
    tmp = dst.with_suffix(dst.suffix + ".part")
    headers = {}
    if os.environ.get("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    pos = tmp.stat().st_size if tmp.exists() else 0
    if pos:
        headers["Range"] = f"bytes={pos}-"
    with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
        if r.status_code == 416:  # déjà complet
            tmp.rename(dst)
            return
        r.raise_for_status()
        mode = "ab" if pos and r.status_code == 206 else "wb"
        total = int(r.headers.get("content-length", 0)) + (pos if mode == "ab" else 0)
        done = pos if mode == "ab" else 0
        with open(tmp, mode) as fh:
            for chunk in r.iter_content(chunk_size=8 << 20):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r   {done / 1e9:6.2f} / {total / 1e9:.2f} Go", end="", flush=True)
    print()
    tmp.rename(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", type=Path, default=Path(os.environ.get("COMFY_DIR", "ComfyUI")))
    ap.add_argument("--features", nargs="*", help="recast recast_fond parler photos animer_photo fond fluidite")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    manifest = json.loads(MANIFEST.read_text())

    if a.list:
        for k, f in manifest["features"].items():
            size = sum(m["gb"] for m in f["models"])
            print(f"{k:14} {size:6.1f} Go  {f['label']}")
        return

    feats = resolve(a.features or list(manifest["features"]), manifest)
    models_dir = a.comfy / "models"
    if not models_dir.exists():
        sys.exit(f"Dossier introuvable : {models_dir} (utilise --comfy)")
    todo = {(m["dir"], m["file"]): m for f in feats for m in manifest["features"][f]["models"]}
    print(f"Fonctions : {', '.join(feats)} — {sum(m['gb'] for m in todo.values()):.1f} Go au total")
    for (d, name), m in todo.items():
        dst = models_dir / d / name
        if dst.exists() and dst.stat().st_size > 1_000_000:
            print(f"✔ {d}/{name}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        print(f"⬇ {d}/{name} (~{m['gb']} Go)")
        download(m["url"], dst)
    print("Terminé. (Les modèles de voix Chatterbox se téléchargent tout seuls au premier usage.)")


if __name__ == "__main__":
    main()
