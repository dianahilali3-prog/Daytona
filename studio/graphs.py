"""Construction des graphes ComfyUI (format API) pour chaque fonction du studio.

Tous les noms de nœuds / entrées proviennent des workflows officiels Comfy-Org
(dossier ../workflows) et ont été validés contre un serveur ComfyUI réel
(voir tools/validate_graphs.py).
"""
from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"

# --------------------------------------------------------------------- modèles
WAN_T5 = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "Wan2_1_VAE_bf16.safetensors"
CLIP_VISION_H = "clip_vision_h.safetensors"
LIGHTX2V_I2V = "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
NEG_WAN = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，"
    "丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，"
    "杂乱的背景，三条腿，背景人很多，倒着走"
)


def rand_seed() -> int:
    return random.randint(1, 2**31 - 1)


class Graph:
    """Petit utilitaire pour écrire un graphe au format API."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self._n = 0

    def add(self, class_type: str, **inputs) -> str:
        self._n += 1
        nid = str(self._n)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    @staticmethod
    def out(nid: str, idx: int = 0) -> list:
        return [nid, idx]


def _save_video(g: Graph, video_ref, prefix: str) -> str:
    return g.add("SaveVideo", video=video_ref, filename_prefix=prefix, format="auto", codec="auto")


def prune(graph: dict, keep_outputs: list[str]) -> dict:
    """Supprime tous les nœuds qui ne servent pas aux sorties demandées."""
    needed: set[str] = set()
    stack = list(keep_outputs)
    while stack:
        nid = stack.pop()
        if nid in needed:
            continue
        needed.add(nid)
        for v in graph[nid]["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in graph:
                stack.append(v[0])
    return {k: v for k, v in graph.items() if k in needed}


def _load_template(name: str) -> dict:
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


# ============================================================ 1. PHOTOS (Soul)
def photo_edit(images: list[str], prompt: str, seed: int | None = None, fast: bool = True,
               prefix: str = "daytona/photo") -> dict:
    """Qwen-Image-Edit-2511 : jusqu'à 3 images (image 1 = base/cadre, 2-3 = références).
    Ex : « La femme de l'image 2 prend la pose de l'image 1, dans un café parisien »."""
    if not 1 <= len(images) <= 3:
        raise ValueError("1 à 3 images")
    g = Graph()
    unet = g.add("UNETLoader", unet_name="qwen_image_edit_2511_fp8mixed.safetensors", weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default")
    vae = g.add("VAELoader", vae_name="qwen_image_vae.safetensors")
    model = g.add("ModelSamplingAuraFlow", model=[unet, 0], shift=3.1)
    model = g.add("CFGNorm", model=[model, 0], strength=1.0)
    if fast:
        model = g.add("LoraLoaderModelOnly", model=[model, 0], strength_model=1.0,
                      lora_name="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors")
    loads = [g.add("LoadImage", image=img) for img in images]
    base = g.add("FluxKontextImageScale", image=[loads[0], 0])
    img_inputs = {"image1": [base, 0]}
    for i, nid in enumerate(loads[1:], start=2):
        img_inputs[f"image{i}"] = [nid, 0]
    pos = g.add("TextEncodeQwenImageEditPlus", clip=[clip, 0], vae=[vae, 0], prompt=prompt, **img_inputs)
    neg = g.add("TextEncodeQwenImageEditPlus", clip=[clip, 0], vae=[vae, 0], prompt="", **img_inputs)
    pos = g.add("FluxKontextMultiReferenceLatentMethod", conditioning=[pos, 0], reference_latents_method="index_timestep_zero")
    neg = g.add("FluxKontextMultiReferenceLatentMethod", conditioning=[neg, 0], reference_latents_method="index_timestep_zero")
    latent = g.add("VAEEncode", pixels=[base, 0], vae=[vae, 0])
    ks = g.add("KSampler", model=[model, 0], positive=[pos, 0], negative=[neg, 0], latent_image=[latent, 0],
               seed=seed or rand_seed(), steps=4 if fast else 40, cfg=1.0 if fast else 4.0,
               sampler_name="euler", scheduler="simple", denoise=1.0)
    dec = g.add("VAEDecode", samples=[ks, 0], vae=[vae, 0])
    g.add("SaveImage", images=[dec, 0], filename_prefix=prefix)
    return g.nodes


# ======================================================= 2. PHOTO -> VIDÉO (DoP)
def image_to_video(image: str, prompt: str, seconds: float = 5, width: int = 480, height: int = 832,
                   fps: int = 16, seed: int | None = None, fast: bool = True,
                   negative: str = NEG_WAN, prefix: str = "daytona/i2v") -> dict:
    """Wan 2.2 I2V 14B (high + low noise) avec LoRA Lightx2v 4 étapes."""
    g = Graph()
    length = int(seconds * fps) // 4 * 4 + 1
    clip = g.add("CLIPLoader", clip_name=WAN_T5, type="wan", device="default")
    vae = g.add("VAELoader", vae_name=WAN_VAE)
    hi = g.add("UNETLoader", unet_name="wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", weight_dtype="default")
    lo = g.add("UNETLoader", unet_name="wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", weight_dtype="default")
    if fast:
        hi = g.add("LoraLoaderModelOnly", model=[hi, 0], strength_model=1.0,
                   lora_name="wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors")
        lo = g.add("LoraLoaderModelOnly", model=[lo, 0], strength_model=1.0,
                   lora_name="wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors")
    hi = g.add("ModelSamplingSD3", model=[hi, 0], shift=5.0)
    lo = g.add("ModelSamplingSD3", model=[lo, 0], shift=5.0)
    pos = g.add("CLIPTextEncode", clip=[clip, 0], text=prompt)
    neg = g.add("CLIPTextEncode", clip=[clip, 0], text=negative)
    img = g.add("LoadImage", image=image)
    i2v = g.add("WanImageToVideo", positive=[pos, 0], negative=[neg, 0], vae=[vae, 0], start_image=[img, 0],
                width=width, height=height, length=length, batch_size=1)
    steps, split, cfg = (4, 2, 1.0) if fast else (20, 10, 3.5)
    s = seed or rand_seed()
    k1 = g.add("KSamplerAdvanced", model=[hi, 0], positive=[i2v, 0], negative=[i2v, 1], latent_image=[i2v, 2],
               add_noise="enable", noise_seed=s, steps=steps, cfg=cfg, sampler_name="euler", scheduler="simple",
               start_at_step=0, end_at_step=split, return_with_leftover_noise="enable")
    k2 = g.add("KSamplerAdvanced", model=[lo, 0], positive=[i2v, 0], negative=[i2v, 1], latent_image=[k1, 0],
               add_noise="disable", noise_seed=0, steps=steps, cfg=cfg, sampler_name="euler", scheduler="simple",
               start_at_step=split, end_at_step=steps, return_with_leftover_noise="disable")
    dec = g.add("VAEDecode", samples=[k2, 0], vae=[vae, 0])
    vid = g.add("CreateVideo", images=[dec, 0], fps=float(fps))
    _save_video(g, [vid, 0], prefix)
    return g.nodes


# ================================================================== 3. VOIX
LANGUAGES = {
    "fr": "French (fr)", "en": "English (en)", "es": "Spanish (es)", "it": "Italian (it)",
    "de": "German (de)", "ar": "Arabic (ar)", "pt": "Portuguese (pt)",
}


def tts(text: str, voice: str | None, language: str = "fr", exaggeration: float = 0.5,
        cfg_weight: float = 0.5, temperature: float = 0.8, seed: int | None = None,
        prefix: str = "daytona/voice") -> dict:
    """Chatterbox Multilingual : texte -> voix clonée depuis un échantillon (10-30 s)."""
    g = Graph()
    extra = {}
    if voice:
        extra["audio_prompt"] = [g.add("LoadAudio", audio=voice), 0]
    t = g.add("FL_ChatterboxMultilingualTTS", text=text, language=LANGUAGES.get(language, language),
              exaggeration=exaggeration, cfg_weight=cfg_weight, temperature=temperature,
              repetition_penalty=2.0, min_p=0.05, top_p=1.0, seed=seed or rand_seed() % 2**32,
              keep_model_loaded=False, **extra)
    g.add("SaveAudio", audio=[t, 0], filename_prefix=prefix)
    return g.nodes


def voice_convert(source_audio: str, target_voice: str, seed: int | None = None,
                  prefix: str = "daytona/voice_vc") -> dict:
    """Chatterbox VC : garde ton intonation / ton texte, remplace le timbre par la voix cible."""
    g = Graph()
    src = g.add("LoadAudio", audio=source_audio)
    tgt = g.add("LoadAudio", audio=target_voice)
    vc = g.add("FL_ChatterboxVC", input_audio=[src, 0], target_voice=[tgt, 0], seed=seed or rand_seed() % 2**32,
               keep_model_loaded=False)
    g.add("SaveAudio", audio=[vc, 0], filename_prefix=prefix)
    return g.nodes


# ===================================================== 4. AVATAR PARLANT (Speak)
TALK_FPS = 25
TALK_CHUNK = 81
TALK_MOTION = 9


def talk_segments(audio_seconds: float) -> int:
    total = math.ceil(audio_seconds * TALK_FPS) + 1
    return 1 + max(0, math.ceil((total - TALK_CHUNK) / (TALK_CHUNK - TALK_MOTION)))


def talking_video(image: str, audio: str, audio_seconds: float, prompt: str,
                  width: int = 480, height: int = 832, seed: int | None = None,
                  audio_scale: float = 1.0, prefix: str = "daytona/talk") -> dict:
    """InfiniteTalk (Wan 2.1 I2V 14B) : photo + audio -> vidéo parlante, durée illimitée
    (enchaîne automatiquement des segments de 81 images à 25 fps)."""
    g = Graph()
    unet = g.add("UNETLoader", unet_name="Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors", weight_dtype="default")
    model = g.add("LoraLoaderModelOnly", model=[unet, 0], lora_name=LIGHTX2V_I2V, strength_model=1.0)
    patch = g.add("ModelPatchLoader", name="wan2.1_infiniteTalk_single_fp16.safetensors")
    clip = g.add("CLIPLoader", clip_name=WAN_T5, type="wan", device="default")
    vae = g.add("VAELoader", vae_name=WAN_VAE)
    aenc = g.add("AudioEncoderLoader", audio_encoder_name="wav2vec2-chinese-base_fp16.safetensors")
    aud = g.add("LoadAudio", audio=audio)
    aemb = g.add("AudioEncoderEncode", audio_encoder=[aenc, 0], audio=[aud, 0])
    img = g.add("LoadImage", image=image)
    pos = g.add("CLIPTextEncode", clip=[clip, 0], text=prompt)
    neg = g.add("ConditioningZeroOut", conditioning=[pos, 0])
    sampler = g.add("KSamplerSelect", sampler_name="euler")
    s = seed or rand_seed()
    acc = None
    for i in range(talk_segments(audio_seconds)):
        extra = {"previous_frames": [acc, 0]} if acc else {}
        it = g.add("WanInfiniteTalkToVideo", mode="single_speaker", model=[model, 0], model_patch=[patch, 0],
                   positive=[pos, 0], negative=[neg, 0], vae=[vae, 0], width=width, height=height,
                   length=TALK_CHUNK, audio_encoder_output_1=[aemb, 0], motion_frame_count=TALK_MOTION,
                   audio_scale=audio_scale, start_image=[img, 0], **extra)
        guider = g.add("CFGGuider", model=[it, 0], positive=[it, 1], negative=[it, 2], cfg=1.0)
        sig = g.add("BasicScheduler", model=[it, 0], scheduler="normal", steps=6, denoise=1.0)
        noise = g.add("RandomNoise", noise_seed=s + i)
        samp = g.add("SamplerCustomAdvanced", noise=[noise, 0], guider=[guider, 0], sampler=[sampler, 0],
                     sigmas=[sig, 0], latent_image=[it, 3])
        dec = g.add("VAEDecode", samples=[samp, 0], vae=[vae, 0])
        if acc is None:
            acc = dec
        else:
            new = g.add("ImageFromBatch", image=[dec, 0], batch_index=TALK_MOTION, length=4096)
            acc = g.add("BatchImagesNode", **{"images.image0": [acc, 0], "images.image1": [new, 0]})
    vid = g.add("CreateVideo", images=[acc, 0], audio=[aud, 0], fps=float(TALK_FPS))
    _save_video(g, [vid, 0], prefix)
    return g.nodes


# ============================================ 5. RECAST — remplacement (SCAIL-2)
SCAIL_CHUNK = 81
SCAIL_OVERLAP = 5


def scail_segments(frames: int) -> int:
    return 1 + max(0, math.ceil((frames - SCAIL_CHUNK) / (SCAIL_CHUNK - SCAIL_OVERLAP)))


def scail_padded_frames(frames: int) -> int:
    n = scail_segments(frames)
    return SCAIL_CHUNK + (n - 1) * (SCAIL_CHUNK - SCAIL_OVERLAP)


def recast_replace(ref_image: str, video: str, frames: int, prompt: str,
                   target_in_video: str = "person", target_in_ref: str = "woman",
                   width: int = 480, height: int = 832, replace_mode: bool = True,
                   turbo: bool = True, seed: int | None = None, prefix: str = "daytona/recast") -> dict:
    """SCAIL-2 : remplace la personne de ta vidéo par l'avatar en gardant le décor,
    les mouvements, l'expression et le son d'origine. Durée illimitée (segments de 81 images)."""
    t = _load_template("scail2_replace.api.json")
    seg_tpl = {k: v for k, v in t.items() if k.startswith("262:")}
    s = seed or rand_seed()

    t["30"]["inputs"]["image"] = ref_image
    t["155"]["inputs"]["file"] = video
    t["271"]["inputs"]["filename_prefix"] = prefix

    def configure(p: str, idx: int):
        ids = {  # (base, extension)
            "prompt": ("213:3", "262:258"), "sam_vid": ("213:191", "262:242"), "sam_ref": ("213:212", "262:261"),
            "w": ("213:178", "262:236"), "h": ("213:179", "262:237"), "replace": ("213:203", "262:247"),
            "turbo": ("213:172", "262:255"), "sampler": ("213:19", "262:227"),
        }
        pick = lambda key: ids[key][0] if p == "213" else ids[key][1].replace("262:", p + ":")
        t[pick("prompt")]["inputs"]["text"] = prompt
        t[pick("sam_vid")]["inputs"]["text"] = target_in_video
        t[pick("sam_ref")]["inputs"]["text"] = target_in_ref
        t[pick("w")]["inputs"]["value"] = width
        t[pick("h")]["inputs"]["value"] = height
        t[pick("replace")]["inputs"]["value"] = replace_mode
        t[pick("turbo")]["inputs"]["value"] = turbo
        t[pick("sampler")]["inputs"]["noise_seed"] = s + idx

    configure("213", 0)
    n = scail_segments(frames)
    batch = {"images.image0": ["213:6", 0]}
    if n == 1:
        for k in seg_tpl:
            t.pop(k)
    else:
        configure("262", 1)
        batch["images.image1"] = ["262:252", 0]
        prev = "262"
        for idx in range(3, n + 1):
            p = f"s{idx}"
            for k, v in seg_tpl.items():
                nv = copy.deepcopy(v)
                for name, val in nv["inputs"].items():
                    if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) and val[0].startswith("262:"):
                        nv["inputs"][name] = [p + val[0][3:], val[1]]
                t[p + k[3:]] = nv
            t[f"{p}:240"]["inputs"]["value"] = idx
            t[f"{p}:259"]["inputs"]["previous_frames"] = [f"{prev}:250", 0]
            configure(p, idx - 1)
            batch[f"images.image{idx - 1}"] = [f"{p}:252", 0]
            prev = p
    t["269"]["inputs"] = batch
    return prune(t, ["271"])


# ================================== 6. RECAST — nouveau fond (Wan Animate 2)
def recast_new_background(ref_image: str, video: str, appearance_and_background: str, motion: str,
                          width: int = 480, height: int = 854, seed: int | None = None,
                          prefix: str = "daytona/recast_bg") -> dict:
    """Wan-Animate-2 : l'avatar reproduit les mouvements de ta vidéo ; le décor est
    généré à partir du texte. Gère toute la longueur de la vidéo (boucle native)."""
    t = _load_template("wan_animate2.api.json")
    t["189"]["inputs"]["image"] = ref_image
    t["240"]["inputs"]["file"] = video
    t["672:582"]["inputs"]["text"] = appearance_and_background
    t["672:585"]["inputs"]["text"] = motion
    t["672:600"]["inputs"]["resize_type.width"] = width
    t["672:600"]["inputs"]["resize_type.height"] = height
    t["672:597"]["inputs"]["noise_seed"] = seed or rand_seed()
    t["246"]["inputs"]["filename_prefix"] = prefix
    return prune(t, ["246"])


# ==================================================== 7. DÉTOURAGE / FOND
def remove_background(image: str, prefix: str = "daytona/cutout") -> dict:
    """BiRefNet : renvoie un PNG transparent (le nouveau fond est appliqué ensuite)."""
    g = Graph()
    m = g.add("LoadBackgroundRemovalModel", bg_removal_name="birefnet.safetensors")
    img = g.add("LoadImage", image=image)
    rb = g.add("RemoveBackground", bg_removal_model=[m, 0], image=[img, 0])
    inv = g.add("InvertMask", mask=[rb, 0])
    rgba = g.add("JoinImageWithAlpha", image=[img, 0], alpha=[inv, 0])
    g.add("SaveImage", images=[rgba, 0], filename_prefix=prefix)
    return g.nodes


# ===================================================== 8. FLUIDITÉ (FILM)
def interpolate(video: str, multiplier: int = 2, prefix: str = "daytona/smooth") -> dict:
    g = Graph()
    v = g.add("LoadVideo", file=video)
    comp = g.add("GetVideoComponents", video=[v, 0])
    m = g.add("FrameInterpolationModelLoader", model_name="film_net_fp16.safetensors")
    fi = g.add("FrameInterpolate", interp_model=[m, 0], images=[comp, 0], multiplier=multiplier)
    fps = g.add("ComfyMathExpression", expression=f"a * {multiplier}", **{"values.a": [comp, 2]})
    vid = g.add("CreateVideo", images=[fi, 0], audio=[comp, 1], fps=[fps, 0])
    _save_video(g, [vid, 0], prefix)
    return g.nodes
