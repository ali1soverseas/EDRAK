from types import SimpleNamespace

import pytest
from edrak.contracts.evidence import Evidence, SourceType
from edrak.graph.connection import GraphSettings
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

    def available(self):
        return self.is_available

    def find_findings(self, *args, **kwargs):
        return ["finding"]

    def evidence_from_findings(self, findings, source_types=None):
        return [Evidence(source_type=SourceType.WEB_PAGE, extracted_fact="graph")]

    def findings_citing_sources(self, *args):
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


@pytest.mark.asyncio
async def test_run_manager_ingests_workspace_state(monkeypatch):
    from edrak.api.run_manager import RunManager
    from edrak.graph.ingestion.pipeline import GraphIngestor, IngestReport

    captured = {}

    async def ingest(self, request, **kwargs):
        captured.update(kwargs)
        return IngestReport(request_id=request.request_id)

    monkeypatch.setattr(GraphIngestor, "aingest_run", ingest)
    monkeypatch.setattr("edrak.graph.connection.get_graph_settings", lambda: SimpleNamespace(GRAPH_ENABLED=True))
    run = load_runs()[0]
    active = SimpleNamespace(workspace_id="ws-a", requested_by_id="user-a")
    state = {"results": run.results, "verification_result": run.verification}
    await RunManager()._record_graph(active, run.request, state, run.plan)
    assert captured["workspace_id"] == "ws-a"
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
