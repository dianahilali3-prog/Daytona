"""Outils ffmpeg / PIL : préparation des vidéos, audio, montage, composition de fond."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image


def _run(cmd: list[str]):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué : {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def probe(path: str | Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    data = json.loads(r.stdout)
    info = {"duration": float(data["format"].get("duration", 0)), "has_audio": False}
    for s in data["streams"]:
        if s["codec_type"] == "video" and "width" not in info:
            num, den = (s.get("r_frame_rate") or "0/1").split("/")
            info.update(width=int(s["width"]), height=int(s["height"]),
                        fps=float(num) / float(den or 1) if float(den or 1) else 0)
            rot = int((s.get("tags") or {}).get("rotate", 0) or 0)
            for sd in s.get("side_data_list", []) or []:
                rot = int(sd.get("rotation", rot) or rot)
            if abs(rot) in (90, 270):
                info["width"], info["height"] = info["height"], info["width"]
        elif s["codec_type"] == "audio":
            info["has_audio"] = True
    return info


def is_portrait(path: str | Path) -> bool:
    i = probe(path)
    return i.get("height", 1) >= i.get("width", 1)


def prepare_video(src: str | Path, dst: str | Path, fps: int = 16, max_seconds: float | None = None,
                  pad_to_frames: int | None = None, max_side: int = 1280) -> int:
    """Ré-encode à fps fixe (Wan = 16 fps), limite la durée, et peut prolonger la
    dernière image pour atteindre un nombre d'images exact. Renvoie le nb d'images d'origine."""
    info = probe(src)
    dur = info["duration"] if not max_seconds else min(info["duration"], max_seconds)
    frames = max(1, int(dur * fps))
    vf = [f"fps={fps}", f"scale='min({max_side},iw)':-2"]
    if pad_to_frames and pad_to_frames > frames:
        vf.append(f"tpad=stop_mode=clone:stop={pad_to_frames - frames}")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-t", f"{dur:.3f}", "-i", str(src), "-vf", ",".join(vf)]
    if pad_to_frames:
        cmd += ["-frames:v", str(max(pad_to_frames, frames))]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-c:a", "aac", "-b:a", "192k", str(dst)]
    _run(cmd)
    return frames


def trim_frames(src: str | Path, dst: str | Path, frames: int):
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-frames:v", str(frames),
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-c:a", "copy", "-shortest", str(dst)])


def extract_audio(src: str | Path, dst: str | Path, max_seconds: float | None = None) -> bool:
    if not probe(src)["has_audio"]:
        return False
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.3f}"]
    _run(cmd + ["-vn", "-ac", "1", "-ar", "24000", str(dst)])
    return True


def mux(video: str | Path, audio: str | Path | None, dst: str | Path, music: str | Path | None = None,
        music_volume: float = 0.15):
    """Remplace la piste audio de la vidéo (voix + musique de fond optionnelle)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    if audio:
        cmd += ["-i", str(audio)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    if audio and music:
        cmd += ["-filter_complex", f"[2:a]volume={music_volume}[m];[1:a][m]amix=inputs=2:duration=first[a]",
                "-map", "0:v", "-map", "[a]"]
    elif audio:
        cmd += ["-map", "0:v", "-map", "1:a"]
    elif music:
        cmd += ["-filter_complex", f"[1:a]volume={music_volume}[a]", "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v"]
    _run(cmd + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(dst)])


def audio_duration(path: str | Path) -> float:
    return probe(path)["duration"]


def to_wav(src: str | Path, dst: str | Path, max_seconds: float | None = None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.3f}"]
    _run(cmd + ["-vn", "-ac", "1", "-ar", "24000", str(dst)])


def composite(cutout_png: str | Path, dst: str | Path, background: str | Path | None = None,
              color: str = "#111111") -> Path:
    """Pose le sujet détouré sur une image de fond (recadrée en « cover ») ou une couleur."""
    fg = Image.open(cutout_png).convert("RGBA")
    if background:
        bg = Image.open(background).convert("RGBA")
        scale = max(fg.width / bg.width, fg.height / bg.height)
        bg = bg.resize((round(bg.width * scale), round(bg.height * scale)), Image.LANCZOS)
        left, top = (bg.width - fg.width) // 2, (bg.height - fg.height) // 2
        bg = bg.crop((left, top, left + fg.width, top + fg.height))
    else:
        bg = Image.new("RGBA", fg.size, color)
    out = Image.alpha_composite(bg, fg).convert("RGB")
    out.save(dst)
    return Path(dst)


def fit_size(portrait: bool, quality: str) -> tuple[int, int]:
    """Résolutions compatibles Wan (multiples de 32)."""
    w, h = {"480p": (480, 832), "720p": (704, 1280)}[quality]
    return (w, h) if portrait else (h, w)
