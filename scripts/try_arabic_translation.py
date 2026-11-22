r"""Run English sentences through the Arabic brief pipeline and print each result.

Does not read or write the translation cache. Edit EXAMPLES, or pass sentences
on the command line. A name after --names is protected the way a company name is.

    .venv\Scripts\python scripts\try_arabic_translation.py
    .venv\Scripts\python scripts\try_arabic_translation.py "Buy a GPU this quarter." "The market is opening."
    .venv\Scripts\python scripts\try_arabic_translation.py --names NVIDIA "NVIDIA's Blackwell rollout may lag."
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from edrak.i18n.localize import (  # noqa: E402
    _ARABIC,
    _default_translator,
    _keep_acronyms,
    _mark_rtl,
    _with_ar_mark,
    _protect_names,
    _restore_names,
    _splice_word_translations,
    _split_sentences,
    _translate_articles,
    _usable_arabic,
    skip_string,
)

EXAMPLES = [
    "The market is opening.",
    "Buy a GPU this quarter.",
    "Competitors: AMD, Intel +1",
    "NVIDIA's Blackwell rollout may lag cloud providers' AI capacity, risking early workload capture.",
]


def _write(text: str) -> None:
    """Write UTF-8 so the console does not replace Arabic letters."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _why(source: str, arabic: str) -> str:
    value = (arabic or "").strip()
    if not value:
        return "blank"
    if value == source.strip():
        return "unchanged"
    if not _ARABIC.search(value):
        return "no Arabic letters"
    if not _usable_arabic(source, value):
        return "dropped an acronym"
    return ""


def _one(source: str, names: list[str]) -> None:
    protected, mapping = _protect_names(source, names)
    raw = _default_translator([protected])[0]
    restored = _restore_names(str(raw or ""), mapping).strip()
    if _ARABIC.search(restored):
        restored = _translate_articles(restored)
    restored = _keep_acronyms(source, restored)
    if not _usable_arabic(source, restored):
        restored = _keep_acronyms(
            source,
            _restore_names(_splice_word_translations(protected, _default_translator), mapping).strip(),
        )
    reason = _why(source, restored)
    _write(f"EN:   {source}")
    if protected != source:
        _write(f"SENT: {protected}")
    _write(_mark_rtl(_with_ar_mark(restored)) if restored else "(empty)")
    _write(f"KEEP: {'yes' if not reason else 'no — ' + reason}")
    _write("")


def _sentences(args: argparse.Namespace) -> list[str]:
    given = list(args.sentences)
    if not given and not sys.stdin.isatty():
        given = [line.strip() for line in sys.stdin if line.strip()]
    if not given:
        given = list(EXAMPLES)
    units: list[str] = []
    for text in given:
        if skip_string(text):
            _write(f"SKIP: {text}")
            _write("")
            continue
        units.extend(_split_sentences(text))
    return units


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("transformers").setLevel(logging.ERROR)
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description="Try English sentences on the Arabic translation pipeline.")
    parser.add_argument("sentences", nargs="*", help="English sentences. With none, the examples in this file are used.")
    parser.add_argument("--names", nargs="*", default=[], help="Company or place names to leave in English.")
    args = parser.parse_args()
    units = _sentences(args)
    if not units:
        _write("No sentences to translate.")
        return
    for unit in units:
        _one(unit, list(args.names))


if __name__ == "__main__":
    main()
