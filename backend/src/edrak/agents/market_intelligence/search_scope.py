"""Search scope derived from a brief: region, language, recency, domains."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PlaceScope:
    names: tuple[str, ...]
    gl: str
    language: str
    hl: str
    local_name: str
    tavily_country: str


# Longest name is tried first so "south korea" wins over "korea".
PLACES: tuple[PlaceScope, ...] = (
    PlaceScope(("mainland china", "people's republic of china", "china", "prc"), "cn", "zh", "zh-cn", "Chinese", "china"),
    PlaceScope(("south korea", "korea"), "kr", "ko", "ko", "Korean", "south korea"),
    PlaceScope(("united kingdom", "britain", "uk"), "uk", "en", "en", "English", "united kingdom"),
    PlaceScope(("united states", "usa", "u.s.a.", "u.s."), "us", "en", "en", "English", "united states"),
    PlaceScope(("united arab emirates", "uae"), "ae", "ar", "ar", "Arabic", "united arab emirates"),
    PlaceScope(("saudi arabia",), "sa", "ar", "ar", "Arabic", "saudi arabia"),
    PlaceScope(("germany",), "de", "de", "de", "German", "germany"),
    PlaceScope(("france",), "fr", "fr", "fr", "French", "france"),
    PlaceScope(("japan",), "jp", "ja", "ja", "Japanese", "japan"),
    PlaceScope(("brazil",), "br", "pt", "pt-br", "Portuguese", "brazil"),
    PlaceScope(("mexico",), "mx", "es", "es", "Spanish", "mexico"),
    PlaceScope(("spain",), "es", "es", "es", "Spanish", "spain"),
    PlaceScope(("egypt",), "eg", "ar", "ar", "Arabic", "egypt"),
    PlaceScope(("india",), "in", "en", "en", "English", "india"),
)

LANGUAGE_HINTS: tuple[tuple[str, str, str, str], ...] = (
    ("chinese", "zh", "zh-cn", "Chinese"),
    ("japanese", "ja", "ja", "Japanese"),
    ("korean", "ko", "ko", "Korean"),
    ("arabic", "ar", "ar", "Arabic"),
    ("german", "de", "de", "German"),
    ("french", "fr", "fr", "French"),
    ("portuguese", "pt", "pt", "Portuguese"),
    ("spanish", "es", "es", "Spanish"),
)

_RECENCY = {"day", "week", "month", "year"}


@dataclass(frozen=True)
class SearchScope:
    gl: str = ""
    language: str = "en"
    hl: str = "en"
    local_name: str = ""
    place: str = ""
    recency: str = "year"
    include_domains: tuple[str, ...] = ()
    tavily_country: str = ""

    @property
    def needs_local_queries(self) -> bool:
        return bool(self.language) and self.language != "en"

    def as_dict(self) -> dict:
        data = {
            "gl": self.gl,
            "language": self.language,
            "hl": self.hl,
            "local_language": self.local_name if self.needs_local_queries else "",
            "place": self.place,
            "recency": self.recency,
            "tavily_country": self.tavily_country,
        }
        if self.include_domains:
            data["include_domains"] = list(self.include_domains)
        return data

    def note(self) -> str:
        place = self.place or "no specific country"
        text = f"place={place}; gl={self.gl or 'none'}; language={self.language}; recency={self.recency or 'none'}."
        if self.needs_local_queries:
            text += f" Write each query in English and again in {self.local_name}."
        return text


def _mentioned(text: str, name: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text) is not None


def _place_from_text(text: str) -> PlaceScope | None:
    lowered = text.lower()
    found: list[tuple[int, PlaceScope]] = []
    for place in PLACES:
        for name in place.names:
            if _mentioned(lowered, name):
                found.append((len(name), place))
                break
    if not found:
        return None
    found.sort(key=lambda item: item[0], reverse=True)
    return found[0][1]


def _language_hint(text: str) -> tuple[str, str, str] | None:
    lowered = text.lower()
    for name, language, hl, local_name in LANGUAGE_HINTS:
        if _mentioned(lowered, name):
            return language, hl, local_name
    return None


def _is_target_market(text: str, place: PlaceScope) -> bool:
    """True when the brief is about that geography, not only a company's home country."""
    lowered = text.lower()
    for name in place.names:
        escaped = re.escape(name)
        if re.search(rf"enter(?:ing)?(?: the)? {escaped}", lowered):
            return True
        if f"{name} market" in lowered or f"market in {name}" in lowered:
            return True
        if re.search(rf"\bin {escaped}\b", lowered):
            return True
        if f"among {name}" in lowered or f"{name} enterprises" in lowered:
            return True
        if f"{name} devops" in lowered or f"{name} news" in lowered:
            return True
        if f"conditions in {name}" in lowered or f"demand in {name}" in lowered:
            return True
    if place.gl == "us" and re.search(
        r"\b(?:among )?US enterprises\b|\bUS market\b|\bUS demand\b|\bin the US\b",
        text,
    ):
        return True
    return False


def recency_from_brief(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(today|this week|past week)\b", lowered):
        return "week"
    if re.search(r"\b(this month|past month|breaking news)\b", lowered):
        return "month"
    if re.search(r"\b(historical|history|over the last decade)\b", lowered):
        return ""
    return "year"


def domains_in_text(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for host in re.findall(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", text.lower()):
        if host not in found:
            found.append(host)
    return tuple(found[:8])


def clean_domains(values) -> list[str]:
    cleaned: list[str] = []
    if not isinstance(values, list):
        return cleaned
    for value in values:
        host = re.sub(r"^https?://", "", str(value).strip().lower()).split("/")[0]
        if host.startswith("www."):
            host = host[4:]
        if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host) and host not in cleaned:
            cleaned.append(host)
    return cleaned[:5]


def scope_from_brief(goal: str, context: str = "") -> SearchScope:
    """Use a country code only when the research question is about that market."""
    text = f"{goal}\n{context}"
    recency = recency_from_brief(text)
    hint = _language_hint(goal) or _language_hint(context)
    for blob in (goal, context):
        place = _place_from_text(blob)
        if place and _is_target_market(blob, place):
            language, hl, local_name = place.language, place.hl, place.local_name
            if place.language == "en" and hint and hint[0] != "en":
                language, hl, local_name = hint
            return SearchScope(
                gl=place.gl,
                language=language,
                hl=hl,
                local_name=local_name,
                place=place.names[0],
                recency=recency,
                tavily_country=place.tavily_country,
            )
    if hint and hint[0] != "en":
        return SearchScope(
            language=hint[0],
            hl=hint[1],
            local_name=hint[2],
            recency=recency,
        )
    if re.search(r"\b(?:among )?US enterprises\b|\bUS market\b|\bUS demand\b|\bin the US\b", goal):
        return SearchScope(
            gl="us",
            language="en",
            hl="en",
            place="united states",
            recency=recency,
            tavily_country="united states",
        )
    return SearchScope(recency=recency)


def query_spec(
    text: str,
    scope: SearchScope,
    language: str = "",
    include_domains: list[str] | None = None,
) -> dict:
    """One search query with the brief's region, language, and recency."""
    lang = (language or "").strip().lower()
    if not lang:
        lang = scope.language if any(ord(char) > 127 for char in text) else "en"
    if lang in {scope.language, scope.hl}:
        hl = scope.hl
        lang = scope.language
    else:
        hl = "en"
        lang = "en"
    domains = clean_domains(include_domains) if include_domains else []
    spec = {
        "q": text.strip(),
        "gl": scope.gl,
        "language": lang,
        "hl": hl,
        "recency": scope.recency if scope.recency in _RECENCY else "",
    }
    if domains:
        spec["include_domains"] = domains
    if scope.tavily_country:
        spec["tavily_country"] = scope.tavily_country
    return spec
