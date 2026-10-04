"""Internal Data MCP Server for EDRAK.

Exposes RAG retrieval, internal handbook search, and internal document tools
via the Model Context Protocol (MCP).
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

# Ensure backend package is in python path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from edrak.contracts.evidence import Evidence
from edrak.core.config import settings
from edrak.rag.indexer import InternalIndexer
from edrak.rag.retriever import InternalRetriever

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-internal-data")

app = Server("edrak-internal-data-server")
retriever = InternalRetriever()
indexer = InternalIndexer()


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Lists the internal data tools available to agents."""
    return [
        Tool(
            name="search_internal_knowledge",
            description=(
                "Search internal company documentation, handbook, product architecture, "
                "roadmaps, pricing tiers, and telemetry using semantic vector retrieval."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Semantic search query describing the internal information needed.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of relevant evidence snippets to retrieve (default: 4).",
                        "default": 4,
                    },
                    "doc_type": {
                        "type": "string",
                        "description": "Optional filter: 'internal_handbook', 'internal_doc', 'product', 'pricing', 'telemetry'.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_internal_document_by_id",
            description="Retrieve an exact internal document chunk by its unique ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "The unique ID of the chunk to retrieve.",
                    }
                },
                "required": ["chunk_id"],
            },
        ),
        Tool(
            name="index_internal_data",
            description="Indexes all internal documents from the data/internal folder into vector store.",
            inputSchema={
                "type": "object",
                "properties": {
                    "force_reset": {
                        "type": "boolean",
                        "description": "Whether to reset the collection before indexing.",
                        "default": False,
                    }
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handles execution of tool calls."""
    logger.info("Executing tool %s with args %s", name, arguments)

    if name == "search_internal_knowledge":
        query = arguments.get("query", "")
        top_k = int(arguments.get("top_k", 4))
        doc_type = arguments.get("doc_type")
        where_filter = {"doc_type": doc_type} if doc_type else None

        evidence_items = retriever.retrieve(query=query, top_k=top_k, where_filter=where_filter)
        results = [ev.model_dump(mode="json") for ev in evidence_items]

        return [TextContent(type="text", text=json.dumps(results, indent=2))]

    elif name == "get_internal_document_by_id":
        chunk_id = arguments.get("chunk_id", "")
        ev = retriever.get_document_by_id(chunk_id)
        if ev:
            return [TextContent(type="text", text=json.dumps(ev.model_dump(mode="json"), indent=2))]
        return [TextContent(type="text", text=json.dumps({"error": f"Document chunk {chunk_id} not found."}))]

    elif name == "index_internal_data":
        force_reset = arguments.get("force_reset", False)
        if force_reset:
            indexer.reset_collection()

        internal_dir = settings.get_absolute_internal_data_path()
        count = indexer.index_directory(internal_dir)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "indexed_chunks": count,
                        "source_directory": str(internal_dir),
                    },
                    indent=2,
                ),
            )
        ]

    return [TextContent(type="text", text=json.dumps({"error": f"Tool '{name}' not found."}))]


async def main():
    """Runs the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
