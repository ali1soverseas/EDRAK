"""A scripted world for the graph tests: fake providers that serve fixture-like evidence and fake
models that answer each role the way the real prompts ask, without a network or a key."""

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from edrak.agents.customer_trends.deps import LlmFactory, WorkerDeps
from edrak.agents.customer_trends.llm.client import Role
from edrak.agents.customer_trends.llm.fake import Scripted, ScriptedChatModel
from edrak.agents.customer_trends.providers.base import (
    ProviderResult,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.runner import make_deps
from edrak.agents.customer_trends.schemas.common import Budget, Platform, SourceType
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import (
    ARABIC_PHRASES,
    BASE_DAY,
    ENGLISH_PHRASES,
    make_evidence,
)
from tests.customer_trends.tool_helpers import trend_series

# phrase markers, theme label, sentiment: the analyst fake labels an item by the first match
THEME_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("support", "الدعم"), "slow customer support", "negative"),
    (("Excel", "التصدير"), "missing excel export", "mixed"),
    (("income", "الدخل"), "irregular income tracking", "neutral"),
    (("Fast", "سريعة"), "easy to use", "positive"),
    (("competitor", "المنافس"), "cheaper competitors", "mixed"),
]

PLAN: dict[str, Any] = {
    "social_queries": {
        "reddit": ["GitLab Duo vs GitHub Copilot"],
        "x": ["GitLab Duo", "جيت لاب"],
        "youtube": ["GitLab Duo review"],
    },
    "hashtags": {"x": ["#gitlab"]},
    "trend_keywords": ["gitlab duo", "github copilot"],
    "news_queries": ["GitLab Duo"],
    "review_targets": [{"store": "app_store", "target": "1234567890", "country": "US"}],
    "competitor_angles": ["pricing complaints"],
    "rationale": "cover social, demand and reviews",
}
PLAN_SOCIAL_ONLY: dict[str, Any] = {
    "social_queries": {"reddit": ["GitLab Duo problems"], "x": ["GitLab Duo problems"]},
    "rationale": "close the social gap",
}


# the providers


@dataclass
class World:
    """What the fake providers do. `failing` capabilities raise until `fail_calls` calls were
    made to them; `delay_s` makes every call wait."""

    failing: set[str] = field(default_factory=set)
    fail_calls: int | None = None
    delay_s: float = 0.0
    items_per_call: int = 1
    calls: dict[str, int] = field(default_factory=dict)


def _posts(
    capability: str, n: int, call: int, platform: Platform, phrases: Sequence[str], dated: bool
) -> list[Any]:
    return [
        make_evidence(
            f"{phrases[i % len(phrases)]} GitLab Duo, GitHub Copilot ({capability} {call}-{i})",
            platform=platform,
            source_type=SourceType.SOCIAL_POST,
            language="ar" if phrases is ARABIC_PHRASES else "en",
            published_at=BASE_DAY + timedelta(days=i) if dated else None,
            engagement={"likes": (i * 7) % 40, "replies": i % 4},
            provider="fake",
        )
        for i in range(n)
    ]


def _serve(capability: str, call: int) -> ProviderResult:
    kind, _, variant = capability.partition(":")
    items: list[Any]
    if kind == "social_search" and variant == "reddit":
        items = _posts(capability, 30, call, Platform.REDDIT, ENGLISH_PHRASES, True)
    elif kind == "social_search" and variant == "x":
        items = [
            *_posts(capability, 15, call, Platform.X, ARABIC_PHRASES, True),
            *_posts(capability + "-en", 10, call, Platform.X, ENGLISH_PHRASES, True),
        ]
    elif kind == "social_search" and variant == "youtube":
        items = _posts(capability, 6, call, Platform.YOUTUBE, ENGLISH_PHRASES, True)
    elif kind in {"social_search", "social_comments"}:
        items = _posts(capability, 8, call, Platform(variant), ENGLISH_PHRASES, True)
    elif kind == "search_interest":
        return ProviderResult(
            items=[
                trend_series(k, [10 + 3 * i for i in range(24)])
                for k in ("gitlab duo", "github copilot")
            ],
            raw_count=2,
            cost_estimate=0.001,
        )
    elif kind == "reviews":
        items = [
            make_evidence(
                f"{ENGLISH_PHRASES[i % 5]} review {call}-{i} of the GitHub Copilot app",
                platform=None,
                source_type=SourceType.REVIEW,
                published_at=BASE_DAY + timedelta(days=i),
                engagement={"rating": 1 + i % 5},
                provider="fake",
            )
            for i in range(25)
        ]
    elif kind == "news":
        items = [
            make_evidence(
                f"News headline {i} about GitLab Duo ({capability} {call})",
                platform=None,
                source_type=SourceType.NEWS,
                published_at=BASE_DAY + timedelta(days=i),
                snippet_only=True,
                provider="fake",
            )
            for i in range(12)
        ]
    else:
        items = [
            make_evidence(
                f"Web result {i} for GitLab Duo ({capability} {call})",
                platform=None,
                source_type=SourceType.WEB,
                snippet_only=True,
                provider="fake",
            )
            for i in range(6)
        ]
    return ProviderResult(items=items, raw_count=len(items), cost_estimate=0.001)


class WorldProvider:
    """One provider name serving every capability the config routes to it first."""

    def __init__(self, name: str, capabilities: set[str], world: World) -> None:
        self.name = name
        self.capabilities = capabilities
        self._world = world

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        world = self._world
        call = world.calls.get(capability, 0) + 1
        world.calls[capability] = call
        if world.delay_s:
            await asyncio.sleep(world.delay_s)
        if capability in world.failing and (world.fail_calls is None or call <= world.fail_calls):
            raise ProviderUnavailable(f"{capability} is down")
        return _serve(capability, call)


def world_registry(world: World | None = None, budget: Budget | None = None) -> ProviderRegistry:
    world = world or World()
    config = load_providers_config()
    by_name: dict[str, set[str]] = {}
    for capability, names in config.routing.items():
        by_name.setdefault(names[0], set()).add(capability)
    providers = [WorldProvider(name, caps, world) for name, caps in by_name.items()]
    return ProviderRegistry(
        config,
        budget=BudgetTracker(budget or Budget()),
        breaker=CircuitBreaker(threshold=100),
        providers=providers,
    )


# the models


class FunctionChatModel(ScriptedChatModel):
    """A scripted model whose answer is computed from the messages it receives."""

    responder: Callable[[list[BaseMessage]], Scripted]

    def _next(self, messages: Sequence[BaseMessage]) -> Scripted:
        self.calls.append(list(messages))
        return self.responder(list(messages))


def _human_json(messages: list[BaseMessage], marker: str) -> Any:
    human = messages[1].content
    assert isinstance(human, str)
    return json.loads(human.split(marker, 1)[1].split("\n\nREJECTED:\n")[0])


def analyst_answer(messages: list[BaseMessage]) -> Scripted:
    """Label every item of the chunk by the phrase it contains."""
    items = _human_json(messages, "Items:\n")
    labels = []
    for item in items:
        for markers, theme, sentiment in THEME_RULES:
            if any(marker in item["text"] for marker in markers):
                labels.append({"id": item["id"], "sentiment": sentiment, "themes": [theme]})
                break
        else:
            labels.append({"id": item["id"], "sentiment": "neutral", "themes": []})
    return {"items": labels, "candidate_themes": []}


@dataclass
class WriterScript:
    """How the writer fake behaves. `bad_first` makes the first draft hold flawed findings."""

    bad_first: bool = False
    fail: bool = False
    headline: str | None = (
        "Evidence from several platforms shows recurring support and pricing complaints."
    )
    seen: list[str] = field(default_factory=list)


def good_findings(context: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    themes = context["themes"]
    if themes:
        top = themes[0]
        findings.append(
            {
                "id": "f1",
                "type": "pain_point",
                "claim": f"The theme '{top['label']}' appears in {top['count']} analyzed items.",
                "confidence": "medium",
                "evidence_ids": top["evidence_ids"][:5],
                "metrics": {"count": top["count"]},
                "caveats": ["Fixture data."],
            }
        )
    mix = next((m for m in context["metrics"] if m["name"] == "platform_mix"), None)
    if mix and themes:
        shares = mix["values"]["percent"]
        platform, percent = next(iter(shares.items()))
        ids = list(dict.fromkeys(i for theme in themes for i in theme["evidence_ids"]))[:12]
        findings.append(
            {
                "id": "f2",
                "type": "sentiment",
                "claim": f"{platform} holds {percent}% of the collected items.",
                "confidence": "high",
                "evidence_ids": ids,
                "metrics": {"metric_id": mix["metric_id"], "percent": percent},
            }
        )
    if context["trends"]:
        trend = context["trends"][0]
        findings.append(
            {
                "id": "f3",
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


def flawed_findings(context: dict[str, Any]) -> list[dict[str, Any]]:
    good = good_findings(context)
    base_ids = context["themes"][0]["evidence_ids"][:3] if context["themes"] else ["0" * 16]
    return [
        *good[:1],
        {
            "id": "bad_id",
            "type": "risk",
            "claim": "Complaints grew by 40% last month.",
            "confidence": "medium",
            "evidence_ids": [*base_ids[:1], "f" * 16],
            "metrics": {},
        },
        {
            "id": "verdict",
            "type": "risk",
            "claim": "GitLab should enter this market now.",
            "confidence": "medium",
            "evidence_ids": base_ids,
            "metrics": {},
        },
    ]


def writer_model(script: WriterScript) -> FunctionChatModel:
    def respond(messages: list[BaseMessage]) -> Scripted:
        system = str(messages[0].content)
        if system.startswith("You write the headline"):
            script.seen.append("headline")
            if script.headline is None:
                raise ValueError("no headline today")
            return {"headline": script.headline}
        if script.fail:
            raise ValueError("the writer is down")
        marker = "CONTEXT:\n"
        context = _human_json(messages, marker)
        if system.startswith("Some of your findings"):
            script.seen.append("repair")
            rejected = json.loads(str(messages[1].content).split("\n\nREJECTED:\n", 1)[1])
            by_id = {r["finding"]["id"]: r for r in rejected}
            repaired = []
            if "bad_id" in by_id:
                top = context["themes"][0]
                repaired.append(
                    {
                        "id": "bad_id",
                        "type": "risk",
                        "claim": f"The theme '{top['label']}' is raised in {top['count']} items.",
                        "confidence": "low",
                        "evidence_ids": top["evidence_ids"][:3],
                        "metrics": {"count": top["count"]},
                    }
                )
            if "verdict" in by_id:
                repaired.append({**by_id["verdict"]["finding"]})  # still a verdict: stays rejected
            return {"findings": repaired}
        script.seen.append("write")
        findings = flawed_findings(context) if script.bad_first else good_findings(context)
        return {"findings": findings}

    return FunctionChatModel(responses=[], responder=respond)


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


def tool_calls(*calls: tuple[str, dict[str, Any]], tag: str = "c") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"{tag}{n}"} for n, (name, args) in enumerate(calls)
        ],
    )


def social_turns(tag: str = "s") -> list[Scripted]:
    return [
        tool_calls(
            ("social_search", {"platform": "reddit", "query": "GitLab Duo vs GitHub Copilot"}),
            ("social_search", {"platform": "x", "query": "GitLab Duo"}),
            ("social_search", {"platform": "youtube", "query": "GitLab Duo review"}),
            tag=tag,
        ),
        AIMessage(content="Collected posts from reddit, x and youtube."),
    ]


def demand_turns(tag: str = "d") -> list[Scripted]:
    return [
        tool_calls(
            ("search_interest", {"keywords": ["gitlab duo", "github copilot"]}),
            ("news_coverage", {"query": "GitLab Duo", "source": "gdelt"}),
            ("web_search", {"query": "GitLab Duo"}),
            tag=tag,
        ),
        AIMessage(content="Interest series and news collected."),
    ]


def reviews_turns(tag: str = "r") -> list[Scripted]:
    return [
        tool_calls(
            (
                "reviews_fetch",
                {"store": "app_store", "target_id_or_url": "1234567890", "country": "US"},
            ),
            tag=tag,
        ),
        AIMessage(content="App store reviews collected."),
    ]


@dataclass
class Models:
    """The role models of a run, kept so tests can look at what each was asked."""

    planner: ScriptedChatModel
    analyst: FunctionChatModel
    writer: FunctionChatModel
    router: BranchRouter
    writer_script: WriterScript

    @property
    def factory(self) -> LlmFactory:
        by_role: dict[str, BaseChatModel] = {
            "planner": self.planner,
            "analyst": self.analyst,
            "writer": self.writer,
            "branch": self.router,
        }

        def build(role: Role) -> BaseChatModel:
            return by_role[role]

        return build

    @property
    def branch_calls(self) -> dict[str, int]:
        return {name: len(model.calls) for name, model in self.router.scripts.items()}


def models(
    *,
    plans: Sequence[Scripted] = (PLAN,),
    social: Sequence[Scripted] | None = None,
    demand: Sequence[Scripted] | None = None,
    reviews: Sequence[Scripted] | None = None,
    writer: WriterScript | None = None,
) -> Models:
    script = writer or WriterScript()
    router = BranchRouter(
        scripts={
            "social": ScriptedChatModel(responses=list(social or social_turns())),
            "demand": ScriptedChatModel(responses=list(demand or demand_turns())),
            "reviews": ScriptedChatModel(responses=list(reviews or reviews_turns())),
        }
    )
    return Models(
        planner=ScriptedChatModel(responses=list(plans)),
        analyst=FunctionChatModel(responses=[], responder=analyst_answer),
        writer=writer_model(script),
        router=router,
        writer_script=script,
    )


def scenario_settings(tmp_path: Path) -> Settings:
    """Settings that keep the store, checkpoints and results inside `tmp_path`."""
    return Settings(  # type: ignore[call-arg]  # pydantic-settings fills the other fields
        _env_file=None,
        edrak_data_dir=tmp_path / "data",
        artifacts_path=tmp_path / "artifacts",
    )


def worker_deps(
    brief: TaskBrief,
    tmp_path: Path,
    store: EvidenceStore,
    *,
    world: World | None = None,
    scripted: Models | None = None,
    budget: Budget | None = None,
) -> WorkerDeps:
    """Dependencies for calling nodes directly, over the fake world and the fake models."""
    return make_deps(
        brief,
        store=store,
        settings=scenario_settings(tmp_path),
        sink=None,
        providers=world_registry(world, budget),
        llm=(scripted or models()).factory,
    )
