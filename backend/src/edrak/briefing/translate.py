"""Local Argos Translate for Arabic output and for tagging an Arabic decision reason.

The language package stays on this machine. It downloads on the first translation.
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_TRANSLATORS: dict[tuple[str, str], Any] = {}


def _translator(from_code: str, to_code: str) -> Any:
    key = (from_code, to_code)
    cached = _TRANSLATORS.get(key)
    if cached is not None:
        return cached
    import argostranslate.package
    import argostranslate.translate

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
    return translation


def translate_many(texts: list[str], *, from_code: str = "en", to_code: str = "ar") -> list[str]:
    """Translate each string. The language package loads on the first call."""
    if not texts:
        return []
    with _LOCK:
        translator = _translator(from_code, to_code)
        return [(translator.translate(text) or "").strip() for text in texts]


def translate_ar_to_en(text: str) -> str:
    """The English wording used to tag an Arabic reason. The stored reason stays Arabic."""
    return translate_many([text], from_code="ar", to_code="en")[0]


def translate_en_to_ar(text: str) -> str:
    """Arabic for one stored English sentence. Callers cache the result."""
    return translate_many([text], from_code="en", to_code="ar")[0]
