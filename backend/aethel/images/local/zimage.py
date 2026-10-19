"""Z-Image Turbo on this PC, ported from backend/image_generator.py (the legacy SD 1.5 path isn't carried over).

Text only: it takes no reference images, so faces may vary (the UI says so). The components are found in
models/image/ by name: a Z-Image transformer (.gguf or .safetensors, name contains "z-image"/"zimage"), optionally a
VAE (*ae*/*vae*.safetensors, the 16-channel Flux VAE) and a Qwen text encoder (.gguf, or a text_encoder/ folder);
anything missing comes from the base repo once. On a GPU with 6 GB or less the local LLM is suspended while a picture
is drawn and resumed after (the legacy VRAM handoff, now through LocalLlama.suspend/resume).

Not run live in this phase: models/image/ was empty on the development PC."""
import asyncio
import io
import logging
import os
import threading
import time
from pathlib import Path

from ...paths import MODELS_DIR
from ..backend import ImageError, ImageRequest, ImageResult

log = logging.getLogger("aethel.images")
BASE_REPO = "Tongyi-MAI/Z-Image-Turbo"
STEPS, GUIDANCE = 8, 1.0  # Turbo: few steps, effectively no CFG
SIDE = {"portrait": (896, 1152), "square": (1024, 1024), "landscape": (1152, 896)}
HANDOFF_VRAM_GB = 6
VAE_CONFIG = {  # Z-Image's VAE is Flux's 16-channel one; from_single_file would build a 4-channel SD VAE
    "_class_name": "AutoencoderKL", "act_fn": "silu", "block_out_channels": [128, 256, 512, 512],
    "down_block_types": ["DownEncoderBlock2D"] * 4, "up_block_types": ["UpDecoderBlock2D"] * 4, "force_upcast": True,
    "in_channels": 3, "out_channels": 3, "latent_channels": 16, "layers_per_block": 2, "mid_block_add_attention": True,
    "norm_num_groups": 32, "sample_size": 1024, "scaling_factor": 0.3611, "shift_factor": 0.1159,
    "use_post_quant_conv": False, "use_quant_conv": False,
}


def _is_transformer(n: str) -> bool:
    return any(k in n for k in ("z-image", "zimage", "z_image")) and n.endswith((".gguf", ".safetensors"))


def find_components(image_dir: Path) -> dict | None:
    """{transformer, vae, text_encoder} (paths or None), or None without a Z-Image transformer."""
    found: dict[str, str | None] = {"transformer": None, "vae": None, "text_encoder": None}
    if image_dir.is_dir():
        for f in sorted(p for p in image_dir.iterdir() if p.is_file()):
            n = f.name.lower()
            if found["transformer"] is None and _is_transformer(n):
                found["transformer"] = str(f)
            elif found["vae"] is None and n.endswith(".safetensors") and ("vae" in n or "ae" in n):
                found["vae"] = str(f)
            elif found["text_encoder"] is None and n.endswith((".gguf", ".safetensors")) and (
                    "qwen" in n or "text_encoder" in n or "text-encoder" in n):
                found["text_encoder"] = str(f)
    return found if found["transformer"] else None


def _load_pipeline(comps: dict, image_dir: Path):
    import torch
    from diffusers import ZImagePipeline, ZImageTransformer2DModel

    cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if cuda else torch.float32
    if comps["transformer"].lower().endswith(".gguf"):
        from diffusers import GGUFQuantizationConfig
        transformer = ZImageTransformer2DModel.from_single_file(
            comps["transformer"], quantization_config=GGUFQuantizationConfig(compute_dtype=dtype), torch_dtype=dtype)
    else:
        transformer = ZImageTransformer2DModel.from_single_file(comps["transformer"], torch_dtype=dtype)
    kwargs = {"transformer": transformer, "torch_dtype": dtype}
    if comps["vae"]:
        try:
            kwargs["vae"] = _load_vae(comps["vae"], dtype)
        except Exception as exc:  # the base repo's VAE is small: better than failing
            log.warning("local VAE unusable (%r); using the base repo's", exc)
    if comps["text_encoder"] and comps["text_encoder"].lower().endswith(".gguf"):
        from transformers import AutoTokenizer
        from . import gguf_text_encoder as gte
        kwargs["text_encoder"], _ = gte.load_gguf_qwen3_encoder(comps["text_encoder"], device="cpu", dtype=dtype)
        local_tok = gte.find_tokenizer_dir(image_dir)
        kwargs["tokenizer"] = (AutoTokenizer.from_pretrained(local_tok) if local_tok
                               else AutoTokenizer.from_pretrained(BASE_REPO, subfolder="tokenizer"))
    pipe = ZImagePipeline.from_pretrained(BASE_REPO, **kwargs)
    if cuda:
        vram = torch.cuda.get_device_properties(0).total_memory / 2**30
        if vram <= 4:
            pipe.enable_sequential_cpu_offload()
        elif vram <= 8:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
        for opt in ("enable_attention_slicing", "enable_vae_slicing"):
            try:
                getattr(pipe, opt)()
            except Exception:
                pass
    return pipe


def _load_vae(path: str, dtype):
    from diffusers import AutoencoderKL
    from diffusers.loaders.single_file_utils import convert_ldm_vae_checkpoint
    from safetensors.torch import load_file

    state = load_file(path)
    vae = AutoencoderKL.from_config(VAE_CONFIG)
    try:
        state = convert_ldm_vae_checkpoint(state, vae.config)
    except Exception:
        pass  # already in diffusers layout
    missing, _ = vae.load_state_dict(state, strict=False)
    if [m for m in missing if "quant_conv" not in m]:
        raise RuntimeError("the VAE's weights are incomplete")
    return vae.to(dtype)


def _small_gpu() -> bool:
    import torch
    return torch.cuda.is_available() and torch.cuda.get_device_properties(0).total_memory / 2**30 <= HANDOFF_VRAM_GB


class LocalZImage:
    name = "local:z-image-turbo"
    cloud = False
    max_references = 0

    def __init__(self, *, image_dir: Path | None = None, local_llm=None, loader=None, small_gpu=None):
        self.image_dir = image_dir or Path(os.environ.get("AETHEL_IMAGE_DIR", MODELS_DIR / "image"))
        self.local_llm = local_llm  # LocalLlama: suspended around generation on a small GPU
        self._loader = loader or _load_pipeline
        self._small_gpu = small_gpu or _small_gpu
        self._pipe = None
        self._lock = threading.Lock()  # one picture at a time: the GPU can't hold two

    async def available(self) -> tuple[bool, str]:
        if find_components(self.image_dir) is None:
            return False, "No Z-Image model in models/image/."
        return True, ""

    def _run(self, req: ImageRequest) -> bytes:
        with self._lock:
            handoff = self.local_llm is not None and self._small_gpu()
            suspended = handoff and self.local_llm.suspend()
            try:
                if self._pipe is None:
                    comps = find_components(self.image_dir)
                    if comps is None:
                        raise ImageError("No Z-Image model in models/image/.")
                    self._pipe = self._loader(comps, self.image_dir)
                width, height = SIDE[req.size]
                image = self._pipe(prompt=req.prompt, width=width, height=height, num_inference_steps=STEPS,
                                   guidance_scale=GUIDANCE).images[0]
                buf = io.BytesIO()
                image.convert("RGB").save(buf, "JPEG", quality=92)
                return buf.getvalue()
            finally:
                if handoff:
                    self._pipe = None  # give the GPU back entirely
                    _free_cuda()
                if suspended:
                    self.local_llm.resume()

    async def generate(self, req: ImageRequest) -> ImageResult:
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(self._run, req)
        except ImageError:
            raise
        except Exception as exc:
            log.exception("local image generation failed")
            raise ImageError(f"The local image model failed: {type(exc).__name__}", retryable=False) from exc
        return ImageResult(data=data, mime="image/jpeg", backend="local", model="z-image-turbo",
                           latency_ms=int((time.monotonic() - started) * 1000))


def _free_cuda() -> None:
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
