"""The built-in demo script of the fake model, for runs with no keys (`EDRAK_FAKE_LLM=true`).

It plays each role of the worker without a network: the planner answers with a plan built from the
brief, each collection branch calls its tools the way the real branch would, the analyst labels
items by the demo phrases, and the writer turns the aggregates it is given into findings. It is
consistent with the demo fixtures in `backend/evals/customer_trends/fixtures/providers`, which
serve those same phrases, so a run in fixture mode ends with themes, trends and findings.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from edrak.agents.customer_trends.llm.client import Role
from edrak.agents.customer_trends.llm.fake import FunctionChatModel, Scripted, ScriptedChatModel
from edrak.agents.customer_trends.schemas.task import TaskBrief

DEMO_PASSES = 3
HEADLINE_THEMES = 3


class DemoTheme(NamedTuple):
    label: str
    sentiment: str
    english: str
    arabic: str


DEMO_THEMES = (
    DemoTheme(
        "slow customer support",
        "negative",
        "The support team is slow to reply and tickets sit for days",
        "الدعم الفني بطيء جدا ويتأخر الرد على التذاكر",
    ),
    DemoTheme(
        "pricing concerns",
        "negative",
        "Pricing keeps going up and the free tier keeps shrinking",
        "الأسعار ترتفع باستمرار والخطة المجانية تتقلص",
    ),
    DemoTheme(
        "missing export features",
        "mixed",
        "Great product but exporting to Excel or CSV is missing",
        "المنتج ممتاز لكن ينقصه التصدير إلى إكسل",
    ),
    DemoTheme(
        "easy to use",
        "positive",
        "Fast, simple and easy to use from day one",
        "سريع وبسيط وسهل الاستخدام من اليوم الأول",
    ),
    DemoTheme(
        "irregular income tracking",
        "neutral",
        "I need irregular income tracking for freelance work",
        "أحتاج ميزة تتبع الدخل غير المنتظم للعمل الحر",
    ),
    DemoTheme(
        "cheaper competitors",
        "mixed",
        "A competitor is cheaper but the quality is worse",
        "المنافس أرخص لكن جودته أقل",
    ),
    DemoTheme(
        "reliability problems",
        "negative",
        "Outages and crashes happen too often during busy hours",
        "الأعطال والانهيارات تتكرر كثيرا في أوقات الذروة",
    ),
    DemoTheme(
        "confusing onboarding",
        "negative",
        "Onboarding is confusing and the documentation is out of date",
        "التهيئة الأولية مربكة والتوثيق قديم",
    ),
)
ARABIC_QUERY_SUFFIX = "مشاكل"


def demo_plan(brief: TaskBrief) -> dict[str, Any]:
    """The planner's answer: a plan built from the brief's entity and competitors."""
    names = [brief.entity, *brief.competitors]
    plan: dict[str, Any] = {
        "social_queries": {
            "reddit": [
                f"{brief.entity} problems",
                *[f"{brief.entity} vs {c}" for c in brief.competitors[:1]],
            ],
            "x": [brief.entity, f"{brief.entity} {ARABIC_QUERY_SUFFIX}"],
            "youtube": [f"{brief.entity} review"],
        },
        "hashtags": {},
        "trend_keywords": names[:5],
        "news_queries": [brief.entity],
        "review_targets": [],
        "competitor_angles": [f"complaints about {c}" for c in brief.competitors[:2]],
        "rationale": "demo plan built from the brief",
    }
    if brief.competitors:
        plan["review_targets"] = [{"store": "app_store", "target": "1234567890", "country": "US"}]
    return plan


def planner_model(brief: TaskBrief) -> FunctionChatModel:
    return FunctionChatModel(responses=[], responder=lambda messages: demo_plan(brief))


# the analyst


def analyst_answer(messages: list[BaseMessage]) -> Scripted:
    """Label each item of the chunk with the demo theme whose phrase it contains."""
    human = str(messages[1].content)
    items = json.loads(human.split("Items:\n", 1)[1])
    labels = []
    for item in items:
        match = next(
            (t for t in DEMO_THEMES if t.english in item["text"] or t.arabic in item["text"]), None
        )
        labels.append(
            {
                "id": item["id"],
                "sentiment": match.sentiment if match else "neutral",
                "themes": [match.label] if match else [],
            }
        )
    return {"items": labels, "candidate_themes": []}


def analyst_model() -> FunctionChatModel:
    return FunctionChatModel(responses=[], responder=analyst_answer)


# the writer


def findings_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings that state only what the context holds: theme counts, a platform share, a trend."""
    findings: list[dict[str, Any]] = []
    themes = context.get("themes", [])
    for number, theme in enumerate(themes[:2], start=1):
        percent = round(theme["share"] * 100, 1)
        negative = theme["sentiment_mix"].get("negative", 0) >= 0.5
        findings.append(
            {
                "id": f"f{number}",
                "type": "pain_point" if negative else "sentiment",
                "claim": (
                    f"The theme '{theme['label']}' appears in {theme['count']} analyzed items, "
                    f"{percent}% of them."
                ),
                "confidence": "medium",
                "evidence_ids": theme["evidence_ids"][:5],
                "metrics": {"count": theme["count"], "share_pct": percent},
                "caveats": ["Demo data, not a real market sample."],
            }
        )
    mix = next((m for m in context.get("metrics", []) if m["name"] == "platform_mix"), None)
    if mix and themes:
        platform, percent = next(iter(mix["values"]["percent"].items()))
        ids = list(dict.fromkeys(i for theme in themes for i in theme["evidence_ids"]))[:12]
        findings.append(
            {
                "id": "f3",
                "type": "sentiment",
                "claim": f"{platform} holds {percent}% of the collected items.",
                "confidence": "high",
                "evidence_ids": ids,
                "metrics": {"metric_id": mix["metric_id"], "percent": percent},
            }
        )
    trend = next((t for t in context.get("trends", []) if t.get("evidence_id")), None)
    if trend:
        findings.append(
            {
                "id": "f4",
                "type": "trend",
                "claim": (
                    f"Search interest for {trend['keyword']} moved from {trend['first']:g} to "
                    f"{trend['last']:g}."
                ),
                "confidence": "low",
                "evidence_ids": [trend["evidence_id"]],
                "metrics": {"first": trend["first"], "last": trend["last"]},
            }
        )
    return findings


def headline_from_context(context: dict[str, Any]) -> str:
    coverage = context["coverage"]
    platforms = [p for p in coverage["by_platform"] if p != "unknown"]
    labels = ", ".join(t["label"] for t in context["themes"][:HEADLINE_THEMES]) or "no clear theme"
    return (
        f"{coverage['evidence_items']} public items from {len(platforms)} platform(s) were "
        f"collected; the most discussed themes are {labels}."
    )


def writer_answer(messages: list[BaseMessage]) -> Scripted:
    system = str(messages[0].content)
    human = str(messages[1].content)
    context = json.loads(human.split("CONTEXT:\n", 1)[1].split("\n\nREJECTED:\n")[0])
    if system.startswith("You write the headline"):
        return {"headline": headline_from_context(context)}
    return {"findings": findings_from_context(context)}


def writer_model() -> FunctionChatModel:
    return FunctionChatModel(responses=[], responder=writer_answer)


# the collection branches


def tool_calls(*calls: tuple[str, dict[str, Any]], tag: str = "c") -> AIMessage:
    """One assistant turn that asks for several tool calls at once."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"{tag}{n}"} for n, (name, args) in enumerate(calls)
        ],
    )


class BranchRouter(BaseChatModel):
    """The `branch` model: each collection branch gets its own script, chosen from its tools."""

    scripts: dict[str, ScriptedChatModel]

    @property
    def _llm_type(self) -> str:
        return "branch-router"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("a branch model is reached through bind_tools")

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        names = {str(getattr(tool, "name", "")) for tool in tools}
        branch = (
            "social"
            if "social_search" in names
            else "demand"
            if "search_interest" in names
            else "reviews"
        )
        return self.scripts[branch]


def branch_turns(branch: str, brief: TaskBrief, plan: dict[str, Any]) -> list[Scripted]:
    """What a branch does: one turn with its tool calls, then a summary."""
    entity = brief.entity
    calls: tuple[tuple[str, dict[str, Any]], ...]
    if branch == "social":
        calls = (
            ("social_search", {"platform": "reddit", "query": f"{entity} problems"}),
            ("social_search", {"platform": "x", "query": entity}),
            ("social_search", {"platform": "youtube", "query": f"{entity} review"}),
        )
        done = "Collected posts from reddit, x and youtube."
    elif branch == "demand":
        calls = (
            ("search_interest", {"keywords": plan["trend_keywords"]}),
            ("news_coverage", {"query": entity, "source": "gdelt"}),
            ("web_search", {"query": entity}),
        )
        done = "Collected search interest, news and web results."
    else:
        calls = (
            (
                "reviews_fetch",
                {"store": "app_store", "target_id_or_url": "1234567890", "country": "US"},
            ),
        )
        done = "Collected app store reviews."
    return [
        item
        for number in range(DEMO_PASSES)
        for item in (tool_calls(*calls, tag=f"{branch}{number}-"), AIMessage(content=done))
    ]


def branch_router(brief: TaskBrief) -> BranchRouter:
    plan = demo_plan(brief)
    return BranchRouter(
        scripts={
            branch: ScriptedChatModel(responses=branch_turns(branch, brief, plan))
            for branch in ("social", "demand", "reviews")
        }
    )


def demo_llm(brief: TaskBrief) -> Callable[[Role], BaseChatModel]:
    """The model factory of a demo run: one fake model per role, built once for the brief."""
    models: dict[str, BaseChatModel] = {
        "planner": planner_model(brief),
        "analyst": analyst_model(),
        "writer": writer_model(),
        "branch": branch_router(brief),
    }

    def build(role: Role) -> BaseChatModel:
        return models[role]

    return build
