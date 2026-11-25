"""Client minimal pour l'API HTTP de ComfyUI (upload, file d'attente, suivi, téléchargement)."""
from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
from pathlib import Path

import requests


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url: str | None = None, timeout: float = 30):
        self.url = (url or os.environ.get("COMFY_URL", "http://127.0.0.1:8188")).rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    # ------------------------------------------------------------------ infos
    def alive(self) -> bool:
        try:
            requests.get(f"{self.url}/system_stats", timeout=5).raise_for_status()
            return True
        except Exception:
            return False

    def object_info(self, node: str | None = None) -> dict:
        path = f"/object_info/{node}" if node else "/object_info"
        r = requests.get(self.url + path, timeout=120)
        r.raise_for_status()
        return r.json()

    # ----------------------------------------------------------------- upload
    def upload(self, path: str | Path, subfolder: str = "daytona") -> str:
        """Envoie un fichier (image, vidéo, audio) dans ComfyUI/input et renvoie
        le nom à utiliser dans LoadImage / LoadVideo / LoadAudio."""
        path = Path(path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            r = requests.post(
                f"{self.url}/upload/image",
                files={"image": (path.name, fh, mime)},
                data={"subfolder": subfolder, "type": "input", "overwrite": "true"},
                timeout=600,
            )
        r.raise_for_status()
        info = r.json()
        return f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]

    # ------------------------------------------------------------------ queue
    def queue(self, prompt: dict) -> str:
        r = requests.post(
            f"{self.url}/prompt",
            json={"prompt": prompt, "client_id": self.client_id},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise ComfyError(_format_error(r))
        return r.json()["prompt_id"]

    def wait(self, prompt_id: str, poll: float = 2.0, timeout: float = 6 * 3600, on_progress=None) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            r = requests.get(f"{self.url}/history/{prompt_id}", timeout=self.timeout)
            r.raise_for_status()
            hist = r.json().get(prompt_id)
            if hist:
                status = hist.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    detail = msgs[-1][1] if msgs else status
                    raise ComfyError(f"Erreur d'exécution ComfyUI : {json.dumps(detail, ensure_ascii=False)[:2000]}")
                if status.get("completed", True):
                    return hist
            if on_progress:
                on_progress(time.time() - start)
            time.sleep(poll)
        raise ComfyError("Délai dépassé en attendant ComfyUI")

    def download_outputs(self, hist: dict, dest: str | Path, node_ids: list[str] | None = None) -> list[Path]:
        """Télécharge tous les fichiers produits (images, vidéos, audio)."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for node_id, out in hist.get("outputs", {}).items():
            if node_ids and node_id not in node_ids:
                continue
            for key, items in out.items():
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict) or "filename" not in it:
                        continue
                    if it.get("type") not in ("output", None):
                        continue  # ignore les aperçus temporaires
                    r = requests.get(
                        f"{self.url}/view",
                        params={"filename": it["filename"], "subfolder": it.get("subfolder", ""), "type": it.get("type", "output")},
                        timeout=600,
                    )
                    r.raise_for_status()
                    p = dest / it["filename"]
                    p.write_bytes(r.content)
                    files.append(p)
        return files

    def run(self, prompt: dict, dest: str | Path, node_ids: list[str] | None = None, on_progress=None) -> list[Path]:
        pid = self.queue(prompt)
        hist = self.wait(pid, on_progress=on_progress)
        return self.download_outputs(hist, dest, node_ids)


def _format_error(r: requests.Response) -> str:
    try:
        data = r.json()
    except Exception:
        return f"HTTP {r.status_code}: {r.text[:1000]}"
    lines = [data.get("error", {}).get("message", "Erreur de validation")]
    for nid, err in (data.get("node_errors") or {}).items():
        for e in err.get("errors", []):
            lines.append(f"  - nœud {nid} ({err.get('class_type')}): {e.get('message')} {e.get('details', '')}")
    return "\n".join(lines)
