"""MCP client and adapter layer for EDRAK intelligence workers."""

import json
import logging
from typing import Any, Dict, List, Optional

from edrak.contracts.evidence import Evidence
from edrak.rag.retriever import InternalRetriever

logger = logging.getLogger(__name__)


class InternalDataToolClient:
    """Client for querying internal RAG data, providing a uniform tool interface.

    Allows workers to execute tool calls in-process or route to MCP server.
    """

    def __init__(self, retriever: Optional[InternalRetriever] = None):
        self._retriever = retriever or InternalRetriever()

    def search_internal_knowledge(
        self,
        query: str,
        workspace_id: str,
        top_k: int = 4,
        doc_type: Optional[str] = None,
    ) -> List[Evidence]:
        """Queries one workspace's internal knowledge and returns typed Evidence items."""
        where_filter = {"doc_type": doc_type} if doc_type else None
        return self._retriever.retrieve(query=query, top_k=top_k, where_filter=where_filter, workspace_id=workspace_id)

    def get_document_by_id(self, chunk_id: str, workspace_id: str) -> Optional[Evidence]:
        """Fetches a specific chunk by its ID, if it belongs to the workspace."""
        return self._retriever.get_document_by_id(chunk_id, workspace_id)
