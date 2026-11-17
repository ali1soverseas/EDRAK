from types import SimpleNamespace

import pytest
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerType,
)
from edrak.contracts.evidence import Evidence, SourceType
from edrak.graph.connection import GraphSettings, effective_graph_workspace_id
from edrak.graph.loader import load_runs
from edrak.graph.retrieval.graph_retriever import GraphRetriever
from edrak.graph.retrieval.hybrid_retriever import HybridRetriever


class Vector:
    def __init__(self):
        self.calls = []
        self.evidence = Evidence(source_type=SourceType.INTERNAL_DOCUMENT, extracted_fact="vector")

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return [self.evidence]


class Graph:
    def __init__(self, available=True):
        self.is_available = available
        self.workspace_ids = []

    def available(self):
        return self.is_available

    def find_findings(self, workspace_id, *args, **kwargs):
        self.workspace_ids.append(workspace_id)
        return ["finding"]

    def evidence_from_findings(self, findings, source_types=None):
        return [Evidence(source_type=SourceType.WEB_PAGE, extracted_fact="graph")]

    def findings_citing_sources(self, workspace_id, *args):
        self.workspace_ids.append(workspace_id)
        return {}


def test_loader_imports_and_validates_sample():
    runs = load_runs()
    assert len(runs) == 3
    sample = runs[-1]
    assert sample.request.request_id == "sample-profile-nvidia-2026-01"
    assert sample.profile_snapshot is not None
    assert len(sample.profile_snapshot.markets) == 20
    assert all(m.relation == "serves" for m in sample.profile_snapshot.markets)
    assert {m.name for m in sample.profile_snapshot.markets} == set(sample.request.business_context.markets)
    assert all(e.is_synthetic for r in sample.results for e in r.evidence)
    assert sample.catalog == []


def test_profile_snapshot_is_scoped_and_preserves_market_relations():
    from edrak.graph.profile_snapshot import ProfileSnapshot, ingest_profile_snapshot

    class Connection:
        def write(self, query, **params):
            self.query, self.params = query, params

    conn = Connection()
    snapshot = ProfileSnapshot.model_validate({
        "industries": ["technology"], "goals": ["Expand"],
        "markets": [{"name": "Japan", "relation": "serves"}, {"name": "Canada", "relation": "considers"}],
    })
    ingest_profile_snapshot(conn, snapshot, "workspace-a", "request-a", "NVIDIA")
    assert conn.params["ws"] == "workspace-a"
    assert conn.params["request_id"] == "request-a"
    assert [m["relation"] for m in conn.params["markets"]] == ["serves", "considers"]
    assert "PROFILE_MARKET" in conn.query
    other = Connection()
    ingest_profile_snapshot(other, snapshot, "workspace-b", "request-a", "NVIDIA")
    assert conn.params["id"] != other.params["id"]
    with pytest.raises(ValueError):
        ProfileSnapshot.model_validate({"markets": [
            {"name": "Japan", "relation": "serves"}, {"name": "Japan", "relation": "considers"}
        ]})


def test_settings_find_root_env_from_another_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert GraphSettings.model_config["env_file"].name == ".env"
    assert GraphSettings.model_config["env_file"].parent.joinpath("requirements.txt").is_file()


def test_graph_workspace_override_defaults_to_request_workspace(monkeypatch):
    from edrak.graph import connection

    monkeypatch.setattr(
        connection, "get_graph_settings", lambda: SimpleNamespace(GRAPH_WORKSPACE_ID="sandbox")
    )
    assert effective_graph_workspace_id("workspace-a") == "sandbox"

    monkeypatch.setattr(
        connection, "get_graph_settings", lambda: SimpleNamespace(GRAPH_WORKSPACE_ID=" ")
    )
    assert effective_graph_workspace_id("workspace-a") == "workspace-a"


def test_prior_knowledge_reads_from_graph_workspace_override(monkeypatch):
    from edrak.graph import connection
    from edrak.graph.agent_context import prior_knowledge_for_task

    monkeypatch.setattr(
        connection, "get_graph_settings", lambda: SimpleNamespace(GRAPH_WORKSPACE_ID="sandbox")
    )
    task = ResearchTask(
        parent_request_id="run-a",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Research NVIDIA GPU.",
        focus="GPU architecture",
        company_profile=CompanyProfile(name="NVIDIA", workspace_id="workspace-a"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            offering="GPU",
        ),
    )

    class Retriever:
        def __init__(self):
            self.workspace_ids = []

        def available(self):
            return True

        def find_findings(self, workspace_id, **_kwargs):
            self.workspace_ids.append(workspace_id)
            return []

        def signals(self, workspace_id, **_kwargs):
            self.workspace_ids.append(workspace_id)
            return []

        def open_gaps(self, workspace_id, **_kwargs):
            self.workspace_ids.append(workspace_id)
            return []

        def metric_history(self, workspace_id, **_kwargs):
            self.workspace_ids.append(workspace_id)
            return []

    retriever = Retriever()
    prior_knowledge_for_task(task, retriever=retriever)

    assert len(retriever.workspace_ids) >= 4
    assert set(retriever.workspace_ids) == {"sandbox"}


def test_hybrid_preserves_positional_arguments_and_vector_only_when_disabled():
    vector = Vector()
    result = HybridRetriever(vector=vector, graph=Graph(False)).retrieve("query", 4, None, 0.7, "ws")
    assert vector.calls[0]["min_score"] == 0.7
    assert vector.calls[0]["workspace_id"] == "ws"
    assert vector.calls[0]["top_k"] == 4
    assert result == [vector.evidence]
    assert not result[0].metadata


def test_filtered_hybrid_search_cannot_include_unfiltered_graph_evidence():
    vector = Vector()
    result = HybridRetriever(vector=vector, graph=Graph()).retrieve(
        "query", where_filter={"origin": "public_handbook"}, workspace_id="ws"
    )
    assert [item.extracted_fact for item in result] == ["vector"]
    assert vector.calls[0]["where_filter"] == {"origin": "public_handbook"}


def test_hybrid_routes_only_neo4j_reads_to_graph_workspace_override(monkeypatch):
    from edrak.graph import connection

    monkeypatch.setattr(
        connection, "get_graph_settings", lambda: SimpleNamespace(GRAPH_WORKSPACE_ID="sandbox")
    )
    vector = Vector()
    graph = Graph()

    HybridRetriever(vector=vector, graph=graph).retrieve("query", workspace_id="workspace-a")

    assert vector.calls[0]["workspace_id"] == "workspace-a"
    assert graph.workspace_ids == ["sandbox", "sandbox"]


def test_entity_and_neighbourhood_reads_require_workspace():
    class Connection:
        enabled = True

        def __init__(self):
            self.calls = []

        def read(self, query, **params):
            self.calls.append((query, params))
            return []

    conn = Connection()
    retriever = GraphRetriever(conn)
    assert retriever.resolve_entities(["NVIDIA"]) == []
    assert retriever.neighbourhood("nvidia") == {}
    assert not conn.calls
    retriever.resolve_entities(["NVIDIA"], "ws-a")
    retriever.neighbourhood("nvidia", "ws-a")
    assert [params["ws"] for _, params in conn.calls] == ["ws-a", "ws-a"]


@pytest.mark.parametrize(
    ("override", "expected_workspace"),
    [(None, "ws-a"), ("sandbox", "sandbox")],
)
@pytest.mark.asyncio
async def test_run_manager_ingests_workspace_state(monkeypatch, override, expected_workspace):
    from edrak.api.run_manager import RunManager
    from edrak.graph.ingestion.pipeline import GraphIngestor, IngestReport

    captured = {}

    async def ingest(self, request, **kwargs):
        captured.update(kwargs)
        return IngestReport(request_id=request.request_id)

    monkeypatch.setattr(GraphIngestor, "aingest_run", ingest)
    monkeypatch.setattr(
        "edrak.graph.connection.get_graph_settings",
        lambda: SimpleNamespace(GRAPH_ENABLED=True, GRAPH_WORKSPACE_ID=override),
    )
    run = load_runs()[0]
    active = SimpleNamespace(workspace_id="ws-a", requested_by_id="user-a")
    state = {"results": run.results, "verification_result": run.verification}
    await RunManager()._record_graph(active, run.request, state, run.plan)
    assert captured["workspace_id"] == expected_workspace
    assert captured["user_id"] == "user-a"
    assert captured["results"] == run.results
    assert captured["verification"] == run.verification


@pytest.mark.asyncio
async def test_run_manager_does_not_ingest_without_workspace(monkeypatch, caplog):
    from edrak.api.run_manager import RunManager

    monkeypatch.setattr("edrak.graph.connection.get_graph_settings", lambda: SimpleNamespace(GRAPH_ENABLED=True))
    run = load_runs()[0]
    active = SimpleNamespace(workspace_id=None, requested_by_id=None)
    await RunManager()._record_graph(active, run.request, {}, run.plan)
    assert "no workspace ID" in caplog.text
