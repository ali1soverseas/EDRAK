"""Secure HTTP Client for Dynamic REST API Execution."""

import logging
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)


class SecureAPIExecutor:
    """Executes dynamically selected REST operations with strict security policies."""

    def __init__(
        self,
        base_url: str,
        auth_headers: Optional[Dict[str, str]] = None,
        app: Optional[Any] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_headers = auth_headers or {}
        self.app = app

    def _prepare_request(self, path: str, method: str) -> tuple[str, Dict[str, str]]:
        if method.upper() not in ("GET", "HEAD"):
            raise PermissionError(f"HTTP method '{method}' is prohibited. Only GET/HEAD read operations are allowed.")

        if not path.startswith("/"):
            path = "/" + path

        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "EDRAK-InternalIntelligenceAgent/1.0",
            **self.auth_headers,
        }
        return url, headers

    async def execute_endpoint(
        self,
        path: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Any:
        """Safely executes an async HTTP GET request without leaking secrets."""
        url, headers = self._prepare_request(path, method)

        if self.app:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url or "http://testserver", timeout=timeout) as client:
                response = await client.request(method=method.upper(), url=path, headers=headers, params=params)
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return response.text

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method=method.upper(), url=url, headers=headers, params=params)
            response.raise_for_status()
            try:
                return response.json()
            except Exception:
                return response.text

    def execute_endpoint_sync(
        self,
        path: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Any:
        """Synchronously executes an HTTP GET request."""
        url, headers = self._prepare_request(path, method)

        if self.app:
            from fastapi.testclient import TestClient
            with TestClient(self.app, base_url=self.base_url or "http://testserver") as client:
                response = client.request(method=method.upper(), url=path, headers=headers, params=params)
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return response.text

        with httpx.Client(timeout=timeout) as client:
            response = client.request(method=method.upper(), url=url, headers=headers, params=params)
            response.raise_for_status()
            try:
                return response.json()
            except Exception:
                return response.text
