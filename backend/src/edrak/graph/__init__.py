"""EDRAK knowledge graph (Neo4j).

    connection        -> GraphConnection, get_connection, GraphSettings
    ingestion         -> GraphIngestor, record_run          (contracts -> graph)
    retrieval         -> GraphRetriever, HybridRetriever    (graph -> workers)
                         assessment_history, theme_history, trend_history   (run-scoped observations)
    agent_context     -> prior_knowledge_for_task, GraphAwareWorker, wrap_registry
    loader            -> python -m edrak.graph.loader       (loads sample_data.json)

Exports are lazy so that importing one submodule never imports the Neo4j driver
or ChromaDB, and a missing optional dependency only fails where it is used.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "GraphConnection": "edrak.graph.connection",
    "GraphSettings": "edrak.graph.connection",
    "GraphUnavailable": "edrak.graph.connection",
    "get_connection": "edrak.graph.connection",
    "GraphIngestor": "edrak.graph.ingestion.pipeline",
    "IngestReport": "edrak.graph.ingestion.pipeline",
    "record_run": "edrak.graph.ingestion.pipeline",
    "GraphRetriever": "edrak.graph.retrieval.graph_retriever",
    "HybridRetriever": "edrak.graph.retrieval.hybrid_retriever",
    "assessment_history": "edrak.graph.retrieval.history",
    "theme_history": "edrak.graph.retrieval.history",
    "trend_history": "edrak.graph.retrieval.history",
    "PriorKnowledge": "edrak.graph.agent_context",
    "prior_knowledge_for_task": "edrak.graph.agent_context",
    "GraphAwareWorker": "edrak.graph.agent_context",
    "wrap_registry": "edrak.graph.agent_context",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'edrak.graph' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
