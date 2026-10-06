"""Streamlit rendering of the developer UI. All logic sits in the sibling modules; these functions
only lay out what they compute (SPEC section 12)."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import streamlit as st

from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.schemas.common import (
    Budget,
    Depth,
    Platform,
    SourceType,
    UseCase,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult, to_worker_result
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore, RunSummary
from edrak.agents.customer_trends.ui.components import format as fmt
from edrak.agents.customer_trends.ui.components import forms
from edrak.agents.customer_trends.ui.components.controller import RunController

KEY_FIELDS = (
    "ollama_api_key",
    "serper_api_key",
    "youtube_api_key",
    "apify_token",
    "socialcrawl_api_key",
    "google_trends_api_key",
)
EVIDENCE_LIMIT = 200
RESULT_TABS = (
    "Summary",
    "Findings",
    "Themes",
    "Trends",
    "Evidence explorer",
    "Raw JSON",
    "Provenance",
)


@dataclass(frozen=True)
class SidebarChoice:
    provider_mode: str
    fake_llm: bool


# the sidebar


def provider_health(settings: Settings) -> dict[str, dict[str, Any]]:
    """The health of the providers these settings would set up (breakers are fresh, quotas and key
    positions are real)."""
    registry = ProviderRegistry.from_config(settings, BudgetTracker(Budget()), CircuitBreaker())
    try:
        return registry.health()
    finally:
        asyncio.run(registry.aclose())


def render_sidebar(settings: Settings, runs: list[RunSummary]) -> SidebarChoice:
    with st.sidebar:
        st.header("Environment")
        st.caption(f"Model: {settings.ollama_model}  \nBase URL: {settings.ollama_base_url}")
        redacted = settings.redacted()
        st.table([{"key": key, "status": redacted[key]} for key in KEY_FIELDS])
        mode = st.radio(
            "Provider mode",
            ["live", "fixture"],
            index=0 if settings.edrak_provider_mode == "live" else 1,
            key="sb_provider_mode",
            help="Fixture mode serves recorded provider data and needs no keys.",
        )
        fake = st.toggle(
            "Fake LLM (scripted demo model)",
            value=settings.edrak_fake_llm,
            key="sb_fake_llm",
            help="Runs the whole graph with no model key.",
        )
        st.caption(f"Data directory: {settings.data_dir}")
        st.header("Provider health")
        if mode == "fixture":
            st.caption("Fixture mode: no provider is called.")
        else:
            rows = fmt.health_rows(
                provider_health(settings.model_copy(update={"edrak_provider_mode": mode}))
            )
            st.dataframe(rows, hide_index=True) if rows else st.caption("No provider is set up.")
        st.header("Run history")
        with_results = [r for r in runs if r.result_location]
        if with_results:
            st.selectbox(
                "Past run",
                [r.run_id for r in with_results],
                key="sb_history_run",
                format_func=lambda run_id: next(
                    f"{r.run_id}  ({r.created_at:%Y-%m-%d %H:%M}, {r.findings_count} findings)"
                    for r in with_results
                    if r.run_id == run_id
                ),
            )
            st.button("Load this result", key="sb_history_load", on_click=_request_load)
        else:
            st.caption("No finished runs yet.")
    return SidebarChoice(provider_mode=mode, fake_llm=fake)


def _request_load() -> None:
    st.session_state["ct_load_run"] = st.session_state.get("sb_history_run")


# the task form


def _apply_values(values: dict[str, Any]) -> None:
    for name, value in values.items():
        st.session_state[f"f_{name}"] = value


def _load_preset(title: str) -> None:
    preset = forms.load_presets()[title]
    _apply_values(forms.form_values(preset))
    st.session_state["f_json"] = preset.model_dump_json(indent=2)


def _use_json_brief() -> None:
    brief, problems = forms.brief_from_json(st.session_state.get("f_json", ""))
    if brief is None:
        st.session_state["f_json_problems"] = problems
        return
    st.session_state["f_json_problems"] = []
    _apply_values(forms.form_values(brief))


def init_form(presets: dict[str, TaskBrief]) -> None:
    """Fill the form with the first preset the first time the page loads."""
    if "f_entity" not in st.session_state and presets:
        first = next(iter(presets.values()))
        _apply_values(forms.form_values(first))
        st.session_state["f_json"] = first.model_dump_json(indent=2)


def render_form(presets: dict[str, TaskBrief]) -> tuple[TaskBrief | None, list[str]]:
    """The task form and its JSON tab. Returns the brief the form describes, or its problems."""
    st.subheader("Task")
    columns = st.columns(len(presets) or 1)
    for column, title in zip(columns, presets, strict=False):
        column.button(title, key=f"preset_{title}", on_click=_load_preset, args=(title,))
    form_tab, json_tab = st.tabs(["Form", "JSON"])
    with form_tab:
        left, right = st.columns(2)
        with left:
            st.selectbox("Use case", [u.value for u in UseCase], key="f_use_case")
            st.text_input("Entity (company, product or category)", key="f_entity")
            st.text_area("Question", key="f_question", height=100)
            st.text_input("Market", key="f_market")
            st.text_input("Country code (ISO 3166-1 alpha-2)", key="f_geo")
            st.multiselect("Languages", list(forms.LANGUAGE_OPTIONS), key="f_languages")
            st.text_input("More language codes, comma separated", key="f_extra_languages")
            st.text_input("Competitors, comma separated", key="f_competitors")
        with right:
            st.multiselect("Focus", list(forms.FOCUS_OPTIONS), key="f_focus")
            st.date_input("Since", value=None, key="f_since")
            st.date_input("Until", value=None, key="f_until")
            st.selectbox("Depth", [d.value for d in Depth], key="f_depth")
            st.number_input("Budget: tool calls", min_value=1, step=5, key="f_max_tool_calls")
            st.number_input("Budget: cost (USD)", min_value=0.0, step=0.25, key="f_max_cost_usd")
            st.number_input("Budget: seconds", min_value=1.0, step=30.0, key="f_max_seconds")
            st.text_area("Notes", key="f_notes", height=68)
            st.text_input("Task id", key="f_task_id")
            st.text_input("Run id", key="f_run_id")
    with json_tab:
        st.text_area("TaskBrief as JSON", key="f_json", height=320)
        st.button("Use this JSON in the form", key="f_json_use", on_click=_use_json_brief)
        for problem in st.session_state.get("f_json_problems", []):
            st.error(problem)
    values = {
        str(name).removeprefix("f_"): value
        for name, value in st.session_state.items()
        if str(name).startswith("f_") and name not in {"f_json", "f_json_problems"}
    }
    return forms.brief_from_form(values)


# the run panel


def render_run_panel(controller: RunController, brief: TaskBrief) -> None:
    events = controller.events
    state = "stopped" if controller.stopped else "running" if controller.running else "finished"
    st.subheader(f"Run {controller.brief.run_id}: {state}")
    if controller.running and st.button("Stop", key="run_stop"):
        controller.stop()
    columns = st.columns(len(fmt.NODE_ORDER))
    for column, row in zip(
        columns, fmt.node_states(events, finished=not controller.running), strict=True
    ):
        column.caption(row["node"])
        column.markdown(fmt.badge(row["state"]))
    limits = brief.budget.model_dump()
    meters = st.columns(3)
    for column, meter in zip(meters, fmt.budget_meter(events, limits), strict=True):
        column.progress(
            meter["share"], text=f"{meter['name']}: {meter['used']:g} of {meter['limit']:g}"
        )
    for line in fmt.notices(events):
        st.warning(line)
    rows = fmt.tool_rows(events)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    if controller.error:
        st.error(controller.error)


# the result


def _items_by_id(store: EvidenceStore, result: CustomerTrendsResult) -> dict[str, Any]:
    ids = list(dict.fromkeys(i for f in result.findings for i in f.evidence_ids))
    return {item.id: item for item in store.get_items(result.run_id, ids)}


def render_results(result: CustomerTrendsResult, settings: Settings) -> None:
    st.divider()
    st.header(f"Result of {result.run_id}")
    with EvidenceStore.from_settings(settings) as store:
        tabs = st.tabs(list(RESULT_TABS))
        with tabs[0]:
            _summary(result)
        with tabs[1]:
            _findings(result, _items_by_id(store, result))
        with tabs[2]:
            _themes(result)
        with tabs[3]:
            _trends(result)
        with tabs[4]:
            _evidence_explorer(result, store)
        with tabs[5]:
            _raw(result, store, settings)
        with tabs[6]:
            _provenance(result)


def _text(text: str) -> None:
    st.markdown(fmt.rtl_html(text), unsafe_allow_html=True)


def _summary(result: CustomerTrendsResult) -> None:
    summary = result.control_summary
    st.markdown(
        f"Status {fmt.badge(summary.status)}  "
        f"Confidence {fmt.badge(summary.overall_confidence.value)}"
    )
    _text(summary.headline)
    counts = st.columns(5)
    counts[0].metric("Findings", summary.findings_count)
    counts[1].metric("Evidence items", summary.evidence_count)
    counts[2].metric("Tool calls", int(summary.budget_used.get("tool_calls", 0)))
    counts[3].metric("Cost (USD)", f"{summary.budget_used.get('cost_usd', 0.0):.3f}")
    counts[4].metric("Seconds", f"{summary.budget_used.get('seconds', 0.0):.1f}")
    rows = fmt.coverage_rows(summary.coverage)
    if rows:
        st.subheader("Coverage")
        st.bar_chart(rows, x="name", y="items", color="by")
        st.dataframe(rows, hide_index=True)
    st.subheader("Gaps")
    for gap in summary.gaps or ["no gaps"]:
        (st.warning if summary.gaps else st.success)(gap)
    if summary.warnings:
        st.subheader("Warnings")
        for warning in summary.warnings:
            st.info(warning)


def _findings(result: CustomerTrendsResult, items: dict[str, Any]) -> None:
    if not result.findings:
        st.info("This run produced no findings.")
    for finding in result.findings:
        view = fmt.finding_view(finding, items)
        with st.container(border=True):
            st.markdown(
                f"**{view['id']}** :blue-badge[{view['type']}] "
                f"confidence {fmt.badge(view['confidence'])}"
            )
            _text(view["claim"])
            if view["metrics"]:
                st.json(view["metrics"], expanded=False)
            for caveat in view["caveats"]:
                st.caption(f"Caveat: {caveat}")
            for gap in view["related_gaps"]:
                st.caption(f"Related gap: {gap}")
            with st.expander(f"Evidence ({len(view['evidence'])})"):
                for cited in view["evidence"]:
                    if not cited["found"]:
                        st.caption(f"{cited['id']}: not found in the store")
                        continue
                    marker = " (snippet only)" if cited["snippet_only"] else ""
                    label = (
                        f"[{cited['platform']}]({cited['url']})"
                        if cited["url"]
                        else cited["platform"]
                    )
                    st.markdown(f"{label}{marker}")
                    _text(cited["text"])


def _themes(result: CustomerTrendsResult) -> None:
    aggregates = result.theme_aggregates
    if not aggregates:
        st.info("No themes were stored for this run.")
        return
    themes = fmt.theme_rows(aggregates)
    st.dataframe(themes, hide_index=True, width="stretch")
    counts = themes
    st.bar_chart(counts, x="theme", y="items")
    sentiment = fmt.sentiment_rows(aggregates)
    if sentiment:
        st.subheader("Sentiment mix")
        st.bar_chart(sentiment, x="theme", y="share", color="sentiment")
    left, right = st.columns(2)
    left.subheader("By platform")
    left.bar_chart(fmt.split_rows(aggregates, "by_platform"), x="theme", y="items", color="name")
    right.subheader("By language")
    right.bar_chart(fmt.split_rows(aggregates, "by_language"), x="theme", y="items", color="name")
    st.subheader("Representative quotes")
    for aggregate in aggregates:
        st.markdown(f"**{aggregate.theme_label}**")
        for quote in aggregate.representative_quotes:
            _text(quote)


def _trends(result: CustomerTrendsResult) -> None:
    if not result.trend_series:
        st.info("No search interest series were collected.")
        return
    for series, summary in zip(
        result.trend_series, fmt.trend_summaries(result.trend_series), strict=False
    ):
        st.subheader(series.keyword)
        st.caption(
            f"direction {summary['direction']}, first {summary['first']:g}, "
            f"last {summary['last']:g}, peak {summary['peak_value']:g} on {summary['peak_date']}"
        )
        st.line_chart(fmt.trend_points(series), x="date", y="interest")
        if series.related_queries:
            st.caption("Related queries: " + ", ".join(series.related_queries))


def _evidence_explorer(result: CustomerTrendsResult, store: EvidenceStore) -> None:
    left, middle, right = st.columns(3)
    platform = left.selectbox("Platform", ["any", *[p.value for p in Platform]], key="ev_platform")
    source = middle.selectbox(
        "Source type", ["any", *[s.value for s in SourceType]], key="ev_source"
    )
    language = right.selectbox("Language", ["any", "ar", "en"], key="ev_language")
    text = st.text_input("Text contains", key="ev_text")
    minimum = st.number_input("Minimum engagement", min_value=0, step=5, key="ev_min")
    filters = EvidenceFilters(
        platform=None if platform == "any" else Platform(platform),
        source_type=None if source == "any" else SourceType(source),
        language=None if language == "any" else language,
        text_contains=text or None,
        min_engagement=int(minimum) or None,
    )
    found = store.query(result.run_id, filters, limit=EVIDENCE_LIMIT, sample="top")
    st.caption(f"{found.total} matching items, the {len(found.items)} most engaged shown")
    st.dataframe(fmt.evidence_rows(found.items), hide_index=True, width="stretch")
    if found.items:
        chosen = st.selectbox("Open an item", [i.id for i in found.items], key="ev_open")
        item = next(i for i in found.items if i.id == chosen)
        with st.expander("Full text", expanded=True):
            _text(item.text)
            st.caption(f"{item.url or 'no url'}  |  provider {item.provider}")


def _raw(result: CustomerTrendsResult, store: EvidenceStore, settings: Settings) -> None:
    result_json = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
    st.subheader("CustomerTrendsResult")
    st.download_button(
        "Download CustomerTrendsResult", result_json, f"{result.run_id}.json", key="dl_result"
    )
    st.code(result_json, language="json", height=320)
    st.subheader("WorkerResult (the shared contract)")
    task = forms.task_from_brief(result.brief)
    cited = list(dict.fromkeys(i for f in result.findings for i in f.evidence_ids))
    shared = to_worker_result(
        result,
        store.get_items(result.run_id, cited),
        task=task,
        location=store.result_location(result.run_id),
        synthetic=settings.edrak_provider_mode == "fixture",
    )
    shared_json = shared.model_dump_json(indent=2)
    st.download_button(
        "Download WorkerResult", shared_json, f"{result.run_id}.worker.json", key="dl_worker"
    )
    st.code(shared_json, language="json", height=320)


def _provenance(result: CustomerTrendsResult) -> None:
    for title, rows in fmt.provenance_rows(result).items():
        st.subheader(title.capitalize())
        if rows:
            st.dataframe(rows, hide_index=True)
        else:
            st.caption("none")
