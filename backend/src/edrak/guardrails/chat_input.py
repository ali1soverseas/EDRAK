"""Guardrail on a chat message before Ask Edrak works on it.

A chat is not an analysis form, so three things differ from `check_query`:

* Small talk ("hi", "thanks", "who are you?", "شكرا") is answered at once, without a model call and
  without the length or scope checks, which would otherwise block it.
* A follow-up can be one word ("why?", "and Temu?"), so the minimum length is two characters.
* A question that is declined (off topic, harmful, an injection attempt, too many messages) gets a
  polite reply in the conversation instead of an HTTP error, so the chat keeps going.

Injection, harm, hidden characters and personal-data redaction run exactly as in `check_query`.
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Literal, Optional

from edrak.core.config import settings
from edrak.guardrails.patterns import injection_hits
from edrak.guardrails.query import Check, check_query

CHAT_MIN_CHARS = 2

Lang = Literal["en", "ar"]
_ARABIC = re.compile(r"[؀-ۿ]")


def detect_lang(text: str) -> Lang:
    """Arabic when the message has more Arabic letters than Latin ones."""
    arabic = len(_ARABIC.findall(text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return "ar" if arabic > latin else "en"


def tr(lang: Lang, en: str, ar: str) -> str:
    return ar if lang == "ar" else en


# ------------------------------------------------------------------------------------------- small talk

_SMALL_TALK: list[tuple[str, re.Pattern[str]]] = [
    ("greeting", re.compile(
        r"^(hi|hello|hey|hiya|yo|good (morning|afternoon|evening)|greetings|salam|marhaba|"
        r"مرحبا|مرحبًا|اهلا|أهلا|أهلًا|السلام عليكم|سلام|صباح الخير|مساء الخير|هاي)(\s+(there|edrak|إدراك|ادراك))?[\s!.؟?]*$",
        re.IGNORECASE)),
    ("thanks", re.compile(
        r"^(thanks|thank you|thx|ty|cheers|great,? thanks|perfect|awesome|شكرا|شكرًا|شكرا لك|شكرا جزيلا|متشكر|تسلم|ممتاز)"
        r"([\s,]+(a lot|so much|edrak|إدراك))?[\s!.؟?]*$",
        re.IGNORECASE)),
    ("bye", re.compile(r"^(bye|goodbye|see you|later|مع السلامة|إلى اللقاء|باي)[\s!.]*$", re.IGNORECASE)),
    ("ack", re.compile(r"^(ok|okay|k|got it|cool|sure|nice|great|تمام|حسنا|حسنًا|اوكي|طيب)[\s!.]*$", re.IGNORECASE)),
    ("identity", re.compile(
        r"^(who are you|what are you|what is edrak|what's edrak|مين انت|من أنت|من انت|ما هو إدراك|ايه هو ادراك)[\s?؟!.]*$",
        re.IGNORECASE)),
    ("capabilities", re.compile(
        r"^(what can you do|how can you help( me)?|help|what do you know|what can i ask( you)?|"
        r"ماذا تستطيع أن تفعل|ماذا يمكنك أن تفعل|إزاي تساعدني|كيف تساعدني|مساعدة|ساعدني)[\s?؟!.]*$",
        re.IGNORECASE)),
]


def small_talk(text: str) -> Optional[str]:
    """The kind of small talk `text` is, or None for a real question."""
    raw = (text or "").strip()
    if len(raw) > 60:
        return None
    for kind, pattern in _SMALL_TALK:
        if pattern.match(raw):
            return kind
    return None


def small_talk_reply(kind: str, lang: Lang, *, scope: str, company: str = "", has_briefs: bool = False) -> tuple[str, list[str]]:
    """A short reply and two or three questions the person could ask next."""
    who = company or tr(lang, "your company", "شركتك")
    if scope in ("brief", "card", "signal"):
        follow = [tr(lang, "What is the single most important takeaway?", "ما أهم خلاصة واحدة؟"),
                  tr(lang, "Which figures rest on a single source?", "أي الأرقام تعتمد على مصدر واحد؟"),
                  tr(lang, "What is missing before we decide?", "ما الناقص قبل أن نقرر؟")]
    elif has_briefs:
        follow = [tr(lang, "Summarise my latest brief", "لخّص أحدث موجز لي"),
                  tr(lang, "What changed with our competitors recently?", "ما الذي تغيّر لدى منافسينا مؤخرًا؟"),
                  tr(lang, "Who are our target customers?", "من هم عملاؤنا المستهدفون؟")]
    else:
        follow = [tr(lang, f"What do you know about {who}?", f"ماذا تعرف عن {who}؟"),
                  tr(lang, "Who are our target customers?", "من هم عملاؤنا المستهدفون؟"),
                  tr(lang, "Which competitors are we tracking?", "ما المنافسون الذين نتابعهم؟")]

    if kind == "greeting":
        text = tr(lang, f"Hi! I'm Edrak. Ask me about {who}, your competitors, markets and customers, or about any brief you have.",
                  f"أهلًا! أنا إدراك. اسألني عن {who} ومنافسيك وأسواقك وعملائك، أو عن أي موجز لديك.")
    elif kind == "thanks":
        text = tr(lang, "You're welcome. Anything else you'd like to check?", "على الرحب والسعة. هل تريد التحقق من شيء آخر؟")
    elif kind == "bye":
        text = tr(lang, "Goodbye. Your conversation is saved; you can pick it up from the history.", "مع السلامة. المحادثة محفوظة ويمكنك العودة إليها من السجل.")
    elif kind == "ack":
        text = tr(lang, "Good. What would you like to look at next?", "تمام. ماذا تريد أن نراجع بعد ذلك؟")
    elif kind == "identity":
        text = tr(lang, "I'm Ask Edrak, the assistant inside Edrak. I answer from what Edrak holds about your company: your briefs, "
                        "your company profile and approved facts, your documents, and, when those don't cover it, the web, "
                        "which I always mark as unverified. I describe what the evidence shows; the decisions stay with you.",
                  "أنا «اسأل إدراك»، المساعد داخل إدراك. أجيب مما يعرفه إدراك عن شركتك: موجزاتك وملف الشركة والحقائق المعتمدة "
                  "ومستنداتك، وعند الحاجة من الويب مع وسمه دائمًا بأنه غير موثّق. أصف ما تُظهره الأدلة، ويبقى القرار لك.")
    else:  # capabilities
        text = tr(lang, "I can:\n- explain a brief, a card or a signal and where each figure comes from\n"
                        "- answer from your company profile, approved company facts and uploaded documents, even before any analysis\n"
                        "- compare what your briefs say across analyses\n"
                        "- look things up on the web when Edrak doesn't hold them (marked unverified)\n"
                        "- suggest an analysis when a question needs real research",
                  "أستطيع:\n- شرح موجز أو بطاقة أو إشارة ومصدر كل رقم\n"
                  "- الإجابة من ملف شركتك والحقائق المعتمدة والمستندات المرفوعة، حتى قبل أي تحليل\n"
                  "- مقارنة ما تقوله موجزاتك عبر التحليلات\n"
                  "- البحث في الويب عندما لا يملك إدراك المعلومة (مع وسمها غير موثّقة)\n"
                  "- اقتراح تحليل عندما يحتاج السؤال إلى بحث حقيقي")
    return text, follow


# ------------------------------------------------------------------------------------------- rate limit


class RateLimiter:
    """Messages per person per minute and per day, kept in memory (one process).

    For several workers, move the counters to the database or Redis; the interface stays the same.
    """

    def __init__(self, per_minute: int, per_day: int) -> None:
        self.per_minute, self.per_day = per_minute, per_day
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> tuple[bool, str]:
        now = time.time() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 86400:
                hits.popleft()
            if len(hits) >= self.per_day:
                return False, "day"
            if sum(1 for t in hits if now - t <= 60) >= self.per_minute:
                return False, "minute"
            hits.append(now)
            return True, ""

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter(int(getattr(settings, "CHAT_RATE_PER_MINUTE", 20)), int(getattr(settings, "CHAT_RATE_PER_DAY", 400)))


# ------------------------------------------------------------------------------------------- the verdict


@dataclass
class ChatVerdict:
    action: Literal["allowed", "small_talk", "declined"]
    text: str                                   # the message to work on (personal data redacted)
    lang: Lang = "en"
    kind: Optional[str] = None                  # small-talk kind, or the decline code
    reply: Optional[str] = None                 # the reply to show when declined
    checks: list[Check] = field(default_factory=list)


def _decline(code: Optional[str], lang: Lang, scope: str) -> str:
    if code in ("prompt_injection", "prompt_injection_semantic"):
        return tr(lang, "That message reads like instructions for the AI rather than a question, so I won't act on it. "
                        "Ask me about your company, competitors, markets or a brief.",
                  "تبدو هذه الرسالة تعليمات موجهة للذكاء الاصطناعي وليست سؤالًا، لذلك لن أنفذها. اسألني عن شركتك أو منافسيك أو أسواقك أو موجز.")
    if code in ("harmful_request", "harmful_request_semantic"):
        return tr(lang, "I can't help with that. I can help you understand competitors, markets and customers from legitimate sources.",
                  "لا أستطيع المساعدة في ذلك. يمكنني مساعدتك على فهم المنافسين والأسواق والعملاء من مصادر مشروعة.")
    if code == "query_too_long":
        return tr(lang, "That message is too long for the chat. Keep it under 4,000 characters, or upload it as a document and ask about it.",
                  "الرسالة أطول من اللازم للمحادثة. اجعلها أقل من 4000 حرف، أو ارفعها كمستند واسأل عنها.")
    if code == "off_topic":
        return tr(lang, "I'm built for questions about your business: your company, competitors, markets, customers and your briefs. "
                        "Try something like “Who are our main competitors?” or “What do customers complain about?”",
                  "أنا مخصص لأسئلة عملك: شركتك ومنافسيك وأسواقك وعملائك وموجزاتك. جرّب مثلًا «من هم منافسونا الرئيسيون؟» "
                  "أو «ما أكثر ما يشتكي منه العملاء؟»")
    if code == "rate_limited":
        return tr(lang, "You've sent a lot of messages in a short time. Wait a moment and try again.",
                  "أرسلت رسائل كثيرة في وقت قصير. انتظر قليلًا ثم حاول مجددًا.")
    return tr(lang, "Rephrase the question as something about your business and I'll take a look.", "أعد صياغة السؤال كسؤال عن عملك وسأراجعه.")


def check_chat_message(text: str, *, scope: str, use_model: bool, rate_key: Optional[str] = None) -> ChatVerdict:
    """Small talk, rate limit, then the analysis guardrail with chat settings.

    `use_model` turns the off-topic model check on; inside a brief it stays off, because a question there is
    about the brief by construction ("what's the main takeaway?" has no business words of its own).
    """
    raw = (text or "").strip()
    lang = detect_lang(raw)
    if rate_key:
        ok, window = limiter.allow(rate_key)
        if not ok:
            return ChatVerdict("declined", raw, lang, "rate_limited", _decline("rate_limited", lang, scope),
                               [Check("chat_rate_limit", "low", "blocked", {"window": window})])

    kind = small_talk(raw)
    if kind and not injection_hits(raw):
        return ChatVerdict("small_talk", raw, lang, kind, checks=[Check("chat_small_talk", "info", "allowed", {"kind": kind})])

    verdict = check_query(raw, use_model=use_model, min_chars=CHAT_MIN_CHARS)
    if verdict.blocked:
        code = verdict.code or ""
        if code == "query_too_short":  # a single character: treat as a nudge, not an error
            return ChatVerdict("declined", raw, lang, code, tr(lang, "Could you say a little more about what you'd like to know?",
                                                                "هل يمكنك توضيح ما تريد معرفته أكثر قليلًا؟"), verdict.checks)
        return ChatVerdict("declined", raw, lang, code or "blocked", _decline(code, lang, scope), verdict.checks)
    return ChatVerdict("allowed", verdict.text, lang, checks=verdict.checks)
