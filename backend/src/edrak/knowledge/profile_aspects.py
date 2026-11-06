"""The comprehensive competitor profile: 14 aspects, each a list of items to establish.

One catalog, used by three parts of EDRAK:

  * the knowledge-base build researches every aspect and records, per item, the facts found
    (each with a quote checked against its page) or that nothing was found;
  * the competitor agent maps each research requirement to an aspect, answers it from the stored
    facts first, and searches only for the items the profile lacks;
  * the profile page shows every aspect, what is known and what is not.

Each aspect says which sentences are relevant to it (`keywords`), which stored page types tend to
hold it, which searches find it (on the official site and on the open web), and how long its facts
stay current (`fresh_days`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Aspect:
    id: str
    label: str
    items: tuple[tuple[str, str], ...]                 # (item id, what to establish)
    keywords: str                                      # regex: a sentence relevant to this aspect
    page_types: tuple[str, ...] = ()                   # stored page types that usually hold it
    official_queries: tuple[str, ...] = ()             # "site:{domain} ..." searches
    web_queries: tuple[str, ...] = ()                  # open-web searches ({name}, {scope}, {market}, {year})
    fresh_days: int = 365
    allow_review_sites: bool = False                   # customer experience reads review sites
    requirement_categories: tuple[str, ...] = ()       # older requirement categories that map here
    pattern: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pattern", re.compile(self.keywords, re.I))

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(i for i, _ in self.items)

    def item_label(self, item_id: str) -> str:
        return dict(self.items).get(item_id, item_id)


ASPECTS: tuple[Aspect, ...] = (
    Aspect(
        "company_overview", "Company overview and business model",
        (("background", "Company background and history"),
         ("headquarters", "Headquarters and geographical presence"),
         ("founding_and_size", "Founding year and company size"),
         ("core_activities", "Core business activities"),
         ("business_model", "Business model (B2B, B2C, B2B2C, SaaS, marketplace...)"),
         ("revenue_streams", "Main revenue streams"),
         ("target_industries", "Target industries and customer segments"),
         ("business_units", "Strategic business units"),
         ("subsidiaries_acquisitions", "Subsidiaries and acquisitions"),
         ("markets_served", "Geographic markets served")),
        r"\b(founded|established|headquarter|based in|employ|workforce|subsidiar|acquir|business unit|division|"
        r"segment|b2b|b2c|saas|marketplace|revenue stream|offices in|operat(es|ions) in|history|industr|"
        r"customers? (in|across)|serves?)\w*|تأسس|المقر|موظف",
        ("about", "home", "news"),
        ("site:{domain} about company history", "site:{domain} investor relations business segments"),
        ("{name} company overview business model segments", "{name} subsidiaries acquisitions history"),
        fresh_days=730, requirement_categories=("other",),
    ),
    Aspect(
        "products_services", "Products and services",
        (("portfolio", "Complete product and service portfolio"),
         ("descriptions", "Product descriptions and main functionalities"),
         ("categories", "Product categories and segments"),
         ("versions_tiers", "Product versions, tiers and editions"),
         ("new_launches", "Newly launched products"),
         ("discontinued", "Discontinued products"),
         ("differentiation", "Product differentiation"),
         ("availability", "Product maturity and availability"),
         ("bundles", "Bundled offerings"),
         ("roadmap", "Product roadmap and announced launches")),
        r"\b(product|portfolio|platform|edition|tier|version|launch|introduc|unveil|discontinu|end of life|eol|"
        r"generally available|ga\b|preview|beta|bundle|roadmap|feature)\w*|منتج|خدمة|إطلاق",
        ("product", "home", "pricing", "news"),
        ("site:{domain} {scope} products", "site:{domain} new product launch announcement"),
        ("{name} {scope} product lineup {year}", "{name} {scope} discontinued end of life"),
        fresh_days=180, requirement_categories=("products",),
    ),
    Aspect(
        "technology", "Technology and technical capabilities",
        (("core_technology", "Core technologies and technical architecture"),
         ("ai_ml", "AI and machine learning capabilities"),
         ("infrastructure", "Infrastructure and deployment architecture"),
         ("integrations", "Integrations and supported platforms"),
         ("apis", "APIs and developer tools"),
         ("automation", "Automation capabilities"),
         ("performance", "Performance and scalability"),
         ("security", "Security architecture"),
         ("data_privacy", "Data privacy and data management"),
         ("certifications", "Certifications and technical standards"),
         ("patents", "Proprietary technologies and patents"),
         ("limitations", "Technical limitations and dependencies")),
        r"\b(architecture|ai\b|machine learning|model|api|sdk|developer|integrat|automat|throughput|latency|"
        r"tbps|gbps|scal|performance|encrypt|security|privacy|gdpr|iso ?\d+|soc ?2|certif|patent|proprietary|"
        r"limitation|requires|depend|compatib|supported platform)\w*",
        ("product", "help", "other"),
        ("site:{domain} {scope} technical documentation", "site:{domain} security compliance certifications"),
        ("{name} {scope} technical specifications", "{name} patents proprietary technology"),
        fresh_days=365, requirement_categories=("technical_capabilities",),
    ),
    Aspect(
        "pricing_commercial", "Pricing and commercial strategy",
        (("public_prices", "Public product prices"),
         ("tier_pricing", "Pricing by product tier"),
         ("subscriptions", "Subscription plans"),
         ("usage_pricing", "Usage-based pricing"),
         ("enterprise_pricing", "Enterprise pricing"),
         ("licensing", "Licensing models"),
         ("trials_freemium", "Free trials and freemium offerings"),
         ("discounts", "Discounts and promotional offers"),
         ("onboarding_fees", "Implementation and onboarding fees"),
         ("service_fees", "Additional service fees"),
         ("contract_terms", "Contract duration and payment terms"),
         ("renewal_cancellation", "Renewal and cancellation conditions"),
         ("price_changes", "Price changes over time"),
         ("quote_or_undisclosed", "Quote-required or undisclosed pricing")),
        r"\b(price|pricing|cost|fee|per (core|seat|user|month|year|unit)|subscription|licen[cs]|free trial|freemium|"
        r"discount|promo|onboarding|implementation fee|contract|payment terms|renewal|cancel|quote|msrp)\w*|سعر|اسعار|رسوم|اشتراك",
        ("pricing", "sales", "policy"),
        ("site:{domain} {scope} pricing licensing", "site:{domain} terms renewal cancellation payment"),
        ("{name} {scope} price per", "{name} pricing change {year}"),
        fresh_days=90, requirement_categories=("pricing",),
    ),
    Aspect(
        "customers_market", "Customers and target market",
        (("profiles", "Target customer profiles"),
         ("segments", "Customer segments"),
         ("major_customers", "Major enterprise customers"),
         ("industries", "Customer industries"),
         ("geography", "Customer geographic distribution"),
         ("use_cases", "Customer use cases"),
         ("deployments", "Publicly announced deployments"),
         ("success_stories", "Customer success stories"),
         ("case_studies", "Case studies and measurable outcomes"),
         ("retention_churn", "Customer retention and churn (when disclosed)"),
         ("acquisition_strategy", "Customer acquisition strategy"),
         ("deployment_partners", "Partnerships supporting customer deployments")),
        r"\b(customer|client|deploy|case study|success stor|use case|serves|industr|enterprise|retention|churn|"
        r"selected by|chose|adopt|rolled out)\w*|عملاء|عميل",
        ("about", "news", "product"),
        ("site:{domain} {scope} customers case study", "site:{domain} customer success stories"),
        ("{name} customer deployment {scope}", "{name} selected by customer contract {year}"),
        fresh_days=365, requirement_categories=("customers",),
    ),
    Aspect(
        "market_position", "Market position and competitive landscape",
        (("market_share", "Market share"),
         ("market_size_growth", "Market size and growth"),
         ("ranking", "Market ranking"),
         ("positioning", "Competitive positioning"),
         ("competitors", "Direct and indirect competitors"),
         ("segments_served", "Market segments served"),
         ("advantages", "Competitive advantages"),
         ("weaknesses", "Competitive weaknesses"),
         ("differentiation", "Differentiation from competitors"),
         ("brand", "Brand awareness and reputation"),
         ("share_of_voice", "Share of voice"),
         ("expansion", "Market expansion"),
         ("threats", "Emerging competitive threats")),
        r"\b(market share|share of|leader|leading|largest|ranked|ranking|number one|#1|competit|rival|versus|vs\.?|"
        r"position|advantage|weakness|differentiat|brand|expan|threat|gartner|idc|forrester)\w*|حصة سوقية|منافس",
        ("about", "news", "other"),
        ("site:{domain} {scope} market leader",),
        ("{name} market share {scope} {year}", "{name} competitors comparison {scope}"),
        fresh_days=180, requirement_categories=("market_position",),
    ),
    Aspect(
        "financials", "Financial performance",
        (("total_revenue", "Total revenue"),
         ("segment_revenue", "Revenue by business segment"),
         ("revenue_growth", "Revenue growth"),
         ("margins", "Gross margin and operating margin"),
         ("profitability", "Profitability"),
         ("arr", "Annual recurring revenue (ARR), where applicable"),
         ("funding", "Funding and investment"),
         ("valuation", "Valuation (private companies, when available)"),
         ("guidance", "Financial guidance"),
         ("rnd_spend", "R&D spending"),
         ("capex", "Capital expenditure"),
         ("financial_risks", "Financial risks and dependencies")),
        r"\b(revenue|net income|profit|margin|ebitda|earnings|fiscal|quarter|guidance|arr\b|recurring revenue|"
        r"funding|raised|series [a-f]|valuation|r&d|research and development|capex|capital expenditure|billion|million)\w*|إيرادات|أرباح",
        ("news", "about", "other"),
        ("site:{domain} investor relations annual report revenue", "site:{domain} quarterly results earnings"),
        ("{name} revenue {year} results", "{name} fiscal {year} annual report segment revenue"),
        fresh_days=120, requirement_categories=("revenue",),
    ),
    Aspect(
        "partnerships", "Partnerships and ecosystem",
        (("technology_partners", "Technology partners"),
         ("alliances", "Strategic alliances"),
         ("distribution_partners", "Distribution partners"),
         ("cloud_partners", "Cloud and infrastructure partners"),
         ("resellers", "Resellers and channel partners"),
         ("integration_ecosystem", "Integration ecosystem"),
         ("oem", "OEM relationships"),
         ("joint_ventures", "Joint ventures"),
         ("announcements", "Partnership announcements"),
         ("partner_products", "Partner-supported products"),
         ("ecosystem_maturity", "Ecosystem maturity")),
        r"\b(partner|alliance|collaborat|distributor|reseller|channel|oem|joint venture|ecosystem|integrat|"
        r"marketplace|certified|aws|azure|google cloud)\w*|شراكة|شريك",
        ("about", "news", "sales"),
        ("site:{domain} {scope} partners ecosystem", "site:{domain} partnership announcement"),
        ("{name} {scope} partnership {year}", "{name} {scope} OEM distributors"),
        fresh_days=180, requirement_categories=("partnerships", "ecosystem"),
    ),
    Aspect(
        "go_to_market", "Sales and go-to-market strategy",
        (("channels", "Sales channels"),
         ("direct_sales", "Direct sales strategy"),
         ("partner_led", "Partner-led sales"),
         ("online", "Online sales channels"),
         ("distribution_model", "Distribution model"),
         ("sales_coverage", "Geographic sales coverage"),
         ("acquisition", "Customer acquisition strategy"),
         ("marketing_positioning", "Marketing positioning"),
         ("campaigns", "Promotional campaigns"),
         ("enterprise_sales", "Enterprise sales approach"),
         ("onboarding", "Customer onboarding process"),
         ("support_model", "Customer support model")),
        r"\b(sales|sell|buy|how to buy|distribut|reseller|online store|app|campaign|promot|marketing|"
        r"onboard|support|account manager|go-to-market|channel)\w*|شراء|توصيل|دعم",
        ("sales", "help", "home"),
        ("site:{domain} how to buy contact sales", "site:{domain} support onboarding"),
        ("{name} {scope} go-to-market sales channels", "{name} marketing campaign {year}"),
        fresh_days=365, requirement_categories=(),
    ),
    Aspect(
        "customer_experience", "Customer experience and reputation",
        (("reviews_ratings", "Customer reviews and ratings"),
         ("satisfaction", "Customer satisfaction"),
         ("complaints", "Common customer complaints"),
         ("pain_points", "Customer pain points"),
         ("positive_feedback", "Positive customer feedback"),
         ("usability", "Product usability"),
         ("support_quality", "Customer support quality"),
         ("reliability", "Service reliability"),
         ("outages", "Publicly reported outages"),
         ("switching", "Switching behavior and reasons"),
         ("loyalty", "Customer loyalty"),
         ("sentiment", "Public sentiment")),
        r"\b(review|rating|stars?|rated|complain|satisf|frustrat|praise|easy to use|usabilit|support team|"
        r"outage|downtime|incident|switch(ed|ing)|migrat|loyal|sentiment|nps)\w*|تقييم|شكوى",
        ("other", "news"),
        (),
        ("{name} {scope} reviews ratings", "{name} {scope} customer complaints", "{name} {scope} outage {year}"),
        fresh_days=180, allow_review_sites=True, requirement_categories=(),
    ),
    Aspect(
        "strategy", "Strategy and future direction",
        (("long_term", "Long-term business strategy"),
         ("priorities", "Strategic priorities"),
         ("expansion", "Expansion plans"),
         ("product_strategy", "Product development strategy"),
         ("ai_strategy", "AI adoption strategy"),
         ("mergers", "Mergers and acquisitions"),
         ("investment_priorities", "Investment priorities"),
         ("new_markets", "New market entry"),
         ("competitive_response", "Competitive response strategies"),
         ("initiatives", "Announced future initiatives"),
         ("leadership_statements", "Leadership statements and strategic commitments")),
        r"\b(strateg|priorit|plan(s|ned)?|expan|vision|roadmap|invest|focus on|aim|commit|initiative|"
        r"ceo said|chief executive|announc|enter(ing)? the)\w*|استراتيجية|خطة",
        ("news", "about"),
        ("site:{domain} strategy investor day", "site:{domain} ceo letter shareholders"),
        ("{name} strategy {year} ceo", "{name} expansion plans new markets"),
        fresh_days=180, requirement_categories=("strategy",),
    ),
    Aspect(
        "operations", "Operations and delivery",
        (("delivery_model", "Service delivery model"),
         ("footprint", "Infrastructure and operational footprint"),
         ("manufacturing", "Manufacturing capabilities, where relevant"),
         ("supply_chain", "Supply chain dependencies"),
         ("availability", "Geographic availability"),
         ("uptime", "Service availability and uptime"),
         ("implementation_time", "Implementation timelines"),
         ("deployment_options", "Deployment options"),
         ("scalability", "Operational scalability"),
         ("slas", "Service-level agreements (SLAs)")),
        r"\b(deliver|shipping|ship|fulfil|warehouse|data cent|facilit|manufactur|fab|foundry|tsmc|supply|"
        r"available in|availability|uptime|sla|implementation|deploy|on-prem|cloud|hybrid|lead time)\w*|شحن|توصيل|مستودع",
        ("policy", "help", "about"),
        ("site:{domain} service level agreement uptime", "site:{domain} shipping delivery availability"),
        ("{name} manufacturing supply chain", "{name} deployment options implementation time"),
        fresh_days=365, requirement_categories=(),
    ),
    Aspect(
        "legal_risk", "Legal, compliance and risk",
        (("regulatory", "Regulatory compliance"),
         ("certifications", "Industry certifications"),
         ("data_protection", "Data protection compliance"),
         ("ip_disputes", "Intellectual property disputes"),
         ("litigation", "Litigation and legal disputes"),
         ("investigations", "Regulatory investigations"),
         ("security_incidents", "Security incidents"),
         ("operational_risks", "Operational risks"),
         ("market_financial_risks", "Market and financial risks"),
         ("contractual", "Contractual commitments")),
        r"\b(complian|regulat|gdpr|iso ?\d+|soc ?2|certif|lawsuit|sued|litigation|court|antitrust|investigat|"
        r"fine|penalt|breach|vulnerab|incident|risk factor|patent infringement|settle)\w*|غرامة|دعوى",
        ("policy", "news", "about"),
        ("site:{domain} compliance certifications trust", "site:{domain} risk factors annual report"),
        ("{name} lawsuit {year}", "{name} regulatory investigation fine", "{name} security breach"),
        fresh_days=365, requirement_categories=(),
    ),
    Aspect(
        "recent_developments", "Recent developments and competitive signals",
        (("announcements", "Latest company announcements"),
         ("product_launches", "Recent product launches"),
         ("pricing_changes", "Recent pricing changes"),
         ("new_customers", "New customer acquisitions"),
         ("new_partnerships", "New partnerships"),
         ("acquisitions_investments", "Acquisitions and investments"),
         ("leadership_changes", "Leadership changes"),
         ("competitor_reactions", "Competitor reactions"),
         ("market_trends", "Market trends affecting the company"),
         ("timeline", "Significant developments over the last 3, 6 and 12 months")),
        r"\b(announc|launch|today|introduc|unveil|acquir|appoint|names? .{0,20}(ceo|cfo|president)|"
        r"steps down|partner|agreement|deal|price (increase|change)|(19|20)\d{2})\w*|أعلن|إطلاق",
        ("news",),
        ("site:{domain} news press release {year}",),
        ("{name} {scope} news {year}", "{name} announces {scope} {year}"),
        fresh_days=90, requirement_categories=(),
    ),
)

BY_ID: dict[str, Aspect] = {a.id: a for a in ASPECTS}

# Aspects about the competitor's products and markets read only what falls in the target company's
# field. Company-level aspects (overview, financials, strategy, operations, legal) read the whole
# company, with the target's field first.
SCOPED_ASPECTS = frozenset({
    "products_services", "technology", "pricing_commercial", "customers_market", "market_position",
    "partnerships", "go_to_market", "customer_experience", "recent_developments",
})


def is_scoped(aspect: Aspect) -> bool:
    return aspect.id in SCOPED_ASPECTS


# ------------------------------------------------------------------ the target company's field


@dataclass
class Scope:
    """The field the target company competes in, as domains with the words that describe them.

    Research on a competitor is about this field: NVIDIA's competitors are researched for AI
    accelerators, data-center networking and AI software, not for everything they sell.
    """

    key: str
    target: str
    domains: list[tuple[str, tuple[str, ...]]]          # (domain name, keywords)
    _patterns: list[tuple[str, re.Pattern]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._patterns = []
        for name, words in self.domains:
            terms = sorted({w.strip().lower() for w in (name, *words) if len(w.strip()) >= 2}, key=len, reverse=True)
            if terms:
                alternation = "|".join(re.escape(t) for t in terms)
                self._patterns.append((name, re.compile(rf"(?<![\w-])(?:{alternation})(?:e?s)?(?![\w])", re.I)))

    def match(self, text: str) -> Optional[str]:
        """The domain the text is about, or None when it is outside the target's field."""
        for name, pattern in self._patterns:
            if pattern.search(text or ""):
                return name
        return None

    def phrase(self, index: int = 0) -> str:
        """A domain name for a search query; successive queries take successive domains."""
        return self.domains[index % len(self.domains)][0] if self.domains else ""

    def as_dict(self) -> dict:
        return {"key": self.key, "target": self.target,
                "domains": [{"name": n, "keywords": list(k)} for n, k in self.domains]}

    @classmethod
    def from_dict(cls, data: dict) -> "Scope":
        return cls(data.get("key", ""), data.get("target", ""),
                   [(d["name"], tuple(d.get("keywords") or ())) for d in data.get("domains") or [] if d.get("name")])


def scope_key(target: str, offerings: list[str], offering: Optional[str] = None) -> str:
    """Stable key for a target company's field: the same company and offerings give the same key."""
    import hashlib

    parts = [(target or "").strip().lower(), *sorted(o.strip().lower() for o in offerings if o and o.strip()),
             (offering or "").strip().lower()]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


_SCOPE_STOP = {"and", "for", "the", "with", "solutions", "solution", "products", "product", "services", "service",
               "platform", "platforms", "systems", "system", "software", "hardware"}


def fallback_scope(target: str, offerings: list[str], offering: Optional[str] = None) -> Scope:
    """Without the model: each offering is a domain, described by itself and its own words."""
    names = [o.strip() for o in [offering or "", *offerings] if o and o.strip()]
    names = list(dict.fromkeys(names))[:8]
    domains = []
    for name in names:
        words = tuple(w for w in re.findall(r"[\w\u0600-\u06ff]{3,}", name.lower()) if w not in _SCOPE_STOP)
        domains.append((name, words))
    return Scope(scope_key(target, offerings, offering), target, domains)

# Older requirement categories -> the aspect that answers them.
CATEGORY_TO_ASPECT: dict[str, str] = {
    "products": "products_services", "technical_capabilities": "technology", "pricing": "pricing_commercial",
    "customers": "customers_market", "partnerships": "partnerships", "ecosystem": "partnerships",
    "revenue": "financials", "market_position": "market_position", "strategy": "strategy",
    "other": "company_overview",
}


def aspect_for(category: Optional[str]) -> Optional[Aspect]:
    """The aspect for a requirement category: an aspect id, or one of the older category names."""
    if not category:
        return None
    return BY_ID.get(category) or BY_ID.get(CATEGORY_TO_ASPECT.get(category, ""))


def relevant_sentences(text: str, aspect: Aspect, name: str = "", min_words: int = 7) -> list[str]:
    """Real sentences (not menu lines) that speak to the aspect."""
    out = []
    for sent in re.split(r"(?<=[.!?؟])\s+|\n", text or ""):
        sent = sent.strip()
        if len(sent.split()) >= min_words and len(sent) <= 700 and aspect.pattern.search(sent):
            out.append(sent)
    return out
