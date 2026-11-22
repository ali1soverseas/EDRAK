"""Local Helsinki OPUS-MT for Arabic output and for tagging an Arabic decision reason.

English findings use Helsinki-NLP/opus-mt-en-ar. An Arabic decision reason is tagged
with Helsinki-NLP/opus-mt-ar-en. Each model downloads on first use.
Translation runs on CUDA when this PyTorch build has it, and on the CPU otherwise.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MODELS: dict[tuple[str, str], str] = {
    ("en", "ar"): "Helsinki-NLP/opus-mt-en-ar",
    ("ar", "en"): "Helsinki-NLP/opus-mt-ar-en",
}
_LOADED: dict[tuple[str, str], tuple[Any, Any]] = {}
_DEVICE_NOTED = False


def _device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _note_device(name: str, device: str) -> None:
    global _DEVICE_NOTED
    if _DEVICE_NOTED:
        return
    _DEVICE_NOTED = True
    where = "GPU (CUDA)" if device == "cuda" else "CPU"
    logger.warning(
        "\n======== OPUS-MT TRANSLATE DEVICE ========\n"
        "Model: %s\n"
        "Running on: %s\n"
        "==========================================",
        name,
        where,
    )


def _pair(from_code: str, to_code: str) -> tuple[Any, Any]:
    key = (from_code, to_code)
    cached = _LOADED.get(key)
    if cached is not None:
        return cached
    name = _MODELS.get(key)
    if name is None:
        raise RuntimeError(f"No OPUS-MT model for {from_code} -> {to_code}")
    from transformers import MarianMTModel, MarianTokenizer

    tokenizer = MarianTokenizer.from_pretrained(name)
    device = _device()
    model = MarianMTModel.from_pretrained(name).to(device)
    model.eval()
    _LOADED[key] = (tokenizer, model)
    _note_device(name, device)
    return tokenizer, model


def translate_many(texts: list[str], *, from_code: str = "en", to_code: str = "ar") -> list[str]:
    """Translate each string. The model loads on the first call."""
    if not texts:
        return []
    with _LOCK:
        import torch

        tokenizer, model = _pair(from_code, to_code)
        batch = tokenizer(list(texts), return_tensors="pt", padding=True, truncation=True).to(model.device)
        with torch.inference_mode():
            generated = model.generate(**batch, num_beams=1, max_length=256)
        return [tokenizer.decode(row, skip_special_tokens=True).strip() for row in generated]


def translate_ar_to_en(text: str) -> str:
    """The English wording used to tag an Arabic reason. The stored reason stays Arabic."""
    return translate_many([text], from_code="ar", to_code="en")[0]


def translate_en_to_ar(text: str) -> str:
    """Arabic for one stored English sentence. Callers cache the result."""
    return translate_many([text], from_code="en", to_code="ar")[0]
