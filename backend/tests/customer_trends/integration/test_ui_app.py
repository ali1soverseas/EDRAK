"""The developer UI under Streamlit's AppTest, in fixture and fake-LLM mode."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from edrak.agents.customer_trends.settings import get_settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import load_brief

APP = Path(__file__).resolve().parents[3] / "src/edrak/agents/customer_trends/ui/app.py"
RESULT_TABS = [
    "Summary",
    "Findings",
    "Themes",
    "Trends",
    "Evidence explorer",
    "Raw JSON",
    "Provenance",
]


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("EDRAK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("EDRAK_PROVIDER_MODE", "fixture")
    monkeypatch.setenv("EDRAK_FAKE_LLM", "true")
    get_settings.cache_clear()
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    return at


def run_count(app: AppTest) -> int:
    with EvidenceStore.from_settings(get_settings()) as store:
        return len(store.list_runs())


def test_the_app_loads_with_the_pilot_preset_and_no_errors(app: AppTest) -> None:
    assert not app.exception
    assert app.title[0].value == "Customer and Trends worker"
    assert app.text_input(key="f_entity").value == "GitLab"
    assert app.selectbox(key="f_use_case").value == "competitive_intelligence"
    assert app.multiselect(key="f_focus").value == ["pain_points", "sentiment", "competitor_gaps"]
    assert app.button(key="run_start").disabled is False


def test_the_sidebar_shows_the_modes_and_never_a_key_value(app: AppTest) -> None:
    assert app.sidebar.radio(key="sb_provider_mode").value == "fixture"
    assert app.sidebar.toggle(key="sb_fake_llm").value is True
    statuses = [row for table in app.sidebar.table for row in table.value["status"].tolist()]
    assert statuses and set(statuses) == {"missing"}
    assert "gpt-oss" in app.sidebar.caption[0].value


def test_a_preset_button_fills_the_form(app: AppTest) -> None:
    app.button(key="preset_Product launch: budgeting app for freelancers in Egypt").click().run()
    assert not app.exception
    assert app.text_input(key="f_entity").value == "Budgeting app for freelancers"
    assert app.selectbox(key="f_use_case").value == "product_launch"
    assert app.text_input(key="f_geo").value == "EG"
    assert app.multiselect(key="f_languages").value == ["ar", "en"]
    assert app.text_input(key="f_competitors").value == "YNAB, Wallet"
    assert app.session_state["f_run_id"] == load_brief("product_launch").run_id


def test_pasted_json_is_validated_and_loaded_into_the_form(app: AppTest) -> None:
    app.text_area(key="f_json").set_value('{"task_id": "t",')
    app.button(key="f_json_use").click().run()
    assert any("not valid JSON" in error.value for error in app.error)
    assert app.text_input(key="f_entity").value == "GitLab", "the form is left as it was"

    brief = load_brief("market_entry")
    app.text_area(key="f_json").set_value(brief.model_dump_json())
    app.button(key="f_json_use").click().run()
    assert not app.error and not app.exception
    assert app.text_input(key="f_entity").value == "Specialty coffee delivery"
    assert app.selectbox(key="f_depth").value == "light"


def test_a_form_that_is_not_a_valid_brief_shows_why_and_cannot_run(app: AppTest) -> None:
    app.text_input(key="f_entity").set_value("")
    app.run()
    assert any("entity:" in error.value for error in app.error)
    assert app.button(key="run_start").disabled is True


def test_a_run_completes_and_every_result_tab_renders(app: AppTest) -> None:
    app.button(key="run_start").click().run(timeout=120)
    assert not app.exception and not app.error
    assert [t.label for t in app.tabs][-7:] == RESULT_TABS
    assert "Run run-ci-gitlab-001: finished" in [s.value for s in app.subheader]
    assert app.session_state["ct_result"].control_summary.status == "complete"
    assert [m.label for m in app.metric][:2] == ["Findings", "Evidence items"]
    assert app.metric[0].value == "4"
    assert any('dir="rtl"' in markdown.value for markdown in app.markdown), (
        "Arabic renders right to left"
    )
    assert any(s.value == "Coverage" for s in app.subheader)
    assert len(app.code) >= 2, "the CustomerTrendsResult and the WorkerResult are shown"
    assert any('"worker": "customer_trends"' in code.value for code in app.code)


def test_running_again_makes_a_new_run_instead_of_returning_the_old_one(app: AppTest) -> None:
    app.button(key="run_start").click().run(timeout=120)
    app.button(key="run_start").click().run(timeout=120)
    assert not app.exception
    assert "Run run-ci-gitlab-001-2: finished" in [s.value for s in app.subheader]
    assert run_count(app) == 2


def test_loading_a_past_run_shows_it_without_running_anything(app: AppTest) -> None:
    app.button(key="run_start").click().run(timeout=120)
    app.button(key="preset_Product launch: budgeting app for freelancers in Egypt").click().run()
    app.button(key="run_start").click().run(timeout=120)
    assert run_count(app) == 2
    app.sidebar.selectbox(key="sb_history_run").set_value("run-ci-gitlab-001").run()
    app.sidebar.button(key="sb_history_load").click().run()
    assert not app.exception
    assert "Result of run-ci-gitlab-001" in [h.value for h in app.header]
    assert app.session_state["ct_controller"] is None
    assert run_count(app) == 2, "nothing was run"
