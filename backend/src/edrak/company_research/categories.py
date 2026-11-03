"""The categories a researched company fact is filed under. This is the only place the list is written down.

"other" holds facts whose category the model proposed: the proposed name is kept on the fact so a person
can later promote it to a category of its own.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class Category(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label_en: str
    label_ar: str


OTHER = "other"

CATEGORIES: tuple[Category, ...] = (
    Category(key="leadership", label_en="Leadership", label_ar="القيادة"),
    Category(key="ownership_and_funding", label_en="Ownership and funding", label_ar="الملكية والتمويل"),
    Category(key="financials", label_en="Financials", label_ar="البيانات المالية"),
    Category(key="customers", label_en="Customers", label_ar="العملاء"),
    Category(key="partners", label_en="Partners", label_ar="الشركاء"),
    Category(key="locations", label_en="Locations", label_ar="المواقع"),
    Category(key="team", label_en="Team", label_ar="الفريق"),
    Category(key="pricing", label_en="Pricing", label_ar="الأسعار"),
    Category(key="technology", label_en="Technology", label_ar="التقنية"),
    Category(key="certifications", label_en="Certifications", label_ar="الشهادات والاعتمادات"),
    Category(key="regulatory", label_en="Regulatory", label_ar="التراخيص والامتثال التنظيمي"),
    Category(key="news_and_events", label_en="News and events", label_ar="الأخبار والفعاليات"),
    Category(key="awards", label_en="Awards", label_ar="الجوائز"),
    Category(key="history", label_en="History", label_ar="التاريخ"),
    Category(key=OTHER, label_en="Other", label_ar="أخرى"),
)

CATEGORY_KEYS: tuple[str, ...] = tuple(c.key for c in CATEGORIES)
_BY_KEY = {c.key: c for c in CATEGORIES}


def get_category(key: str) -> Optional[Category]:
    return _BY_KEY.get(key)


def is_category(key: str) -> bool:
    return key in _BY_KEY
