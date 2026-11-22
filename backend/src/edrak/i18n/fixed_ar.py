"""Arabic for sentences the product writes itself.

An Arabic read uses these instead of the model. New briefs also store a
dictionary key, so the page can show the same wording from the UI dictionary.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Whole field values. The English is exactly what the code stores.
EXACT: dict[str, str] = {
    "Competitors": "المنافسون",
    "Customers and trends": "العملاء والاتجاهات",
    "Market": "السوق",
    "Your company": "شركتك",
    "Us versus them": "نحن في مقابلهم",
    "Our figures next to each competitor's: price, scale, revenue and what customers say.": "أرقامنا إلى جانب أرقام كل منافس: السعر والحجم والإيرادات وما يقوله العملاء.",
    "The market": "السوق",
    "Size, growth, adoption and the rules that apply, with our capacity to deliver.": "الحجم والنمو والتبني والقواعد السارية، مع قدرتنا على التنفيذ.",
    "Demand and price room": "الطلب ومساحة السعر",
    "What customers search for and complain about, and where competitors price.": "ما يبحث عنه العملاء وما يشتكون منه، وأين يسعّر المنافسون.",
    "Frame each option as a competitive response: how the company positions against the named competitors (for example lead, match, differentiate, or hold and watch).": "صِغ كل خيار كاستجابة تنافسية: كيف تتموضع الشركة أمام المنافسين المذكورين (مثل التقدّم، أو المجاراة، أو التمايز، أو الانتظار والمراقبة).",
    "Frame each option as an entry path: when and how to enter (for example enter now, enter through a partner or a pilot, or wait for a condition).": "صِغ كل خيار كمسار دخول: متى وكيف يتم الدخول (مثل الدخول الآن، أو عبر شريك أو تجربة، أو الانتظار حتى يتحقق شرط).",
    "Frame each option as a launch choice: what to launch, for whom and at what price position (for example launch broadly, launch a narrow first version, or validate demand first).": "صِغ كل خيار كقرار إطلاق: ماذا يُطلق، ولمن، وبأي موقع سعري (مثل الإطلاق على نطاق واسع، أو إطلاق نسخة أولى ضيقة، أو التحقق من الطلب أولًا).",
    "Searched and not found.": "بُحث عنه ولم يُعثر عليه.",
    "Not publicly disclosed.": "غير معلن للعامة.",
    "Only third-party sources; this figure needs the company's own page.": "مصادر خارجية فقط؛ هذا الرقم يحتاج صفحة الشركة نفسها.",
    "Too little data behind it to show safely.": "البيانات وراءه قليلة جدًا بحيث لا يُعرض بأمان.",
    "Sources disagree and none is authoritative.": "المصادر متعارضة ولا مصدر حاسم.",
    "Found, but the number could not be matched to its saved source.": "وُجد الرقم، لكن تعذرت مطابقته بمصدره المحفوظ.",
    "Not charted together: the sources define the figure differently.": "لا تُرسم معًا: المصادر تعرّف الرقم بشكل مختلف.",
    "Not shown as a chart: the parts add up to more than 100%.": "لا يُعرض كرسم: الأجزاء تتجاوز 100٪.",
    "Only this part was found, so it is shown on its own, not as a share of a whole.": "وُجد هذا الجزء فقط، لذا يُعرض وحده لا كنصيب من الكل.",
    "Incomplete: only the parts found are named. The rest is Others and unknown, computed as 100% minus what was found.": "غير مكتمل: تُذكر الأجزاء التي وُجدت فقط. الباقي «أخرى وغير معروف»، ويُحسب بطرح ما وُجد من 100٪.",
    "Others and unknown": "أخرى وغير معروف",
}

VISUAL_AR = {
    "kpi": "مؤشر",
    "ladder": "سلّم",
    "segmented": "تقسيم",
    "line": "خط",
    "timeline": "خط زمني",
    "text": "نص",
}

_PERIODS = re.compile(r"Not charted together: the figures cover different periods \((.+)\)\.")
_UNITS = re.compile(r"Not charted together: the figures are in different units \((.+)\)\.")
_FEW_ITEMS = re.compile(r"Not shown as a chart: fewer than (\d+) items behind it\.")
_FEW_POINTS = re.compile(r"Not shown as a chart: fewer than (\d+) data points\.")
_VISUAL = re.compile(r"([A-Za-z]+) is the fixed visual for (.+)\.")
_DISAGREE = re.compile(r"Sources disagree on (.+) for (.*)")
_REPORTED = re.compile(
    r"Reported from (.+?) to (.+?)(?: for (.+))?\. Neither source is authoritative, so no single value is used\."
)
_FROM_SOURCES = re.compile(r"From (\d+) sources\.")
_CONNECT = re.compile(r"(\d+) signal\(s\) connect findings across workers\.")


@lru_cache(maxsize=1)
def _registry() -> tuple[tuple[tuple[str, str], ...], dict[str, str]]:
    from edrak.metrics.registry import get_registry

    reg = get_registry()
    names = []
    definitions: dict[str, str] = {}
    for metric in reg.metrics.values():
        en, ar = metric.label("en"), metric.label("ar")
        if en and ar and en != ar:
            names.append((en, ar))
        if metric.definition and metric.definition_ar:
            definitions[metric.definition] = metric.definition_ar
    names.sort(key=lambda pair: len(pair[0]), reverse=True)
    return tuple(names), definitions


def _metric_ar(name: str) -> str | None:
    folded = name.casefold()
    for en, ar in _registry()[0]:
        if en.casefold() == folded:
            return ar
    return None


def _named_figure(text: str) -> str | None:
    for en, ar in _registry()[0]:
        if text == en:
            return ar
        prefix = f"{en} · "
        if text.startswith(prefix):
            return f"{ar} · {text[len(prefix):]}"
    return None


def fixed_arabic(text: str) -> str | None:
    """The hardcoded Arabic for a product sentence, or None when the model should see it."""
    raw = text.strip()
    if not raw:
        return None
    if raw in EXACT:
        return EXACT[raw]
    named = _named_figure(raw)
    if named:
        return named
    definitions = _registry()[1]
    if raw in definitions:
        return definitions[raw]
    if match := _PERIODS.fullmatch(raw):
        return f"لا تُرسم معًا: الأرقام تغطي فترات مختلفة ({match.group(1)})."
    if match := _UNITS.fullmatch(raw):
        return f"لا تُرسم معًا: الأرقام بوحدات مختلفة ({match.group(1)})."
    if match := _FEW_ITEMS.fullmatch(raw):
        return f"لا يُعرض كرسم: أقل من {match.group(1)} من العناصر وراءه."
    if match := _FEW_POINTS.fullmatch(raw):
        return f"لا يُعرض كرسم: أقل من {match.group(1)} من نقاط البيانات."
    if match := _VISUAL.fullmatch(raw):
        visual = VISUAL_AR.get(match.group(1))
        if visual:
            metric = _metric_ar(match.group(2)) or match.group(2)
            return f"{visual} هو الشكل الثابت لـ {metric}."
    if match := _DISAGREE.fullmatch(raw):
        metric = _metric_ar(match.group(1)) or match.group(1)
        return f"تتعارض المصادر حول {metric} لـ {match.group(2)}"
    if match := _REPORTED.fullmatch(raw):
        low, high, period = match.group(1), match.group(2), match.group(3)
        if period:
            return f"أُبلغ من {low} إلى {high} عن {period}. لا مصدر منهما حاسم، لذا لا يُعتمد رقم واحد."
        return f"أُبلغ من {low} إلى {high}. لا مصدر منهما حاسم، لذا لا يُعتمد رقم واحد."
    if match := _FROM_SOURCES.fullmatch(raw):
        return f"من {match.group(1)} مصادر."
    if match := _CONNECT.fullmatch(raw):
        n = int(match.group(1))
        if n == 1:
            return "إشارة واحدة تربط النتائج عبر الوكلاء."
        return f"{n} إشارات تربط النتائج عبر الوكلاء."
    return None
