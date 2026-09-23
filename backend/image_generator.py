# image_generator.py
"""
Multi-architecture local image generation.

Supports two model families, auto-detected from the checkpoint in models/image/:

  - SD 1.5   : a .safetensors checkpoint. Loaded via StableDiffusionPipeline
               with Compel for long weighted prompts. ~4GB VRAM class.
  - Z-Image  : Tongyi-MAI's Z-Image Turbo, as a .gguf (GGUF-quantized
     Turbo      transformer) or .safetensors. Loaded via ZImagePipeline; the
               VAE/text-encoder/tokenizer come from the base HF repo while only
               the (large) transformer is your local file. 8 steps, guidance 0.

Detection ('auto') keys off the file extension and name:
  *.gguf                         -> zimage (GGUF transformer)
  name contains 'z-image'/'zimage' -> zimage (safetensors transformer)
  otherwise (*.safetensors)      -> sd15

The pipeline is loaded lazily and cached. unload() frees it and empties the
CUDA cache — used by the VRAM handoff so the LLM can reclaim the GPU.
"""

import os
import gc
import json
import traceback
import threading
from pathlib import Path
from typing import Optional

# Reduce CUDA fragmentation before torch initializes its allocator — important
# on 4 GB GPUs where Z-Image's components are streamed in and out.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
from transformers import logging as transformers_logging

from config import (
    MODEL_PATH, TEMP_IMAGE_DIR, IMAGE_BASE_PROMPT, IMAGE_NEGATIVE_PROMPT,
    IMAGE_INFERENCE_STEPS, IMAGE_GUIDANCE_SCALE, MODELS_DIR, MAX_VRAM_ALLOCATION,
    IMAGE_MODEL_ARCH, ZIMAGE_BASE_REPO, ZIMAGE_STEPS, ZIMAGE_GUIDANCE,
    ZIMAGE_WIDTH, ZIMAGE_HEIGHT,
)

transformers_logging.set_verbosity_error()
os.makedirs(TEMP_IMAGE_DIR, exist_ok=True)

BASE_PROMPT = IMAGE_BASE_PROMPT
NEGATIVE_PROMPT = IMAGE_NEGATIVE_PROMPT

ARCH_SD15 = "sd15"
ARCH_ZIMAGE = "zimage"

_lock = threading.Lock()
_pipe = None
_compel_proc = None
_loaded_arch: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Discovery & architecture detection
# ─────────────────────────────────────────────────────────────

# Filename heuristics for identifying Z-Image components in models/image/.
def _is_zimage_transformer(name: str) -> bool:
    n = name.lower()
    return ("z-image" in n or "zimage" in n or "z_image" in n) and (
        n.endswith(".gguf") or n.endswith(".safetensors")
    )


def _is_vae(name: str) -> bool:
    n = name.lower()
    return n.endswith(".safetensors") and ("ae" in n or "vae" in n)


def _is_text_encoder(name: str) -> bool:
    n = name.lower()
    # Z-Image's text encoder is a Qwen model; not the transformer, not the VAE.
    return ("qwen" in n or "text_encoder" in n or "text-encoder" in n) and (
        n.endswith(".gguf") or n.endswith(".safetensors")
    )


def _image_settings() -> dict:
    """Reads image model path overrides from settings.json (set via the UI)."""
    import json
    settings_path = Path(__file__).parent / "data" / "settings.json"
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def discover_zimage_components() -> Optional[dict]:
    """
    Locates Z-Image components. Explicit UI overrides (any path on the PC) win;
    otherwise files are auto-detected in models/image/ by name. Returns a dict
    with transformer / vae / text_encoder (each a path or None), or None if no
    Z-Image transformer can be found.
    """
    s = _image_settings()
    ov_transformer = (s.get("zimage_transformer_path") or "").strip()
    ov_vae = (s.get("zimage_vae_path") or "").strip()
    ov_text = (s.get("zimage_text_encoder_path") or "").strip()

    transformer = ov_transformer if os.path.isfile(ov_transformer) else None
    vae = ov_vae if os.path.isfile(ov_vae) else None
    text_encoder = ov_text if os.path.isfile(ov_text) else None

    image_dir = MODELS_DIR / "image"
    if image_dir.exists():
        for f in sorted(image_dir.iterdir()):
            if not f.is_file():
                continue
            name = f.name
            if transformer is None and _is_zimage_transformer(name):
                transformer = str(f)
            elif vae is None and _is_vae(name):
                vae = str(f)
            elif text_encoder is None and _is_text_encoder(name):
                text_encoder = str(f)

    if transformer is None:
        return None
    return {"transformer": transformer, "vae": vae, "text_encoder": text_encoder}


def discover_image_model() -> Optional[str]:
    """
    Finds the primary image checkpoint (for arch detection / SD1.5).
    Prefers a Z-Image transformer; otherwise the first SD1.5-style
    .safetensors that is not a VAE; then the legacy MODEL_PATH.
    """
    z = discover_zimage_components()
    if z:
        return z["transformer"]

    # SD1.5 explicit override (image_model_path in settings), then folder scan.
    s = _image_settings()
    ov = (s.get("image_model_path") or "").strip()
    if os.path.isfile(ov):
        return ov

    image_dir = MODELS_DIR / "image"
    if image_dir.exists():
        for f in sorted(image_dir.glob("*.safetensors")):
            if not _is_vae(f.name) and not _is_text_encoder(f.name):
                return str(f)
    legacy = os.path.abspath(os.path.expanduser(MODEL_PATH))
    if os.path.exists(legacy):
        return legacy
    return None


def detect_arch(model_path: str) -> str:
    """Determines the model architecture from the UI/env override or filename."""
    override = (_image_settings().get("image_arch") or IMAGE_MODEL_ARCH or "auto").lower()
    if override in (ARCH_SD15, ARCH_ZIMAGE):
        return override
    name = os.path.basename(model_path).lower()
    if _is_zimage_transformer(name):
        return ARCH_ZIMAGE
    return ARCH_SD15


def image_generation_available() -> bool:
    return discover_image_model() is not None


def get_image_status() -> dict:
    path = discover_image_model()
    status = {
        "available": path is not None,
        "model_file": os.path.basename(path) if path else None,
        "arch": detect_arch(path) if path else None,
        "loaded": _pipe is not None,
        "loaded_arch": _loaded_arch,
    }
    if status["arch"] == ARCH_ZIMAGE:
        comps = discover_zimage_components() or {}
        status["zimage_components"] = {
            "transformer": os.path.basename(comps["transformer"]) if comps.get("transformer") else None,
            "vae": os.path.basename(comps["vae"]) if comps.get("vae") else "(from base repo)",
            "text_encoder": (
                os.path.basename(comps["text_encoder"]) + (
                    " [GGUF]" if comps["text_encoder"].lower().endswith(".gguf") else " [safetensors]"
                ) if comps.get("text_encoder") else "(from base repo)"
            ),
        }
    return status


# ─────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────

def _load_sd15(model_path: str):
    from diffusers.pipelines.stable_diffusion.pipeline_stable_diffusion import StableDiffusionPipeline
    from diffusers.schedulers.scheduling_dpmsolver_multistep import DPMSolverMultistepScheduler
    from compel import Compel

    use_cuda = torch.cuda.is_available()
    dtype = torch.float16 if use_cuda else torch.float32
    print(f"[IMAGE GEN] Loading SD1.5 checkpoint {os.path.basename(model_path)} "
          f"({'GPU fp16' if use_cuda else 'CPU fp32'})...")

    pipe = StableDiffusionPipeline.from_single_file(
        model_path, torch_dtype=dtype, safety_checker=None,
    )
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)

    if use_cuda:
        if MAX_VRAM_ALLOCATION <= 4:
            pipe.enable_model_cpu_offload()
            pipe.enable_attention_slicing()
            pipe.enable_vae_slicing()
        else:
            pipe.to("cuda")

    compel = Compel(
        tokenizer=pipe.tokenizer,
        text_encoder=pipe.text_encoder,
        truncate_long_prompts=False,
    )
    return pipe, compel


# Z-Image's VAE is the 16-latent-channel Flux VAE (no quant convs). Many local
# copies are in the original-LDM key layout (as ComfyUI uses), which diffusers'
# from_single_file defaults to a 4-channel SD config for — causing an
# "encoder.conv_out expected [8,...] got [32,...]" shape error. Passing the
# correct config makes it build the right 16-channel skeleton.
_ZIMAGE_VAE_CONFIG = {
    "_class_name": "AutoencoderKL",
    "act_fn": "silu",
    "block_out_channels": [128, 256, 512, 512],
    "down_block_types": ["DownEncoderBlock2D"] * 4,
    "up_block_types": ["UpDecoderBlock2D"] * 4,
    "force_upcast": True,
    "in_channels": 3,
    "out_channels": 3,
    "latent_channels": 16,
    "layers_per_block": 2,
    "mid_block_add_attention": True,
    "norm_num_groups": 32,
    "sample_size": 1024,
    "scaling_factor": 0.3611,
    "shift_factor": 0.1159,
    "use_post_quant_conv": False,
    "use_quant_conv": False,
}


def _load_zimage_vae(path: str, dtype):
    """
    Loads a local Z-Image (Flux-family, 16-channel) VAE, forcing the correct
    config so diffusers doesn't mis-build it as a 4-channel SD VAE. Works for
    both original-LDM and diffusers key layouts.

    from_single_file only accepts a repo/path for `config`, not a dict, and it
    defaults LDM VAEs to a 4-channel SD config. So we build the AutoencoderKL
    from the known 16-channel config ourselves, convert the checkpoint from
    LDM→diffusers layout, and load the weights in.
    """
    from diffusers import AutoencoderKL
    from diffusers.loaders.single_file_utils import convert_ldm_vae_checkpoint
    from safetensors.torch import load_file

    state_dict = load_file(path)

    vae = AutoencoderKL.from_config(_ZIMAGE_VAE_CONFIG)
    # If the checkpoint is in original-LDM layout, convert it; if it's already
    # in diffusers layout, convert_ldm_vae_checkpoint passes keys through.
    try:
        converted = convert_ldm_vae_checkpoint(state_dict, vae.config)
    except Exception:
        converted = state_dict  # already diffusers layout
    missing, unexpected = vae.load_state_dict(converted, strict=False)
    real_missing = [m for m in missing if "quant_conv" not in m]  # quant convs are disabled
    if real_missing:
        raise RuntimeError(f"VAE weights incomplete after load: {real_missing[:3]}")
    return vae.to(dtype)


def _load_zimage_transformer(path: str, dtype):
    from diffusers import ZImageTransformer2DModel
    if path.lower().endswith(".gguf"):
        from diffusers import GGUFQuantizationConfig
        print(f"[IMAGE GEN] Z-Image transformer: {os.path.basename(path)} (GGUF)")
        return ZImageTransformer2DModel.from_single_file(
            path,
            quantization_config=GGUFQuantizationConfig(compute_dtype=dtype),
            torch_dtype=dtype,
        )
    print(f"[IMAGE GEN] Z-Image transformer: {os.path.basename(path)} (safetensors)")
    return ZImageTransformer2DModel.from_single_file(path, torch_dtype=dtype)


def _load_zimage_tokenizer(image_dir):
    """
    Resolves the Qwen3 tokenizer for the text encoder. Prefers a local
    tokenizer (a tokenizer/ subfolder or tokenizer files directly in
    models/image/); otherwise uses the base repo's tokenizer/ subfolder
    (downloaded once, then cached).
    """
    from transformers import AutoTokenizer
    import gguf_text_encoder as gte
    local = gte.find_tokenizer_dir(image_dir)
    if local:
        print(f"[IMAGE GEN] Z-Image tokenizer: {local} (local)")
        return AutoTokenizer.from_pretrained(local)
    print(f"[IMAGE GEN] Z-Image tokenizer: {ZIMAGE_BASE_REPO}/tokenizer (download once)")
    return AutoTokenizer.from_pretrained(ZIMAGE_BASE_REPO, subfolder="tokenizer")


def _load_zimage_text_encoder(path: Optional[str], device: str, dtype):
    """
    Loads the Z-Image text encoder + tokenizer. Supports both formats:
      - .gguf        -> dequantized into a HF Qwen3Model (via gguf_text_encoder)
      - .safetensors -> loaded natively with transformers.AutoModel
    Returns (text_encoder, tokenizer), or (None, None) to signal "download the
    text encoder + tokenizer from the base repo".
    """
    if not path:
        return None, None

    from pathlib import Path as _P
    image_dir = MODELS_DIR / "image"

    if path.lower().endswith(".gguf"):
        import gguf_text_encoder as gte
        print(f"[IMAGE GEN] Z-Image text encoder: {os.path.basename(path)} (GGUF Qwen3 -> HF)")
        # Always build on CPU. On constrained GPUs the pipeline uses
        # enable_model_cpu_offload(), which streams each component to the GPU
        # only while it runs — loading the encoder straight to CUDA on top of
        # the transformer is what caused the OOM.
        text_encoder, _ = gte.load_gguf_qwen3_encoder(path, device="cpu", dtype=dtype)
        tokenizer = _load_zimage_tokenizer(image_dir)
        return text_encoder, tokenizer

    # safetensors text encoder
    from transformers import AutoModel
    print(f"[IMAGE GEN] Z-Image text encoder: {os.path.basename(path)} (safetensors)")
    # A single-file safetensors encoder needs an accompanying config; load the
    # full text_encoder/ folder if present, otherwise defer to the base repo.
    enc_dir = _P(path).parent / "text_encoder"
    if (enc_dir / "config.json").exists():
        text_encoder = AutoModel.from_pretrained(str(enc_dir), torch_dtype=dtype)
    else:
        return None, None
    tokenizer = _load_zimage_tokenizer(image_dir)
    return text_encoder, tokenizer


def _load_zimage(model_path: str):
    """
    Loads Z-Image Turbo from local component files in models/image/.
    Transformer: GGUF or safetensors (local). VAE: local ae.safetensors if
    present. Text encoder: local GGUF or safetensors if present, else fetched
    from the base repo. Only genuinely-missing components download.
    """
    from diffusers import ZImagePipeline

    use_cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if use_cuda else torch.float32
    device = "cuda" if use_cuda else "cpu"

    comps = discover_zimage_components() or {"transformer": model_path, "vae": None, "text_encoder": None}
    transformer = _load_zimage_transformer(comps["transformer"], dtype)

    # Assemble the kwargs for the pipeline. Anything left as None is pulled from
    # the base repo by from_pretrained (one-time, then cached).
    pipe_kwargs = {"transformer": transformer, "torch_dtype": dtype}

    if comps.get("vae"):
        try:
            vae = _load_zimage_vae(comps["vae"], dtype)
            print(f"[IMAGE GEN] Z-Image VAE: {os.path.basename(comps['vae'])} (local)")
            pipe_kwargs["vae"] = vae
        except Exception as e:
            # If the local VAE genuinely can't be loaded, fall back to the
            # base-repo VAE (168 MB, one-time download) rather than crash.
            print(
                f"[IMAGE GEN] Local VAE '{os.path.basename(comps['vae'])}' unusable "
                f"({e}). Falling back to the base-repo VAE."
            )

    has_local_encoder = bool(comps.get("text_encoder"))
    try:
        text_encoder, tokenizer = _load_zimage_text_encoder(comps.get("text_encoder"), device, dtype)
        if text_encoder is not None:
            pipe_kwargs["text_encoder"] = text_encoder
            if tokenizer is not None:
                pipe_kwargs["tokenizer"] = tokenizer
    except Exception as e:
        # If the user configured a LOCAL text encoder, do NOT silently download
        # the ~12 GB safetensors encoder as a fallback — that's the opposite of
        # what they want. Fail loudly so they can fix the file or free VRAM.
        if has_local_encoder:
            raise RuntimeError(
                f"Local Z-Image text encoder failed to load: {e}. "
                f"Not downloading the base-repo encoder. "
                f"(If this is CUDA OOM, the encoder is loaded on CPU now; ensure "
                f"nothing else is holding the GPU.)"
            ) from e
        # No local encoder configured at all -> allow the base-repo download.
        print(f"[IMAGE GEN] No local text encoder; using base-repo encoder. ({e})")

    # Build the pipeline with all components on CPU. Do NOT let from_pretrained
    # move anything to CUDA yet — enable_model_cpu_offload() handles GPU
    # placement per-component at inference time.
    pipe = ZImagePipeline.from_pretrained(ZIMAGE_BASE_REPO, **pipe_kwargs)

    if use_cuda:
        if MAX_VRAM_ALLOCATION <= 8:
            # Sequential offload is the most aggressive: only the single module
            # currently executing sits in VRAM. Essential for 4 GB GPUs where
            # even one Z-Image component can exceed the budget on its own.
            if MAX_VRAM_ALLOCATION <= 4 and hasattr(pipe, "enable_sequential_cpu_offload"):
                pipe.enable_sequential_cpu_offload()
            else:
                pipe.enable_model_cpu_offload()
            try:
                pipe.enable_attention_slicing()
            except Exception:
                pass
            try:
                pipe.enable_vae_slicing()
            except Exception:
                pass
        else:
            pipe.to("cuda")

    return pipe, None  # Z-Image takes plain string prompts; no Compel


def _load_image_assets():
    global _pipe, _compel_proc, _loaded_arch
    if _pipe is not None:
        return _pipe, _compel_proc, _loaded_arch

    model_path = discover_image_model()
    if not model_path:
        raise FileNotFoundError(
            "No image checkpoint found. Place a .safetensors (SD 1.5) or .gguf "
            f"(Z-Image Turbo) file in {MODELS_DIR / 'image'}."
        )

    arch = detect_arch(model_path)
    if arch == ARCH_ZIMAGE:
        _pipe, _compel_proc = _load_zimage(model_path)
    else:
        _pipe, _compel_proc = _load_sd15(model_path)
    _loaded_arch = arch
    return _pipe, _compel_proc, _loaded_arch


def unload():
    """Frees the loaded pipeline and empties CUDA cache (for the VRAM handoff)."""
    global _pipe, _compel_proc, _loaded_arch
    with _lock:
        if _pipe is None:
            return
        print("[IMAGE GEN] Unloading image pipeline, freeing VRAM...")
        _pipe = None
        _compel_proc = None
        _loaded_arch = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()


# ─────────────────────────────────────────────────────────────
# Settings & prompting
# ─────────────────────────────────────────────────────────────

def load_dynamic_sd_settings() -> dict:
    settings_path = Path(__file__).parent / "data" / "settings.json"
    defaults = {
        "sd_steps": IMAGE_INFERENCE_STEPS,
        "sd_cfg_scale": IMAGE_GUIDANCE_SCALE,
        "sd_negative_prompt": IMAGE_NEGATIVE_PROMPT,
        "sd_base_prompt": IMAGE_BASE_PROMPT,
    }
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "sd_steps": int(data.get("sd_steps", IMAGE_INFERENCE_STEPS)),
                    "sd_cfg_scale": float(data.get("sd_cfg_scale", IMAGE_GUIDANCE_SCALE)),
                    "sd_negative_prompt": str(data.get("sd_negative_prompt", IMAGE_NEGATIVE_PROMPT)),
                    "sd_base_prompt": str(data.get("sd_base_prompt", IMAGE_BASE_PROMPT)),
                }
        except Exception:
            pass
    return defaults


def enhance_image_prompt(user_description: str, original_user_input: str, base_prompt: str = None) -> str:
    if base_prompt is None:
        base_prompt = BASE_PROMPT
    fashion_tags = (
        "fashion photography, professional model pose, studio lighting, detailed garments, "
        "highly realistic fabric texture, sharp focus, aesthetic composition"
    )
    return f"{user_description}, {fashion_tags}, {base_prompt}"


# ─────────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────────

def _generate_sd15(pipe, compel, final_prompt, neg_prompt, steps, cfg):
    prompt_embeds = compel(final_prompt)
    negative_embeds = compel(neg_prompt)
    [prompt_embeds, negative_embeds] = compel.pad_conditioning_tensors_to_same_length(
        [prompt_embeds, negative_embeds]
    )
    return pipe(
        prompt_embeds=prompt_embeds,
        negative_prompt_embeds=negative_embeds,
        num_inference_steps=steps,
        guidance_scale=cfg,
    ).images[0]


def _generate_zimage(pipe, final_prompt):
    # Generation resolution: settings.json "zimage_resolution" overrides the
    # config default. Lower = far less peak VRAM (critical on 4 GB GPUs).
    s = _image_settings()
    try:
        res = int(s.get("zimage_resolution") or 0)
    except (TypeError, ValueError):
        res = 0
    width = height = res if res >= 256 else ZIMAGE_WIDTH
    print(f"[IMAGE GEN] Z-Image generating at {width}x{height}, {ZIMAGE_STEPS} steps...")

    # The generator must live on the same device the pipeline samples on.
    # With CPU offload the pipe reports device 'cpu', so build the generator
    # there to avoid a device-mismatch error.
    gen_device = "cpu"
    return pipe(
        prompt=final_prompt,
        height=height,
        width=width,
        num_inference_steps=ZIMAGE_STEPS,
        guidance_scale=ZIMAGE_GUIDANCE,
        generator=torch.Generator(gen_device).manual_seed(int.from_bytes(os.urandom(4), "big")),
    ).images[0]


def _handoff_enabled() -> bool:
    """Whether to suspend the LLM around image gen (frees VRAM on small GPUs)."""
    from config import IMAGE_GEN_VRAM_HANDOFF, IMAGE_GEN_HANDOFF_VRAM_THRESHOLD
    mode = IMAGE_GEN_VRAM_HANDOFF
    if mode in ("true", "1", "on", "always"):
        return True
    if mode in ("false", "0", "off", "never"):
        return False
    # "auto": only when the GPU budget is at/under the threshold, and a GPU
    # is actually present (no point suspending the LLM if image gen is on CPU).
    if not torch.cuda.is_available():
        return False
    return MAX_VRAM_ALLOCATION <= IMAGE_GEN_HANDOFF_VRAM_THRESHOLD


def generate_selfie_with_handoff(user_description: str, original_user_input: str = ""):
    """
    Generates an image, borrowing the GPU from the LLM when VRAM is tight.

    Sequence on a constrained GPU:
        suspend LLM (free its VRAM) -> generate image -> unload SD (free VRAM)
        -> resume LLM.
    The LLM restart is safe for conversation continuity: chat history lives in
    the API process and is re-sent every request, so nothing is forgotten — only
    the KV cache is rebuilt on the next message.

    Runs entirely in the calling worker thread (invoke via asyncio.to_thread).
    """
    if not _handoff_enabled():
        return generate_selfie(user_description, original_user_input)

    import generation
    suspended = False
    try:
        suspended = generation.suspend_llama_server()
        path = generate_selfie(user_description, original_user_input)
        return path
    finally:
        # Always free SD's VRAM and bring the LLM back, even if gen failed.
        unload()
        if suspended:
            try:
                generation.resume_llama_server()
            except Exception as e:
                print(f"[IMAGE GEN] LLM resume error: {e}")


def generate_selfie(user_description: str, original_user_input: str = ""):
    try:
        with _lock:
            pipe, compel, arch = _load_image_assets()

            sd_settings = load_dynamic_sd_settings()
            base_prompt = sd_settings["sd_base_prompt"]
            final_prompt = enhance_image_prompt(user_description, original_user_input, base_prompt)
            print(f"[IMAGE GEN] ({arch}) prompt: {final_prompt[:140]}...")

            if arch == ARCH_ZIMAGE:
                image = _generate_zimage(pipe, final_prompt)
            else:
                image = _generate_sd15(
                    pipe, compel, final_prompt,
                    sd_settings["sd_negative_prompt"],
                    sd_settings["sd_steps"], sd_settings["sd_cfg_scale"],
                )

            filename = f"selfie_{os.urandom(6).hex()}.jpg"
            filepath = os.path.join(TEMP_IMAGE_DIR, filename)
            image.save(filepath)
            return filepath

    except Exception:
        print("\n[ERROR] The Image Generation pipeline crashed. Here is the REAL error:")
        traceback.print_exc()
        return None
