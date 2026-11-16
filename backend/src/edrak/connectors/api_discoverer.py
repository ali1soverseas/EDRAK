"""Dynamic OpenAPI 3.x / Swagger 2.0 API Discoverer."""

import logging
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)


class DynamicAPIDiscoverer:
    """Parses OpenAPI 3.x / Swagger 2.0 specifications to dynamically discover read-safe operations."""

    def __init__(self, spec: Dict[str, Any], base_url: Optional[str] = None):
        self.spec = spec
        self.base_url = (base_url or self._extract_base_url()).rstrip("/")
        self.endpoints = self._index_endpoints()

    def _extract_base_url(self) -> str:
        servers = self.spec.get("servers", [])
        if servers and isinstance(servers, list) and "url" in servers[0]:
            return servers[0]["url"]
        return ""

    def _index_endpoints(self) -> List[Dict[str, Any]]:
        """Indexes all read-safe (GET) operations across the OpenAPI spec."""
        endpoints = []
        paths = self.spec.get("paths", {})
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in ("get", "head"):
                    continue
                if not isinstance(operation, dict):
                    continue

                endpoints.append({
                    "operation_id": operation.get("operationId", f"{method.upper()}_{path}"),
                    "path": path,
                    "method": method.upper(),
                    "summary": operation.get("summary", ""),
                    "description": operation.get("description", ""),
                    "parameters": operation.get("parameters", []),
                    "responses": operation.get("responses", {}),
                })
        return endpoints

    def match_endpoints_for_task(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Filters endpoints using keyword / semantic scoring against task requirements."""
        if not self.endpoints:
            return []

        scored = []
        terms = {term.lower() for term in query.split() if len(term) > 2}
        for ep in self.endpoints:
            haystack = f"{ep['path']} {ep['summary']} {ep['description']} {ep['operation_id']}".lower()
            # Score based on term matches
            score = sum(1 for term in terms if term in haystack)
            if score > 0:
                scored.append((score, ep))

        scored.sort(key=lambda x: x[0], reverse=True)
        # If no direct matches, return top endpoints available
        if not scored:
            return self.endpoints[:top_k]

        return [ep for _, ep in scored[:top_k]]

    @classmethod
    def from_url(cls, spec_url: str, base_url: Optional[str] = None, timeout: float = 10.0) -> "DynamicAPIDiscoverer":
        """Fetches OpenAPI spec from a remote or local HTTP URL."""
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(spec_url)
            resp.raise_for_status()
            spec = resp.json()
        return cls(spec=spec, base_url=base_url)
