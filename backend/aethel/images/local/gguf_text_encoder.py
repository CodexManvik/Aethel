"""
gguf_text_encoder.py

Loads a GGUF-quantized Qwen3 text encoder into a standard HuggingFace
Qwen3Model, so Z-Image can extract hidden_states[-2] exactly as it would from
a safetensors encoder.

This mirrors the approach city96/ComfyUI-GGUF uses: we do NOT go through
llama.cpp (whose embedding API only exposes the final layer). Instead we read
the raw GGUF tensors, dequantize them to fp16/fp32 with the `gguf` package, map
the GGUF tensor names to HF Qwen3 state-dict keys, and load them into a real
nn.Module. After that it's an ordinary transformers model — output_hidden_states
works, and layer -2 is available.

Only the base transformer stack is needed (no LM head), since Z-Image consumes
intermediate hidden states, not logits.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import torch


# GGUF (llama.cpp) → HF Qwen3 per-block tensor name map.
_BLOCK_MAP = {
    "attn_norm.weight":   "input_layernorm.weight",
    "attn_q.weight":      "self_attn.q_proj.weight",
    "attn_k.weight":      "self_attn.k_proj.weight",
    "attn_v.weight":      "self_attn.v_proj.weight",
    "attn_output.weight": "self_attn.o_proj.weight",
    "attn_q_norm.weight": "self_attn.q_norm.weight",
    "attn_k_norm.weight": "self_attn.k_norm.weight",
    "ffn_norm.weight":    "post_attention_layernorm.weight",
    "ffn_gate.weight":    "mlp.gate_proj.weight",
    "ffn_up.weight":      "mlp.up_proj.weight",
    "ffn_down.weight":    "mlp.down_proj.weight",
}

_NON_BLOCK_MAP = {
    "token_embd.weight":  "embed_tokens.weight",
    "output_norm.weight": "norm.weight",
}


def _read_gguf_metadata(reader) -> dict:
    """Extracts the Qwen3 hyperparameters we need to build the HF config."""
    def val(field_name, default=None):
        f = reader.fields.get(field_name)
        if not f or not f.data:
            return default
        part = f.parts[f.data[-1]]
        try:
            return int(part[0]) if hasattr(part, "__len__") else int(part)
        except Exception:
            return default

    return {
        "block_count":     val("qwen3.block_count"),
        "embedding_length": val("qwen3.embedding_length"),
        "ffn_length":      val("qwen3.feed_forward_length"),
        "head_count":      val("qwen3.attention.head_count"),
        "head_count_kv":   val("qwen3.attention.head_count_kv"),
        "context_length":  val("qwen3.context_length", 32768),
        "rope_freq_base":  val("qwen3.rope.freq_base", 1000000),
        "layer_norm_eps":  None,  # use HF default
        "vocab_size":      None,  # filled from token_embd shape
    }


def _map_name(gguf_name: str) -> Optional[str]:
    """Maps one GGUF tensor name to its HF Qwen3Model state-dict key."""
    if gguf_name in _NON_BLOCK_MAP:
        return _NON_BLOCK_MAP[gguf_name]
    m = re.match(r"blk\.(\d+)\.(.+)", gguf_name)
    if not m:
        return None
    layer, suffix = m.group(1), m.group(2)
    hf_suffix = _BLOCK_MAP.get(suffix)
    if hf_suffix is None:
        return None
    return f"layers.{layer}.{hf_suffix}"


def load_gguf_qwen3_encoder(gguf_path: str, device: str, dtype: torch.dtype):
    """
    Builds a HF Qwen3Model from a GGUF file. Returns (model, config).
    The model is in eval mode on the requested device/dtype.
    """
    from gguf import GGUFReader, dequantize
    from transformers import Qwen3Config
    from transformers.models.qwen3.modeling_qwen3 import Qwen3Model

    reader = GGUFReader(gguf_path)
    meta = _read_gguf_metadata(reader)

    # Build the HF state dict by dequantizing + remapping every tensor.
    state_dict: dict[str, torch.Tensor] = {}
    vocab_size = None
    for t in reader.tensors:
        hf_key = _map_name(t.name)
        if hf_key is None:
            continue
        arr = dequantize(t.data, t.tensor_type).astype(np.float32)
        # GGUF stores 2D weights transposed relative to HF (out, in) — the
        # gguf package already returns them in (out_features, in_features)
        # numpy order matching torch Linear.weight, so no transpose needed.
        tensor = torch.from_numpy(arr)
        state_dict[hf_key] = tensor
        if t.name == "token_embd.weight":
            vocab_size = tensor.shape[0]

    if vocab_size is None:
        raise RuntimeError("GGUF text encoder missing token_embd.weight")

    config = Qwen3Config(
        vocab_size=vocab_size,
        hidden_size=meta["embedding_length"],
        intermediate_size=meta["ffn_length"],
        num_hidden_layers=meta["block_count"],
        num_attention_heads=meta["head_count"],
        num_key_value_heads=meta["head_count_kv"],
        max_position_embeddings=meta["context_length"],
        rope_theta=float(meta["rope_freq_base"]),
        tie_word_embeddings=True,
    )

    # Build skeleton on meta (no weight allocation), then to_empty() to allocate
    # real (uninitialized) storage on the target device, and ONLY THEN load the
    # dequantized weights into it. Order is critical: to_empty() wipes tensors,
    # so the state_dict must be loaded after it, not before.
    with torch.device("meta"):
        model = Qwen3Model(config)
    model = model.to_empty(device=device)

    # Rotary inv_freq buffers hold no data after to_empty — recompute them.
    _reinit_rotary(model, config, device)

    # Cast the dequantized weights to the target dtype up front, then load.
    typed_sd = {k: v.to(dtype) for k, v in state_dict.items()}
    missing, unexpected = model.load_state_dict(typed_sd, strict=False)
    real_missing = [m for m in missing if "rotary_emb" not in m and "inv_freq" not in m]
    if real_missing:
        raise RuntimeError(
            f"GGUF encoder load left {len(real_missing)} weights unfilled, "
            f"e.g. {real_missing[:3]} — tensor name map may be out of date."
        )

    model = model.to(dtype=dtype).eval()
    return model, config


def _reinit_rotary(model, config, device: str) -> None:
    """Recomputes rotary-embedding inv_freq buffers after a to_empty() move."""
    for module in model.modules():
        rope = getattr(module, "rotary_emb", None)
        if rope is not None and hasattr(rope, "rope_init_fn"):
            inv_freq, scaling = rope.rope_init_fn(config, device)
            rope.register_buffer("inv_freq", inv_freq.to(device), persistent=False)
            rope.attention_scaling = scaling
    top = getattr(model, "rotary_emb", None)
    if top is not None and hasattr(top, "rope_init_fn"):
        inv_freq, scaling = top.rope_init_fn(config, device)
        top.register_buffer("inv_freq", inv_freq.to(device), persistent=False)
        top.attention_scaling = scaling


def find_tokenizer_dir(image_dir: Path) -> Optional[str]:
    """
    Locates tokenizer files for the GGUF encoder. Order:
      1. a tokenizer/ subfolder in models/image/
      2. tokenizer_config.json sitting directly in models/image/
    Returns a path transformers.AutoTokenizer can load, or None (caller then
    falls back to downloading the tokenizer from the base repo).
    """
    sub = image_dir / "tokenizer"
    if (sub / "tokenizer_config.json").exists() or (sub / "tokenizer.json").exists():
        return str(sub)
    if (image_dir / "tokenizer_config.json").exists() or (image_dir / "tokenizer.json").exists():
        return str(image_dir)
    return None
