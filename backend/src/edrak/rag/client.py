"""One Chroma client per vector-store folder, shared by every indexer and retriever in the process.

Opening several PersistentClients for the same folder at the same moment (four files uploaded together
are indexed in parallel threads) races inside Chroma's client cache and fails with errors such as a
KeyError on the path or "'RustBindingsAPI' object has no attribute 'bindings'". So the client is created
once, under a lock, and writes go through WRITE_LOCK one at a time.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

_clients: dict[str, Any] = {}
_lock = threading.Lock()
WRITE_LOCK = threading.RLock()


def get_client(path: Path) -> Any:
    key = str(Path(path).resolve())
    with _lock:
        client = _clients.get(key)
        if client is None:
            Path(key).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=key, settings=ChromaSettings(anonymized_telemetry=False))
            _clients[key] = client
        return client
