"""Daytona Studio — alternative open source et illimitée à Higgsfield, propulsée par ComfyUI.

Lancement :  python studio/app.py   (ComfyUI doit tourner : COMFY_URL=http://127.0.0.1:8188)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import gradio as gr

import graphs
import media
from comfy_client import ComfyClient

ROOT = Path(__file__).resolve().parents[1]
AVATAR_DIR = Path(os.environ.get("AVATAR_DIR", ROOT / "avatar" / "daytona"))
OUT_DIR = Path(os.environ.get("OUTPUT_DIR", ROOT / "outputs"))
client = ComfyClient()
LAST_ACTIVITY = time.time()
UPLOADS = OUT_DIR / "_uploads"


# ------------------------------------------------------------------ helpers
def profile() -> dict:
    return json.loads((AVATAR_DIR / "profile.json").read_text(encoding="utf-8"))


def save_profile(p: dict):
    (AVATAR_DIR / "profile.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def ref_images() -> list[str]:
    p = profile()
    return [str(AVATAR_DIR / f) for f in p["reference_images"] if (AVATAR_DIR / f).exists()]


def voice_sample() -> str | None:
    v = AVATAR_DIR / profile().get("voice_sample", "voice.wav")
    return str(v) if v.exists() else None


def job_dir(kind: str) -> Path:
    d = OUT_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{kind}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_comfy():
    if not client.alive():
        raise gr.Error(f"ComfyUI ne répond pas sur {client.url}. Lance-le d'abord (voir README).")


def run(graph: dict, dest: Path, progress=None, label="Génération") -> list[Path]:
    def tick(elapsed):
        if progress is not None:
            progress(0.5, desc=f"{label}… {int(elapsed)} s")
    files = client.run(graph, dest, on_progress=tick)
    if not files:
        raise gr.Error("ComfyUI n'a renvoyé aucun fichier.")
    return files


def first(files: list[Path], exts: tuple[str, ...]) -> Path:
    for f in files:
        if f.suffix.lower() in exts:
            return f
    raise gr.Error(f"Aucun fichier {exts} produit")


VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv")
AUDIO_EXT = (".flac", ".wav", ".mp3", ".ogg", ".opus")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def img(path):
    """Convertit les photos iPhone (HEIC, rotation EXIF) avant de les envoyer à ComfyUI."""
    return media.normalize_image(path, UPLOADS) if path else None


def notify(msg: str):
    """Notification push sur iPhone via l'app ntfy (optionnel : NTFY_TOPIC=mon-sujet-secret)."""
    global LAST_ACTIVITY
    LAST_ACTIVITY = time.time()
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            import requests
            requests.post(f"{os.environ.get('NTFY_SERVER', 'https://ntfy.sh')}/{topic}",
                          data=msg.encode(), headers={"Title": "Daytona Studio"}, timeout=10)
        except Exception:
            pass


def finalize(video: Path, d: Path, name: str) -> str:
    """Encodage final lisible sur iPhone et enregistrable dans Photos."""
    out = media.export_iphone(video, d / f"{name}.mp4")
    notify(f"✅ {name} est prêt")
    return str(out)


def pick_ref(custom):
    if custom:
        return img(custom)
    refs = ref_images()
    if not refs:
        raise gr.Error("Ajoute au moins une photo de référence dans l'onglet Avatar.")
    return refs[0]


def daytona_voice_tts(text: str, lang: str, emotion: float, dest: Path, progress) -> Path:
    voice = voice_sample()
    voice_name = client.upload(voice) if voice else None
    files = run(graphs.tts(text, voice_name, lang, exaggeration=emotion), dest, progress, "Voix")
    return first(files, AUDIO_EXT)


def convert_to_daytona_voice(audio_path: Path, dest: Path, progress) -> Path:
    voice = voice_sample()
    if not voice:
        raise gr.Error("Ajoute un échantillon de voix de Daytona dans l'onglet Avatar.")
    files = run(graphs.voice_convert(client.upload(audio_path), client.upload(voice)), dest, progress, "Conversion de voix")
    return first(files, AUDIO_EXT)


def smooth(video: Path, dest: Path, progress) -> Path:
    files = run(graphs.interpolate(client.upload(video), 2), dest, progress, "Fluidité x2")
    return first(files, VIDEO_EXT)


# ============================================================ RECAST
def do_recast(video, mode, custom_ref, scene_prompt, action_prompt, who, quality, voice_mode,
              max_seconds, fluid, progress=gr.Progress()):
    check_comfy()
    if not video:
        raise gr.Error("Importe une vidéo de toi.")
    d = job_dir("recast")
    p = profile()
    ref = pick_ref(custom_ref)
    portrait = media.is_portrait(video)
    w, h = media.fit_size(portrait, quality)
    progress(0.05, desc="Préparation de la vidéo")
    src_frames = media.prepare_video(video, d / "probe.mp4", fps=16, max_seconds=max_seconds)
    ref_name = client.upload(ref)

    if mode.startswith("Garder"):
        padded = graphs.scail_padded_frames(src_frames)
        media.prepare_video(video, d / "driving.mp4", fps=16, max_seconds=max_seconds, pad_to_frames=padded)
        prompt = f"{p['description_en']}. {action_prompt or 'She moves and talks naturally'}."
        g = graphs.recast_replace(ref_name, client.upload(d / "driving.mp4"), padded, prompt,
                                  target_in_video=who or "person", target_in_ref="woman", width=w, height=h)
        out = first(run(g, d, progress, "Recast (SCAIL-2)"), VIDEO_EXT)
        media.trim_frames(out, d / "recast_trim.mp4", src_frames)
        out = d / "recast_trim.mp4"
    else:
        media.prepare_video(video, d / "driving.mp4", fps=16, max_seconds=max_seconds)
        appearance = (f"Character appearance description: {p['description_en']}.\n"
                      f"Background description: {scene_prompt or 'a luxury penthouse at night with city lights'}")
        g = graphs.recast_new_background(ref_name, client.upload(d / "driving.mp4"), appearance,
                                         action_prompt or "a woman talking to the camera with natural gestures",
                                         width=w, height=h)
        out = first(run(g, d, progress, "Recast (Wan-Animate-2)"), VIDEO_EXT)

    if voice_mode != "Garder ma voix":
        audio = None
        if voice_mode.startswith("Voix de") and media.extract_audio(d / "probe.mp4", d / "orig.wav"):
            audio = convert_to_daytona_voice(d / "orig.wav", d, progress)
        media.mux(out, audio, d / "recast_voice.mp4")
        out = d / "recast_voice.mp4"
    if fluid:
        out = smooth(out, d, progress)
    return finalize(out, d, "DAYTONA_recast")


# ============================================================ SPEAK
def do_speak(custom_ref, text, audio_in, convert_voice, lang, emotion, prompt, quality, fluid,
             progress=gr.Progress()):
    check_comfy()
    d = job_dir("speak")
    ref = pick_ref(custom_ref)
    if audio_in:
        media.to_wav(audio_in, d / "in.wav")
        audio = d / "in.wav"
        if convert_voice:
            audio = convert_to_daytona_voice(audio, d, progress)
    elif text and text.strip():
        audio = daytona_voice_tts(text.strip(), lang, emotion, d, progress)
    else:
        raise gr.Error("Écris un texte ou importe un audio.")
    media.to_wav(audio, d / "speech.wav")
    dur = media.audio_duration(d / "speech.wav")
    from PIL import Image
    im = Image.open(ref)
    w, h = media.fit_size(im.height >= im.width, quality)
    g = graphs.talking_video(client.upload(ref), client.upload(d / "speech.wav"), dur,
                             prompt or "A woman talking to the camera with natural expressions and subtle head movements.",
                             width=w, height=h)
    out = first(run(g, d, progress, f"Avatar parlant ({graphs.talk_segments(dur)} segments)"), VIDEO_EXT)
    if fluid:
        out = smooth(out, d, progress)
    return finalize(out, d, "DAYTONA_speak"), str(d / "speech.wav")


# ============================================================ PHOTOS
def do_photo(scene, extra_ref, prompt, n, fast, progress=gr.Progress()):
    check_comfy()
    if not prompt:
        raise gr.Error("Décris la photo voulue.")
    d = job_dir("photo")
    p = profile()
    refs = ref_images()[:2]
    scene = img(scene)
    if extra_ref:
        refs = [img(extra_ref)] + refs[:1]
    if scene:
        imgs = [scene] + refs[:2]
        instr = ("Replace the person in image 1 with the woman from image 2"
                 + (" and image 3" if len(refs) > 1 else "")
                 + ", keeping her exact face, eyes, hair, skin tone, freckles and jewelry. ")
    else:
        imgs = refs[:3]
        instr = ("Create a new photo of the woman from image 1"
                 + (" (same woman as image 2)" if len(refs) > 1 else "")
                 + ", keeping her exact face, eyes, hair, skin tone, freckles and jewelry. ")
    names = [client.upload(i) for i in imgs]
    out = []
    for i in range(int(n)):
        progress((i + 0.1) / n, desc=f"Photo {i + 1}/{int(n)}")
        files = run(graphs.photo_edit(names, instr + prompt + f". {p['style_en']}", fast=fast), d)
        out.append(str(first(files, IMAGE_EXT)))
    notify(f"✅ {len(out)} photo(s) prête(s)")
    return out


def use_as_ref(gallery_sel):
    if not gallery_sel:
        raise gr.Error("Sélectionne une image.")
    return gallery_sel


# ============================================================ ANIMER PHOTO
def do_i2v(image, prompt, seconds, quality, fluid, progress=gr.Progress()):
    check_comfy()
    d = job_dir("i2v")
    image = pick_ref(image)
    from PIL import Image
    im = Image.open(image)
    w, h = media.fit_size(im.height >= im.width, quality)
    files = run(graphs.image_to_video(client.upload(image), prompt or "She smiles and slowly turns her head, subtle camera push-in",
                                      seconds=seconds, width=w, height=h), d, progress, "Animation")
    out = first(files, VIDEO_EXT)
    if fluid:
        out = smooth(out, d, progress)
    return finalize(out, d, "DAYTONA_anim")


# ============================================================ VOIX
def do_tts(text, lang, emotion, progress=gr.Progress()):
    check_comfy()
    d = job_dir("voice")
    return str(daytona_voice_tts(text, lang, emotion, d, progress))


def do_vc(audio, progress=gr.Progress()):
    check_comfy()
    d = job_dir("voice")
    media.to_wav(audio, d / "in.wav")
    return str(convert_to_daytona_voice(d / "in.wav", d, progress))


# ============================================================ FOND PHOTO
def do_bg(image, mode, bg_image, color, prompt, progress=gr.Progress()):
    check_comfy()
    d = job_dir("background")
    image = pick_ref(image)
    if mode.startswith("Décrire"):
        if not prompt:
            raise gr.Error("Décris le nouveau décor.")
        g = graphs.photo_edit([client.upload(image)],
                              f"Change the background to {prompt}. Keep the woman exactly identical: same face, hair, pose, outfit, jewelry and lighting on her.")
        return str(first(run(g, d, progress, "Nouveau décor"), IMAGE_EXT))
    cut = first(run(graphs.remove_background(client.upload(image)), d, progress, "Détourage"), IMAGE_EXT)
    return str(media.composite(cut, d / "DAYTONA_fond.png", img(bg_image), color or "#111111"))


# ============================================================ FINITIONS
def do_finish(video, fluid, music, music_vol, progress=gr.Progress()):
    check_comfy()
    if not video:
        raise gr.Error("Importe une vidéo.")
    d = job_dir("finish")
    # Vidéo iPhone (4K/60 fps/HDR) -> 1280 px max, 30 fps max, couleurs SDR
    fps = min(30, round(media.probe(video).get("fps") or 30))
    media.prepare_video(video, d / "in.mp4", fps=fps)
    out = d / "in.mp4"
    if fluid:
        out = smooth(out, d, progress)
    if music:
        has_voice = media.probe(out)["has_audio"]
        if has_voice:
            media.extract_audio(out, d / "voice.wav")
        media.mux(out, d / "voice.wav" if has_voice else None, d / "DAYTONA_final.mp4", music=music, music_volume=music_vol)
        out = d / "DAYTONA_final.mp4"
    return finalize(out, d, "DAYTONA_finitions")


# ============================================================ AVATAR
def save_avatar(new_refs, voice, description, style):
    p = profile()
    for f in new_refs or []:
        name = f"ref_{int(time.time() * 1000)}.png"
        shutil.copy(img(f), AVATAR_DIR / name)
        p["reference_images"].append(name)
    if voice:
        media.to_wav(voice, AVATAR_DIR / "voice.wav", max_seconds=30)
        p["voice_sample"] = "voice.wav"
    if description:
        p["description_en"] = description
    if style:
        p["style_en"] = style
    save_profile(p)
    return ref_images(), voice_sample(), "✅ Avatar enregistré"


def remove_refs():
    p = profile()
    p["reference_images"] = p["reference_images"][:1]
    save_profile(p)
    return ref_images()


# ============================================================ UI
CSS = """
.gradio-container {max-width: 1200px !important}
#title h1 {font-size: 2.2rem; margin-bottom: 0}
/* iPhone : pleine largeur, gros boutons, onglets qui défilent au doigt */
@media (max-width: 640px) {
  .gradio-container {padding: 8px 10px env(safe-area-inset-bottom) !important}
  #title h1 {font-size: 1.6rem}
  [role=tab] {font-size: 15px !important; padding: 0 10px !important}
  button.primary, button.lg {min-height: 52px !important; font-size: 17px !important}
  textarea, input {font-size: 16px !important}  /* évite le zoom automatique de Safari */
}
"""

HEAD = """
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Daytona">
<meta name="theme-color" content="#b91c1c">
<link rel="apple-touch-icon" href="/gradio_api/file=__ICON__">
"""


def creations(limit: int = 60):
    """Toutes les créations finales (elles restent là même si l'iPhone s'est verrouillé pendant le calcul)."""
    files = [f for f in OUT_DIR.glob("*/*") if f.name.startswith(("DAYTONA_", "photo_", "voice_", "voice_vc_"))
             or (f.parent.name.endswith("_photo") and f.suffix == ".png")]
    files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    media_files = [str(f) for f in files if f.suffix.lower() in VIDEO_EXT + IMAGE_EXT]
    return media_files, [str(f) for f in files]


def idle_watchdog():
    """Arrête automatiquement le Pod RunPod après X minutes sans activité (économise ton argent).
    Activé seulement si IDLE_STOP_MINUTES est défini et qu'on tourne sur RunPod."""
    minutes = float(os.environ.get("IDLE_STOP_MINUTES", "0") or 0)
    pod = os.environ.get("RUNPOD_POD_ID")
    if not minutes or not pod:
        return
    while True:
        time.sleep(60)
        busy = False
        try:
            import requests
            q = requests.get(f"{client.url}/queue", timeout=10).json()
            busy = bool(q.get("queue_running") or q.get("queue_pending"))
        except Exception:
            pass
        if busy:
            touch()
        elif time.time() - LAST_ACTIVITY > minutes * 60:
            notify(f"⏸️ Pod arrêté après {int(minutes)} min d'inactivité")
            subprocess.run(["runpodctl", "stop", "pod", pod])
            return


def touch(*_):
    global LAST_ACTIVITY
    LAST_ACTIVITY = time.time()


def build_ui() -> gr.Blocks:
    p = profile()
    with gr.Blocks(title="Daytona Studio") as ui:
        gr.Markdown(f"# 🎬 {p['name']} Studio\nTon Higgsfield open source : vidéos IA illimitées avec ComfyUI, sur ton propre GPU.",
                    elem_id="title")
        status = "🟢 ComfyUI connecté" if client.alive() else f"🔴 ComfyUI introuvable sur {client.url}"
        gr.Markdown(status)

        with gr.Tabs(overflow_behavior="wrap"):
            with gr.Tab("🔁 Recast"):
                gr.Markdown("Filme-toi (face caméra, danse, story…). L'IA remplace ton corps et ton visage par Daytona "
                            "en gardant tes mouvements, tes expressions et ton lip-sync.")
                with gr.Row():
                    with gr.Column():
                        rc_video = gr.Video(label="Ta vidéo")
                        rc_mode = gr.Radio(["Garder mon décor (SCAIL-2)", "Nouveau décor généré (Wan-Animate-2)"],
                                           value="Garder mon décor (SCAIL-2)", label="Décor")
                        rc_scene = gr.Textbox(label="Nouveau décor (en anglais de préférence)",
                                              placeholder="a rooftop in Dubai at sunset, golden hour light")
                        rc_action = gr.Textbox(label="Ce qui se passe dans la vidéo (optionnel)",
                                               placeholder="she talks to the camera and laughs")
                        rc_voice = gr.Radio(["Garder ma voix", "Voix de Daytona (conversion)", "Couper le son"],
                                            value="Voix de Daytona (conversion)", label="Voix")
                        with gr.Accordion("Options avancées", open=False):
                            rc_ref = gr.Image(label="Tenue / look spécifique (sinon photo de référence principale)", type="filepath")
                            rc_who = gr.Textbox(value="person", label="Qui remplacer dans la vidéo (SAM3, en anglais)")
                            rc_quality = gr.Radio(["480p", "720p"], value="480p", label="Qualité")
                            rc_max = gr.Slider(3, 120, value=30, step=1, label="Durée max (s)")
                            rc_fluid = gr.Checkbox(value=True, label="Fluidité x2 (32 fps)")
                        rc_btn = gr.Button("🚀 Générer", variant="primary")
                    rc_out = gr.Video(label="Résultat")
                rc_btn.click(do_recast, [rc_video, rc_mode, rc_ref, rc_scene, rc_action, rc_who, rc_quality, rc_voice,
                                         rc_max, rc_fluid], rc_out)

            with gr.Tab("🗣️ Parler"):
                gr.Markdown("Daytona dit ton texte avec sa voix, lip-sync parfait, durée illimitée.")
                with gr.Row():
                    with gr.Column():
                        sp_ref = gr.Image(label="Photo (sinon photo de référence principale)", type="filepath")
                        sp_text = gr.Textbox(lines=5, label="Texte à dire", placeholder="Salut tout le monde ! Aujourd'hui je vous emmène…")
                        sp_audio = gr.Audio(label="…ou un audio (ta voix)", type="filepath")
                        sp_conv = gr.Checkbox(value=True, label="Convertir l'audio importé en voix de Daytona")
                        with gr.Row():
                            sp_lang = gr.Dropdown(list(graphs.LANGUAGES), value=p.get("language", "fr"), label="Langue")
                            sp_emo = gr.Slider(0.25, 1.5, value=0.6, step=0.05, label="Émotion / expressivité")
                        with gr.Accordion("Options avancées", open=False):
                            sp_prompt = gr.Textbox(label="Description de la scène (anglais)",
                                                   placeholder="A woman talking to the camera, natural hand gestures")
                            sp_quality = gr.Radio(["480p", "720p"], value="480p", label="Qualité")
                            sp_fluid = gr.Checkbox(value=False, label="Fluidité x2 (50 fps)")
                        sp_btn = gr.Button("🚀 Générer", variant="primary")
                    with gr.Column():
                        sp_out = gr.Video(label="Résultat")
                        sp_wav = gr.Audio(label="Voix générée")
                sp_btn.click(do_speak, [sp_ref, sp_text, sp_audio, sp_conv, sp_lang, sp_emo, sp_prompt, sp_quality, sp_fluid],
                             [sp_out, sp_wav])

            with gr.Tab("📸 Photos"):
                gr.Markdown("Nouvelles photos de Daytona, toujours avec le même visage. Ajoute une photo de scène/pose "
                            "(ex. une photo d'inspiration Pinterest) pour qu'elle prenne la place de la personne.")
                with gr.Row():
                    with gr.Column():
                        ph_scene = gr.Image(label="Scène / pose à reproduire (optionnel)", type="filepath")
                        ph_extra = gr.Image(label="Référence de tenue/visage en plus (optionnel)", type="filepath")
                        ph_prompt = gr.Textbox(lines=3, label="Décris la photo (anglais conseillé)",
                                               placeholder="sitting at a café terrace in Paris, white summer dress, morning sun, iPhone photo")
                        with gr.Row():
                            ph_n = gr.Slider(1, 8, value=4, step=1, label="Nombre")
                            ph_fast = gr.Checkbox(value=True, label="Rapide (4 étapes)")
                        ph_btn = gr.Button("🚀 Générer", variant="primary")
                    ph_out = gr.Gallery(label="Résultats", columns=2, height=700)
                ph_btn.click(do_photo, [ph_scene, ph_extra, ph_prompt, ph_n, ph_fast], ph_out)

            with gr.Tab("🎬 Animer"):
                with gr.Row():
                    with gr.Column():
                        iv_img = gr.Image(label="Photo (sinon photo de référence)", type="filepath")
                        iv_prompt = gr.Textbox(lines=3, label="Mouvement / caméra (anglais)",
                                               placeholder="she flips her hair and smiles, slow orbit camera, cinematic")
                        iv_sec = gr.Slider(2, 10, value=5, step=1, label="Durée (s)")
                        iv_quality = gr.Radio(["480p", "720p"], value="480p", label="Qualité")
                        iv_fluid = gr.Checkbox(value=True, label="Fluidité x2")
                        iv_btn = gr.Button("🚀 Générer", variant="primary")
                    iv_out = gr.Video(label="Résultat")
                iv_btn.click(do_i2v, [iv_img, iv_prompt, iv_sec, iv_quality, iv_fluid], iv_out)

            with gr.Tab("🖼️ Fond"):
                with gr.Row():
                    with gr.Column():
                        bg_img = gr.Image(label="Photo (sinon photo de référence)", type="filepath")
                        bg_mode = gr.Radio(["Décrire le nouveau décor (IA)", "Détourer + image/couleur de fond"],
                                           value="Décrire le nouveau décor (IA)", label="Méthode")
                        bg_prompt = gr.Textbox(label="Nouveau décor", placeholder="a luxury yacht deck in Monaco, blue sea")
                        bg_bg = gr.Image(label="Image de fond", type="filepath")
                        bg_color = gr.ColorPicker(value="#111111", label="…ou couleur unie")
                        bg_btn = gr.Button("🚀 Générer", variant="primary")
                    bg_out = gr.Image(label="Résultat")
                bg_btn.click(do_bg, [bg_img, bg_mode, bg_bg, bg_color, bg_prompt], bg_out)
                gr.Markdown("💡 Pour une **vidéo** avec un autre décor : onglet *Recast* → *Nouveau décor généré*.")

            with gr.Tab("🎙️ Voix"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### Texte → voix de Daytona")
                        v_text = gr.Textbox(lines=4, label="Texte")
                        with gr.Row():
                            v_lang = gr.Dropdown(list(graphs.LANGUAGES), value="fr", label="Langue")
                            v_emo = gr.Slider(0.25, 1.5, value=0.6, step=0.05, label="Émotion")
                        v_btn = gr.Button("Générer la voix", variant="primary")
                        v_out = gr.Audio(label="Résultat")
                    with gr.Column():
                        gr.Markdown("### Ta voix → voix de Daytona")
                        vc_in = gr.Audio(label="Ton enregistrement", type="filepath")
                        vc_btn = gr.Button("Convertir", variant="primary")
                        vc_out = gr.Audio(label="Résultat")
                v_btn.click(do_tts, [v_text, v_lang, v_emo], v_out)
                vc_btn.click(do_vc, [vc_in], vc_out)

            with gr.Tab("✨ Finitions"):
                with gr.Row():
                    with gr.Column():
                        f_video = gr.Video(label="Vidéo")
                        f_fluid = gr.Checkbox(value=True, label="Fluidité x2")
                        f_music = gr.Audio(label="Musique de fond (optionnel)", type="filepath")
                        f_vol = gr.Slider(0, 1, value=0.15, step=0.05, label="Volume musique")
                        f_btn = gr.Button("Appliquer", variant="primary")
                    f_out = gr.Video(label="Résultat")
                f_btn.click(do_finish, [f_video, f_fluid, f_music, f_vol], f_out)

            with gr.Tab("👤 Avatar"):
                gr.Markdown("Photos de référence (visage net, plusieurs angles = meilleure ressemblance) et "
                            "échantillon de voix (10 à 30 s, voix claire, sans musique).")
                with gr.Row():
                    with gr.Column():
                        a_gal = gr.Gallery(value=ref_images(), label="Références actuelles", columns=3)
                        a_new = gr.File(file_count="multiple", file_types=["image"], label="Ajouter des photos")
                        a_reset = gr.Button("Garder seulement la 1re référence")
                    with gr.Column():
                        a_voice_now = gr.Audio(value=voice_sample(), label="Voix actuelle", interactive=False)
                        a_voice = gr.Audio(label="Nouvel échantillon de voix", type="filepath")
                        a_desc = gr.Textbox(value=p["description_en"], lines=4, label="Description physique (anglais)")
                        a_style = gr.Textbox(value=p["style_en"], lines=2, label="Style photo")
                        a_save = gr.Button("💾 Enregistrer", variant="primary")
                        a_msg = gr.Markdown()
                a_save.click(save_avatar, [a_new, a_voice, a_desc, a_style], [a_gal, a_voice_now, a_msg])
                a_reset.click(remove_refs, None, a_gal)

            with gr.Tab("📂 Créations"):
                gr.Markdown("Tout ce que tu as généré, même si ton iPhone s'est verrouillé pendant le calcul. "
                            "Sur iPhone : ouvre une vidéo → bouton télécharger → *Enregistrer la vidéo* pour l'avoir dans Photos.")
                c_btn = gr.Button("🔄 Actualiser", variant="primary")
                c_gal = gr.Gallery(label="Créations", columns=2, height="auto", object_fit="contain")
                c_files = gr.File(label="Fichiers (téléchargement)", file_count="multiple")
                c_btn.click(creations, None, [c_gal, c_files])
                ui.load(creations, None, [c_gal, c_files])
        ui.load(touch)
    return ui


if __name__ == "__main__":
    password = os.environ.get("STUDIO_PASSWORD")
    if password == "change-moi":
        raise SystemExit("❌ Change STUDIO_PASSWORD dans le fichier .env avant de lancer le studio.")
    if not password:
        print("⚠️  STUDIO_PASSWORD non défini : le studio est accessible sans mot de passe. "
              "Définis-le avant de l'ouvrir depuis ton iPhone / Internet.")
    icon = str(ref_images()[0]) if ref_images() else None
    threading.Thread(target=idle_watchdog, daemon=True).start()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_ui().queue(default_concurrency_limit=1).launch(
        server_name=os.environ.get("HOST", "0.0.0.0"),
        server_port=int(os.environ.get("PORT", 7860)),
        share=os.environ.get("GRADIO_SHARE", "0") == "1",
        auth=(os.environ.get("STUDIO_USER", "daytona"), password) if password else None,
        auth_message="Daytona Studio",
        allowed_paths=[str(OUT_DIR), str(AVATAR_DIR)],
        theme=gr.themes.Soft(primary_hue="red", neutral_hue="zinc"),
        css=CSS,
        head=HEAD.replace("__ICON__", icon or ""),
        favicon_path=icon,
        pwa=True,
        max_file_size="4gb",  # vidéos 4K de l'iPhone
    )
