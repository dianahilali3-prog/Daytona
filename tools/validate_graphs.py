"""Vérifie que tous les graphes du studio sont acceptés par un serveur ComfyUI.

Usage : python tools/validate_graphs.py --comfy http://127.0.0.1:8188 [--placeholders /chemin/ComfyUI/models]

Avec --placeholders, crée des fichiers modèles vides pour que la validation passe
sur une machine sans les vrais poids (ex. CI / CPU). Les jobs acceptés sont
immédiatement annulés.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "studio"))
sys.path.insert(0, str(ROOT / "install"))

import graphs  # noqa: E402
from comfy_client import ComfyClient, ComfyError  # noqa: E402


def make_placeholders(models_dir: Path):
    manifest = json.loads((ROOT / "install" / "models.json").read_text())
    for feat in manifest["features"].values():
        for m in feat["models"]:
            p = models_dir / m["dir"] / m["file"]
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_bytes(b"")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--placeholders", type=Path)
    a = ap.parse_args()
    if a.placeholders:
        make_placeholders(a.placeholders)
    c = ComfyClient(a.comfy)

    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=16",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-shortest", str(tmp / "v.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300", "-t", "3",
                    str(tmp / "a.wav")], check=True)
    img = c.upload(ROOT / "avatar" / "daytona" / "daytona_01.png")
    vid = c.upload(tmp / "v.mp4")
    aud = c.upload(tmp / "a.wav")

    cases = {
        "photo_edit (3 images)": graphs.photo_edit([img, img, img], "test"),
        "photo_edit (qualité)": graphs.photo_edit([img], "test", fast=False),
        "image_to_video": graphs.image_to_video(img, "test"),
        "tts": graphs.tts("Bonjour", aud),
        "tts (voix par défaut)": graphs.tts("Bonjour", None),
        "voice_convert": graphs.voice_convert(aud, aud),
        "talking_video (1 segment)": graphs.talking_video(img, aud, 3, "test"),
        "talking_video (4 segments)": graphs.talking_video(img, aud, 12, "test"),
        "recast_replace (1 segment)": graphs.recast_replace(img, vid, 81, "test"),
        "recast_replace (2 segments)": graphs.recast_replace(img, vid, 150, "test"),
        "recast_replace (5 segments)": graphs.recast_replace(img, vid, 380, "test", replace_mode=False),
        "recast_new_background": graphs.recast_new_background(img, vid, "a", "b"),
        "remove_background": graphs.remove_background(img),
        "interpolate": graphs.interpolate(vid, 2),
    }
    ok = True
    for name, g in cases.items():
        try:
            c.queue(g)
            print(f"OK    {name}  ({len(g)} nœuds)")
        except ComfyError as e:
            ok = False
            print(f"ECHEC {name}\n{e}")
        finally:
            requests.post(f"{c.url}/queue", json={"clear": True})
            requests.post(f"{c.url}/interrupt")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
