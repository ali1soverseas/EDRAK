"""The developer UI: exercise the worker as if a task came from the orchestrator and look at
exactly what it hands downstream. Start it from `backend/` with `make ui`.

It is a harness inside the worker package, not the product frontend. The graph runs on a
background thread; this script polls its events and never blocks. Loading a past run reads the
sink and does not run anything.
"""

import time

import streamlit as st

from edrak.agents.customer_trends.logging import configure_logging
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.settings import Settings, get_settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink
from edrak.agents.customer_trends.ui.components import forms, views
from edrak.agents.customer_trends.ui.components.controller import RunController

POLL_SECONDS = 0.4


@st.cache_resource
def _setup_logging() -> bool:
    configure_logging(get_settings())
    return True


def _read(settings: Settings, run_id: str) -> CustomerTrendsResult | None:
    with EvidenceStore.from_settings(settings) as store:
        try:
            return LocalSink(store, settings.artifacts_dir).read_result(run_id)
        except (OSError, ValueError):
            return None


def main() -> None:
    st.set_page_config(page_title="Customer and Trends worker", layout="wide")
    _setup_logging()
    settings = get_settings()
    presets = forms.load_presets()
    views.init_form(presets)
    with EvidenceStore.from_settings(settings) as store:
        runs = store.list_runs()
    choice = views.render_sidebar(settings, runs)
    run_settings = settings.model_copy(
        update={"edrak_provider_mode": choice.provider_mode, "edrak_fake_llm": choice.fake_llm}
    )

    st.title("Customer and Trends worker")
    st.caption("Developer harness: run a task brief and inspect what the worker produces.")
    brief, problems = views.render_form(presets)
    for problem in problems:
        st.error(problem)

    controller: RunController | None = st.session_state.get("ct_controller")
    running = controller is not None and controller.running
    if st.button("Run", type="primary", disabled=brief is None or running, key="run_start"):
        assert brief is not None  # noqa: S101  # the button is disabled without a brief
        unique = forms.unique_run_id(brief.run_id, {r.run_id for r in runs})
        started = brief.model_copy(update={"run_id": unique})
        controller = RunController(started, run_settings)
        st.session_state.update(ct_controller=controller, ct_result=None, ct_finished=None)
        controller.start()
        running = True

    requested = st.session_state.pop("ct_load_run", None)
    if requested:
        st.session_state.update(ct_result=_read(run_settings, requested), ct_controller=None)
        controller = None

    if controller is not None:
        controller.drain()
        views.render_run_panel(controller, controller.brief)
        if running and controller.running:
            time.sleep(POLL_SECONDS)
            st.rerun()
        if st.session_state.get("ct_finished") != controller.brief.run_id:
            controller.drain()
            st.session_state["ct_finished"] = controller.brief.run_id
            st.session_state["ct_result"] = (
                None if controller.stopped else _read(run_settings, controller.brief.run_id)
            )
            if controller.stopped:
                st.info("The run was stopped, so no result was written for it.")
            st.rerun()

    result = st.session_state.get("ct_result")
    if result is not None:
        views.render_results(result, run_settings)


main()
