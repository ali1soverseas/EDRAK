"""Local Argos Translate for Arabic output and for tagging an Arabic decision reason.

The language package stays on this machine. It downloads on the first translation.
A CUDA device runs the model; otherwise it stays on the CPU. ARGOS_DEVICE_TYPE overrides this.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_TRANSLATORS: dict[tuple[str, str], Any] = {}
_DEVICE_NOTED = False
_LOADED = False


def _register_cuda_dlls() -> None:
    """Windows loads cublas64_12.dll only from known directories. The pip CUDA packages put it under nvidia/."""
    if os.name != "nt":
        return
    import ctranslate2

    root = Path(ctranslate2.__file__).resolve().parent.parent / "nvidia"
    if not root.is_dir():
        return
    path = os.environ.get("PATH", "")
    for bin_dir in sorted(root.glob("*/bin")):
        if not any(bin_dir.glob("*.dll")):
            continue
        os.add_dll_directory(str(bin_dir))
        folder = str(bin_dir)
        if folder not in path:
            path = folder + os.pathsep + path
    os.environ["PATH"] = path


def _split_on_cpu() -> None:
    """Sentence splitting uses PyTorch. This install has no CUDA build of it, so the splitter stays on the CPU."""
    import argostranslate.sbd as sbd

    def wrap(cls: type, method_name: str) -> None:
        original = getattr(cls, method_name)
        if getattr(original, "_edrak_cpu", False):
            return

        def on_cpu(self: Any) -> Any:
            import argostranslate.settings as settings

            device = settings.device
            settings.device = "cpu"
            try:
                return original(self)
            finally:
                settings.device = device

        on_cpu._edrak_cpu = True
        setattr(cls, method_name, on_cpu)

    wrap(sbd.StanzaSentencizer, "lazy_pipeline")
    wrap(sbd.MiniSBDSentencizer, "lazy_detector")


def _prefer_gpu() -> None:
    """Set the Argos device before its settings module reads the environment."""
    try:
        import ctranslate2
    except Exception:
        logger.warning("Argos Translate could not check for a GPU, so it will use the CPU.")
        return
    if ctranslate2.get_cuda_device_count() <= 0:
        return
    _register_cuda_dlls()
    if not os.environ.get("ARGOS_DEVICE_TYPE"):
        os.environ["ARGOS_DEVICE_TYPE"] = "cuda"


def _note_device() -> None:
    global _DEVICE_NOTED
    if _DEVICE_NOTED:
        return
    _DEVICE_NOTED = True
    import argostranslate.settings as settings

    where = "GPU (CUDA)" if settings.device == "cuda" else "CPU"
    logger.warning(
        "\n======== ARGOS TRANSLATE DEVICE ========\n"
        "Running on: %s\n"
        "=========================================",
        where,
    )


def _translator(from_code: str, to_code: str) -> Any:
    key = (from_code, to_code)
    cached = _TRANSLATORS.get(key)
    if cached is not None:
        return cached
    _prefer_gpu()
    import argostranslate.package
    import argostranslate.translate

    _split_on_cpu()

    def _pair() -> Any | None:
        installed = argostranslate.translate.get_installed_languages()
        source = next((lang for lang in installed if lang.code == from_code), None)
        target = next((lang for lang in installed if lang.code == to_code), None)
        if source is None or target is None:
            return None
        return source.get_translation(target)

    translation = _pair()
    if translation is None:
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        package = next(
            (pkg for pkg in available if pkg.from_code == from_code and pkg.to_code == to_code),
            None,
        )
        if package is None:
            raise RuntimeError(f"No Argos Translate package for {from_code} -> {to_code}")
        argostranslate.package.install_from_path(package.download())
        translation = _pair()
    if translation is None:
        raise RuntimeError(f"Argos Translate package for {from_code} -> {to_code} did not install")
    _TRANSLATORS[key] = translation
    _note_device()
    return translation


def _use_cpu(translator: Any) -> None:
    """The GPU model did not start. Later calls in this process use the CPU."""
    import argostranslate.settings as settings

    settings.device = "cpu"
    os.environ["ARGOS_DEVICE_TYPE"] = "cpu"
    translator.translator = None


def translate_many(texts: list[str], *, from_code: str = "en", to_code: str = "ar") -> list[str]:
    """Translate each string. The language package loads on the first call."""
    if not texts:
        return []
    with _LOCK:
        translator = _translator(from_code, to_code)
        try:
            translated = [(translator.translate(text) or "").strip() for text in texts]
        except Exception as exc:
            global _LOADED
            if _LOADED:
                raise
            import argostranslate.settings as settings

            if settings.device != "cuda":
                raise
            logger.warning("Argos Translate could not start on the GPU (%s). Using the CPU.", exc)
            _use_cpu(translator)
            global _DEVICE_NOTED
            _DEVICE_NOTED = False
            _note_device()
            translated = [(translator.translate(text) or "").strip() for text in texts]
        _LOADED = True
        _note_device()
        return translated


def translate_ar_to_en(text: str) -> str:
    """The English wording used to tag an Arabic reason. The stored reason stays Arabic."""
    return translate_many([text], from_code="ar", to_code="en")[0]


def translate_en_to_ar(text: str) -> str:
    """Arabic for one stored English sentence. Callers cache the result."""
    return translate_many([text], from_code="en", to_code="ar")[0]
