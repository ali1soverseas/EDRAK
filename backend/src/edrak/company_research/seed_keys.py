"""The canonical fact keys every database starts with, and the function that puts them there.

Each key has an English and an Arabic label and the other names a company or a model might call it.
Synonyms are matched after normalization (case, Arabic spelling variants, punctuation, plurals), so
list a phrase once in the spelling a person would write it. Within a category no phrase may belong to
two keys; a test enforces that.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from edrak.company_research.models import FactKeySpec, Synonyms, ValueType

_T, _N, _D, _U, _L = ValueType.TEXT, ValueType.NUMBER, ValueType.DATE, ValueType.URL, ValueType.LIST


def _k(category: str, key: str, label_en: str, label_ar: str, en: Iterable[str], ar: Iterable[str], value_type: ValueType = _T) -> FactKeySpec:
    return FactKeySpec(
        category=category, key=key, label_en=label_en, label_ar=label_ar,
        synonyms=Synonyms(en=list(en), ar=list(ar)), value_type=value_type,
    )


SEED_KEYS: tuple[FactKeySpec, ...] = (
    # ---- leadership
    _k("leadership", "ceo", "Chief executive officer", "الرئيس التنفيذي",
       ["ceo", "chief executive", "chief executive officer", "president and ceo"],
       ["المدير التنفيذي", "الرئيس التنفيذي للشركة", "الرئيس التنفيذي للمجموعة"]),
    _k("leadership", "managing_director", "Managing director", "العضو المنتدب",
       ["md", "managing director", "general manager", "gm"],
       ["المدير العام", "المدير الإداري", "العضو المنتدب للشركة"]),
    _k("leadership", "founder", "Founder", "المؤسس",
       ["founder", "co-founder", "cofounder", "founded by", "founding partner"],
       ["مؤسس الشركة", "الشريك المؤسس", "المؤسس المشارك", "أسسها"]),
    _k("leadership", "chairman", "Chairman", "رئيس مجلس الإدارة",
       ["chair", "chairman", "chairperson", "chairman of the board", "board chair"],
       ["رئيس المجلس", "رئيس مجلس إدارة الشركة"]),
    _k("leadership", "board_member", "Board member", "عضو مجلس الإدارة",
       ["board member", "board director", "non-executive director", "director"],
       ["أعضاء مجلس الإدارة", "عضو المجلس", "عضو مجلس إدارة"]),
    _k("leadership", "cfo", "Chief financial officer", "المدير المالي",
       ["cfo", "chief financial officer", "finance director", "head of finance"],
       ["الرئيس المالي", "رئيس القطاع المالي", "مدير الشؤون المالية"]),
    _k("leadership", "cto", "Chief technology officer", "المدير التقني",
       ["cto", "chief technology officer", "chief technical officer", "head of technology"],
       ["الرئيس التقني", "رئيس التقنية", "مدير التقنية"]),
    _k("leadership", "coo", "Chief operating officer", "مدير العمليات",
       ["coo", "chief operating officer", "head of operations"],
       ["الرئيس التشغيلي", "المدير التشغيلي", "رئيس العمليات"]),
    _k("leadership", "executive_team_member", "Executive team member", "عضو الإدارة التنفيذية",
       ["executive", "executive team", "management team", "senior management", "c-suite", "vice president", "vp"],
       ["الإدارة التنفيذية", "الإدارة العليا", "فريق الإدارة", "نائب الرئيس"]),

    # ---- ownership_and_funding
    _k("ownership_and_funding", "shareholder", "Shareholder", "المساهم",
       ["shareholder", "owner", "owned by", "major shareholder", "stakeholder", "ownership"],
       ["مساهم رئيسي", "المساهمون", "مالك الشركة", "الملاك", "هيكل الملكية"]),
    _k("ownership_and_funding", "parent_company", "Parent company", "الشركة الأم",
       ["parent", "parent company", "holding company", "group", "subsidiary of", "part of"],
       ["الشركة القابضة", "المجموعة الأم", "تابعة لشركة", "تابعة لمجموعة"]),
    _k("ownership_and_funding", "subsidiary", "Subsidiary", "شركة تابعة",
       ["subsidiary", "affiliate", "sister company", "group company"],
       ["الشركات التابعة", "شركة شقيقة", "الشركات الشقيقة", "شركة زميلة"]),
    _k("ownership_and_funding", "investor", "Investor", "المستثمر",
       ["investor", "backed by", "backer", "venture capital", "vc", "investing firm"],
       ["مستثمر", "بدعم من", "مدعومة من", "صندوق استثمار"]),
    _k("ownership_and_funding", "funding_round", "Funding round", "جولة تمويل",
       ["funding round", "round", "series a", "series b", "series c", "seed round", "raised"],
       ["جولة استثمارية", "جولة تمويلية", "حصلت على تمويل"]),
    _k("ownership_and_funding", "total_funding", "Total funding", "إجمالي التمويل",
       ["total funding", "total raised", "funding to date", "total capital raised", "funding raised"],
       ["حجم التمويل", "إجمالي التمويل المجمع", "التمويل الإجمالي"], _N),
    _k("ownership_and_funding", "valuation", "Valuation", "التقييم",
       ["valuation", "company valuation"],
       ["تقييم الشركة", "قيمة الشركة"], _N),
    _k("ownership_and_funding", "stock_listing", "Stock listing", "الإدراج في البورصة",
       ["listed on", "stock exchange", "ticker", "stock symbol", "ipo", "listing"],
       ["البورصة", "رمز السهم", "الطرح العام", "مدرجة في البورصة"]),

    # ---- financials
    _k("financials", "annual_revenue", "Annual revenue", "الإيرادات السنوية",
       ["revenue", "annual revenue", "total revenue", "net revenue", "sales", "turnover"],
       ["الإيرادات", "إجمالي الإيرادات", "المبيعات", "حجم المبيعات", "رقم الأعمال"], _N),
    _k("financials", "net_profit", "Net profit", "صافي الربح",
       ["net profit", "net income", "profit", "earnings"],
       ["الأرباح", "صافي الدخل", "الربح الصافي"], _N),
    _k("financials", "total_assets", "Total assets", "إجمالي الأصول",
       ["total assets", "assets"],
       ["الأصول", "أصول الشركة"], _N),
    _k("financials", "ebitda", "EBITDA", "الأرباح قبل الفوائد والضرائب والاستهلاك",
       ["ebitda", "adjusted ebitda"],
       ["الأرباح قبل الفوائد والضرائب والإهلاك والاستهلاك"], _N),
    _k("financials", "revenue_growth", "Revenue growth", "نمو الإيرادات",
       ["revenue growth", "growth rate", "yoy growth", "year over year growth", "sales growth"],
       ["معدل النمو", "نمو المبيعات", "النمو السنوي"]),
    _k("financials", "market_capitalization", "Market capitalization", "القيمة السوقية",
       ["market cap", "market capitalization", "market capitalisation"],
       ["رأس المال السوقي", "القيمة السوقية للشركة"], _N),
    _k("financials", "share_capital", "Share capital", "رأس المال",
       ["authorized capital", "paid-in capital", "issued capital", "share capital", "capital"],
       ["رأس المال المدفوع", "رأس المال المصدر", "رأس المال المرخص"], _N),
    _k("financials", "annual_report", "Annual report", "التقرير السنوي",
       ["annual report", "financial report", "financial statements", "investor presentation"],
       ["التقارير المالية", "القوائم المالية", "تقرير الاستدامة"], _U),

    # ---- customers
    _k("customers", "customer_name", "Customer", "عميل",
       ["customer", "client", "key clients", "notable customers", "our clients", "trusted by", "reference customers"],
       ["العملاء", "عملاؤنا", "من عملائنا", "يثق بنا"]),
    _k("customers", "customer_count", "Number of customers", "عدد العملاء",
       ["number of customers", "customer count", "clients served", "users", "active users", "merchants", "subscribers"],
       ["عدد المستخدمين", "عدد المشتركين", "عدد التجار"], _N),
    _k("customers", "customer_segment", "Customer segment", "شريحة العملاء",
       ["customer segment", "target customers", "target market", "industries served", "sectors served", "customer types"],
       ["شرائح العملاء", "فئات العملاء", "القطاعات التي نخدمها", "القطاعات المستهدفة", "العملاء المستهدفون"], _L),
    _k("customers", "case_study", "Case study", "دراسة حالة",
       ["case study", "success story", "testimonial"],
       ["قصة نجاح", "قصص النجاح", "شهادة عميل"]),

    # ---- partners
    _k("partners", "partner", "Partner", "شريك",
       ["partner", "strategic partner", "technology partner", "partnership", "alliance"],
       ["الشركاء", "شركاؤنا", "شريك استراتيجي", "شراكة", "شركاء النجاح"]),
    _k("partners", "distributor", "Distributor", "موزع",
       ["distributor", "reseller", "channel partner", "dealer"],
       ["الموزعون", "وكيل", "الوكلاء", "موزعون معتمدون", "شريك قنوات"]),
    _k("partners", "supplier", "Supplier", "مورد",
       ["supplier", "vendor", "manufacturer partner"],
       ["الموردون", "موردين"]),

    # ---- locations
    _k("locations", "headquarters", "Headquarters", "المقر الرئيسي",
       ["headquarters", "hq", "head office", "main office", "registered office", "corporate office"],
       ["المركز الرئيسي", "المقر", "المكتب الرئيسي", "الإدارة العامة"]),
    _k("locations", "branch", "Branch", "فرع",
       ["branch", "office", "regional office", "store", "showroom"],
       ["الفروع", "مكتب", "المكاتب", "مكتب إقليمي", "معرض"]),
    _k("locations", "factory", "Factory", "مصنع",
       ["factory", "plant", "manufacturing plant", "production facility", "facility", "mill"],
       ["المصانع", "منشأة إنتاج", "مصنع الإنتاج", "مجمع صناعي"]),
    _k("locations", "warehouse", "Warehouse", "مستودع",
       ["warehouse", "distribution center", "logistics hub", "fulfillment center"],
       ["المستودعات", "مركز توزيع", "مخزن", "مركز لوجستي"]),
    _k("locations", "country_presence", "Countries of operation", "الدول التي نعمل بها",
       ["countries", "operates in", "presence", "countries served", "geographic presence", "footprint"],
       ["الدول", "نعمل في", "التواجد الجغرافي", "الانتشار الجغرافي", "الأسواق"], _L),
    _k("locations", "address", "Address", "العنوان",
       ["address", "contact address", "postal address"],
       ["عنوان الشركة", "العنوان البريدي"]),

    # ---- team
    _k("team", "employee_count", "Number of employees", "عدد الموظفين",
       ["employees", "number of employees", "headcount", "staff size", "team size", "workforce", "number of staff"],
       ["عدد العاملين", "عدد أفراد الفريق", "القوى العاملة", "حجم الفريق", "الموظفين"], _N),
    _k("team", "team_member", "Team member", "عضو الفريق",
       ["team member", "staff", "key personnel", "expert", "engineer"],
       ["أعضاء الفريق", "فريق العمل", "الكادر"]),
    _k("team", "department", "Department", "قسم",
       ["department", "division", "business unit"],
       ["الأقسام", "إدارة", "القطاعات", "وحدة أعمال", "وحدات الأعمال"]),
    _k("team", "open_positions", "Open positions", "الوظائف الشاغرة",
       ["open positions", "careers", "hiring", "job openings", "vacancies"],
       ["وظائف", "فرص العمل", "التوظيف"]),

    # ---- pricing
    _k("pricing", "pricing_plan", "Pricing plan", "خطة الأسعار",
       ["pricing plan", "plan", "pricing tier", "subscription plan", "package", "tier"],
       ["الباقات", "باقة", "خطط الاشتراك", "خطة الاشتراك", "باقات الأسعار"]),
    _k("pricing", "price", "Price", "السعر",
       ["price", "pricing", "cost", "rate", "starting price", "starts at", "fee"],
       ["الأسعار", "التسعير", "التكلفة", "يبدأ من", "ابتداء من"]),
    _k("pricing", "free_trial", "Free trial", "تجربة مجانية",
       ["free trial", "trial", "free tier", "freemium", "free plan", "demo"],
       ["فترة تجريبية", "الباقة المجانية", "نسخة مجانية", "نسخة تجريبية"]),
    _k("pricing", "discount", "Discount", "خصم",
       ["discount", "promotion", "promo", "offer", "special offer"],
       ["الخصومات", "عرض", "العروض", "عرض خاص"]),
    _k("pricing", "billing_model", "Billing model", "نموذج التسعير",
       ["billing model", "billing", "subscription", "pay as you go", "payment model", "per user pricing", "license fee"],
       ["نظام الدفع", "الاشتراك", "الدفع حسب الاستخدام", "رسوم الاشتراك"]),
    _k("pricing", "transaction_fee", "Transaction fee", "رسوم المعاملات",
       ["transaction fee", "commission", "processing fee"],
       ["العمولة", "رسوم المعالجة", "رسوم التحويل"]),

    # ---- technology
    _k("technology", "tech_stack", "Technology stack", "التقنيات المستخدمة",
       ["technology stack", "tech stack", "technologies", "built with", "programming languages", "frameworks"],
       ["البنية التقنية", "لغات البرمجة", "التقنيات", "المكدس التقني"], _L),
    _k("technology", "platform", "Platform", "المنصة",
       ["platform", "software platform", "product platform", "system", "solution"],
       ["منصتنا", "النظام", "نظام"]),
    _k("technology", "integration", "Integration", "التكامل",
       ["integration", "api", "connector", "plugin"],
       ["التكاملات", "الربط", "واجهة برمجة التطبيقات", "الإضافات"], _L),
    _k("technology", "infrastructure", "Infrastructure", "البنية التحتية",
       ["cloud provider", "hosting", "cloud", "infrastructure", "data center", "aws", "azure"],
       ["مزود الخدمة السحابية", "الاستضافة", "السحابة", "مركز البيانات"]),
    _k("technology", "patent", "Patent", "براءة اختراع",
       ["patent", "intellectual property", "ip", "patented"],
       ["براءات الاختراع", "الملكية الفكرية"]),
    _k("technology", "ai_capability", "AI capability", "قدرات الذكاء الاصطناعي",
       ["ai", "artificial intelligence", "machine learning", "ml", "ai features"],
       ["الذكاء الاصطناعي", "التعلم الآلي", "تعلم الآلة", "ميزات الذكاء الاصطناعي"]),

    # ---- certifications
    _k("certifications", "iso_certification", "ISO certification", "شهادة الأيزو",
       ["iso", "iso 9001", "iso 14001", "iso 27001", "iso certification", "iso certified"],
       ["أيزو", "شهادة أيزو", "ايزو", "شهادات الأيزو"]),
    _k("certifications", "certification", "Certification", "شهادة",
       ["certification", "certified", "accreditation", "accredited", "certificate"],
       ["الشهادات", "اعتماد", "الاعتمادات", "الاعتماد", "معتمد"]),
    _k("certifications", "quality_standard", "Quality standard", "معيار الجودة",
       ["quality", "quality standard", "quality management", "quality assurance", "standard"],
       ["معايير الجودة", "الجودة", "إدارة الجودة", "ضمان الجودة", "المواصفات القياسية"]),
    _k("certifications", "security_certification", "Security certification", "شهادة الأمن",
       ["soc 2", "soc2", "pci dss", "security certification", "information security"],
       ["شهادة الأمن السيبراني", "أمن المعلومات", "معيار أمن بيانات الدفع"]),
    _k("certifications", "professional_membership", "Professional membership", "عضوية مهنية",
       ["membership", "member of", "association", "chamber of commerce", "industry association"],
       ["عضوية", "عضو في", "الاتحاد", "الغرفة التجارية", "اتحاد الصناعات", "جمعية"]),

    # ---- regulatory
    _k("regulatory", "license", "License", "ترخيص",
       ["license", "licence", "licensed", "operating license", "business license", "trade license"],
       ["التراخيص", "رخصة", "الرخصة", "رخصة تشغيل", "مرخصة", "مرخص"]),
    _k("regulatory", "regulator", "Regulator", "الجهة التنظيمية",
       ["regulator", "regulated by", "supervised by", "regulatory body", "authority", "central bank"],
       ["الجهة الرقابية", "تحت إشراف", "يخضع لرقابة", "البنك المركزي", "هيئة الرقابة المالية"]),
    _k("regulatory", "commercial_registration", "Commercial registration", "السجل التجاري",
       ["commercial registration", "commercial register", "cr number", "registration number", "company registration", "trade register"],
       ["رقم السجل التجاري", "رقم التسجيل", "تسجيل الشركة"]),
    _k("regulatory", "tax_id", "Tax ID", "الرقم الضريبي",
       ["tax id", "tax number", "tax registration", "vat number", "tax card"],
       ["رقم التسجيل الضريبي", "البطاقة الضريبية"]),
    _k("regulatory", "compliance", "Compliance", "الامتثال",
       ["compliance", "regulatory compliance", "aml", "kyc", "data protection", "gdpr"],
       ["الالتزام التنظيمي", "مكافحة غسل الأموال", "حماية البيانات", "الخصوصية"]),
    _k("regulatory", "legal_name", "Legal name", "الاسم القانوني",
       ["legal name", "registered name", "trade name"],
       ["الاسم التجاري", "الاسم المسجل"]),
    _k("regulatory", "legal_form", "Legal form", "الشكل القانوني",
       ["legal form", "legal entity type", "company type", "joint stock", "llc"],
       ["نوع الشركة", "شركة مساهمة", "ذات مسؤولية محدودة"]),

    # ---- news_and_events
    _k("news_and_events", "press_release", "Press release", "بيان صحفي",
       ["press release", "announcement", "announced", "news", "latest news", "media release"],
       ["الأخبار", "آخر الأخبار", "إعلان", "نشرة صحفية", "خبر"]),
    _k("news_and_events", "event", "Event", "فعالية",
       ["event", "conference", "exhibition", "trade show", "webinar", "summit", "upcoming events"],
       ["الفعاليات", "مؤتمر", "معرض", "ندوة", "قمة"]),
    _k("news_and_events", "product_launch", "Product launch", "إطلاق منتج",
       ["product launch", "launch", "launched", "new product", "release", "new release"],
       ["إطلاق", "أطلقت", "منتج جديد", "إصدار جديد"]),
    _k("news_and_events", "media_mention", "Media mention", "ظهور إعلامي",
       ["media mention", "press coverage", "featured in", "in the news", "as seen in"],
       ["تغطية إعلامية", "ذكرت في", "نشر في"]),
    _k("news_and_events", "expansion", "Expansion", "التوسع",
       ["expansion", "expands", "new market", "opens new", "opening new branch"],
       ["توسع", "توسعة", "افتتاح فرع جديد", "دخول سوق جديد"]),

    # ---- awards
    _k("awards", "award", "Award", "جائزة",
       ["award", "prize", "winner", "won", "awarded", "trophy"],
       ["الجوائز", "جوائز", "فوز", "فازت", "حاصلة على جائزة"]),
    _k("awards", "ranking", "Ranking", "تصنيف",
       ["ranking", "ranked", "rank", "top companies", "fortune 500", "forbes list", "best places to work"],
       ["ترتيب", "أفضل الشركات", "قائمة فورتشن", "قائمة فوربس"]),
    _k("awards", "recognition", "Recognition", "تقدير",
       ["recognition", "recognized as", "recognised as", "accolade", "honour", "honor", "honoree", "named"],
       ["شهادة تقدير", "تكريم", "إشادة"]),

    # ---- history
    _k("history", "founded_year", "Year founded", "سنة التأسيس",
       ["founded", "founding year", "year founded", "established", "established in", "since", "est", "year of establishment", "inception"],
       ["عام التأسيس", "تأسست عام", "تأسست في", "منذ", "تأسست"], _N),
    _k("history", "founding_date", "Date founded", "تاريخ التأسيس",
       ["founding date", "date founded", "date of establishment", "incorporation date", "founded on"],
       ["تاريخ تأسيس الشركة", "تاريخ التسجيل"], _D),
    _k("history", "founding_story", "Founding story", "قصة التأسيس",
       ["founding story", "origin story", "company story", "our story", "how we started", "background", "story"],
       ["قصتنا", "نبذة تاريخية", "كيف بدأنا", "قصة الشركة", "خلفية الشركة"]),
    _k("history", "milestone", "Milestone", "محطة بارزة",
       ["milestone", "timeline", "key dates", "company history", "achievement"],
       ["محطات", "أهم المحطات", "المحطات الرئيسية", "الجدول الزمني", "تاريخ الشركة", "إنجازات"]),
    _k("history", "former_name", "Former name", "الاسم السابق",
       ["formerly", "former name", "previously known as", "previous name", "rebranded from"],
       ["سابقا", "كانت تعرف سابقا باسم", "الاسم القديم"]),
    _k("history", "acquisition", "Acquisition or merger", "استحواذ أو اندماج",
       ["acquisition", "acquired", "merger", "acquired by", "acquires", "merged with", "m&a"],
       ["استحواذ", "الاستحواذ", "اندماج", "الاندماج", "استحوذت على", "استحوذت"]),
)


def seed_fact_keys(conn: sqlite3.Connection) -> int:
    """Insert the seed keys that are missing. Keys already there, seeded or promoted, are left as they are.

    Returns how many were added. The caller commits.
    """
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO fact_keys (category, key, label_en, label_ar, synonyms, value_type, origin)
        VALUES (?, ?, ?, ?, ?, ?, 'seed')
        """,
        [
            (k.category, k.key, k.label_en, k.label_ar, json.dumps(k.synonyms.model_dump(), ensure_ascii=False), k.value_type.value)
            for k in SEED_KEYS
        ],
    )
    return conn.total_changes - before
