import re
from pathlib import Path

import pytest

from edrak.agents.customer_trends.graph import NODES, build_graph
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import load_brief
from tests.customer_trends.graph_helpers import worker_deps

ARCHITECTURE = (
    Path(__file__).resolve().parents[3] / "src/edrak/agents/customer_trends/docs/ARCHITECTURE.md"
)
EDGE = re.compile(r"^\s*(\w+)\s+(-->|-\.->)(?:\|[^|]*\|)?\s+(\w+)\s*$")
ALIASES = {"START": "__start__", "END": "__end__"}


def documented_edges() -> set[tuple[str, str]]:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    diagram = text.split("```mermaid\n", 1)[1].split("```", 1)[0]
    edges = set()
    for line in diagram.splitlines():
        match = EDGE.match(line)
        if match:
            source, _, target = match.groups()
            edges.add((ALIASES.get(source, source), ALIASES.get(target, target)))
    return edges


@pytest.fixture
def compiled(tmp_path: Path, store: EvidenceStore):  # type: ignore[no-untyped-def]  # a test fixture
    return build_graph(worker_deps(load_brief(), tmp_path, store))


def test_the_documented_diagram_is_the_compiled_graph(compiled) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    drawn = compiled.get_graph()
    assert {(edge.source, edge.target) for edge in drawn.edges} == documented_edges()


def test_the_graph_has_the_ten_nodes_of_the_spec(compiled) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    assert set(NODES) == {
        "intake",
        "plan_queries",
        "social",
        "demand",
        "reviews",
        "join",
        "analyze",
        "gap_check",
        "write_findings",
        "submit",
    }
    assert set(compiled.get_graph().nodes) == set(NODES) | {"__start__", "__end__"}


def test_only_the_replan_edge_is_conditional(compiled) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    conditional = {(e.source, e.target) for e in compiled.get_graph().edges if e.conditional}
    assert conditional == {("gap_check", "plan_queries"), ("gap_check", "write_findings")}


def test_the_node_table_in_the_docs_names_every_node() -> None:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    table = text.split("| Node | Kind |", 1)[1].split("**State.**", 1)[0]
    for node in NODES:
        assert f"`{node}`" in table, node
