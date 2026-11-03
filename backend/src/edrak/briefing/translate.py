"""Local OPUS-MT translation for an Arabic decision reason.

The model stays on this machine. The stored reason stays Arabic; only the wording
used to tag it is translated.
"""

from __future__ import annotations

import re
from typing import Any

AR_EN = "Helsinki-NLP/opus-mt-ar-en"
_MODELS: dict[str, tuple[Any, Any]] = {}
_BATCH = 8


def _model(name: str) -> tuple[Any, Any]:
    if name not in _MODELS:
        from transformers import MarianMTModel, MarianTokenizer

        _MODELS[name] = (MarianTokenizer.from_pretrained(name), MarianMTModel.from_pretrained(name))
    return _MODELS[name]


def _pieces(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()) or [text]:
        if not sentence:
            continue
        if buf and size + len(sentence) > 400:
            parts.append(" ".join(buf))
            buf, size = [], 0
        buf.append(sentence)
        size += len(sentence)
    if buf:
        parts.append(" ".join(buf))
    return parts or [text.strip()]


def _generate_batch(tokenizer: Any, model: Any, texts: list[str]) -> list[str]:
    import torch

    batch = tokenizer(texts, return_tensors="pt", truncation=True, padding=True, max_length=512)
    with torch.no_grad():
        output = model.generate(**batch, max_length=512)
    return [line.strip() for line in tokenizer.batch_decode(output, skip_special_tokens=True)]


def _translate(text: str) -> str:
    tokenizer, model = _model(AR_EN)
    grouped = [_pieces(text)]
    flat = grouped[0]
    done: list[str] = []
    for start in range(0, len(flat), _BATCH):
        done.extend(_generate_batch(tokenizer, model, flat[start:start + _BATCH]))
    return " ".join(done).strip()


def translate_ar_to_en(text: str) -> str:
    """The English wording used to tag an Arabic reason. The stored reason stays Arabic."""
    return _translate(text)
