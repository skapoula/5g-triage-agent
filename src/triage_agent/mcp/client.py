"""Async MCP client for Prometheus and Loki."""

import asyncio
import logging
from typing import Any

import httpx

from triage_agent.config import get_config

logger = logging.getLogger(__name__)


class MCPQueryError(Exception):
    """Raised when an MCP query fails."""

    pass


class MCPTimeoutError(MCPQueryError):
    """Raised when an MCP query times out."""

    pass


class MCPClient:
    """Async client for Prometheus and Loki via MCP protocol."""

    def __init__(
        self,
        prometheus_url: str | None = None,
        loki_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        config = get_config()
        self.prometheus_url = prometheus_url or config.prometheus_url
        self.loki_url = loki_url or config.loki_url
        self.timeout = timeout or config.mcp_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def query_prometheus(
        self,
        query: str,
        time: int | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Execute instant PromQL query."""
        client = await self._get_client()
        params: dict[str, str | int] = {"query": query}
        if time:
            params["time"] = time

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                logger.debug(f"Prometheus query: {query}")
                response = await client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("status") != "success":
                    raise MCPQueryError(f"Prometheus error: {data.get('error')}")
                result: dict[str, Any] = data.get("data", {})
                return result
            except httpx.TimeoutException as e:
                raise MCPTimeoutError(f"Prometheus query timed out: {query}") from e
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    last_error = e
                    continue
                raise MCPQueryError(f"Prometheus HTTP error: {e}") from e

        if last_error:
            raise MCPQueryError(f"Max retries exceeded for query: {query}") from last_error
        raise MCPQueryError(f"Max retries exceeded for query: {query}")

    async def query_prometheus_range(
        self,
        query: str,
        start: int,
        end: int,
        step: str = "15s",
    ) -> dict[str, Any]:
        """Execute range PromQL query."""
        client = await self._get_client()
        params: dict[str, str | int] = {
            "query": query,
            "start": start,
            "end": end,
            "step": step,
        }

        try:
            logger.debug(f"Prometheus range query: {query} [{start}:{end}]")
            response = await client.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "success":
                raise MCPQueryError(f"Prometheus error: {data.get('error')}")
            result: dict[str, Any] = data.get("data", {})
            return result
        except httpx.TimeoutException as e:
            raise MCPTimeoutError("Prometheus range query timed out") from e
        except httpx.HTTPStatusError as e:
            raise MCPQueryError(f"Prometheus HTTP error: {e}") from e

    async def query_loki(
        self,
        logql: str,
        start: int,
        end: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Execute LogQL query."""
        client = await self._get_client()
        params: dict[str, str | int] = {
            "query": logql,
            "start": start * 1_000_000_000,  # Convert to nanoseconds
            "end": end * 1_000_000_000,
            "limit": limit,
        }

        try:
            logger.debug(f"Loki query: {logql}")
            response = await client.get(
                f"{self.loki_url}/loki/api/v1/query_range",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            # Parse Loki response format
            logs: list[dict[str, Any]] = []
            for stream in data.get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                for value in stream.get("values", []):
                    logs.append(
                        {
                            "timestamp": int(value[0]) // 1_000_000_000,
                            "message": value[1],
                            "labels": labels,
                            "pod": labels.get("pod", ""),
                            "level": self._extract_log_level(value[1]),
                        }
                    )
            return logs
        except httpx.TimeoutException as e:
            raise MCPTimeoutError("Loki query timed out") from e
        except httpx.HTTPStatusError as e:
            raise MCPQueryError(f"Loki HTTP error: {e}") from e

    def _extract_log_level(self, message: str) -> str:
        """Extract log level from message."""
        message_upper = message.upper()
        for level in ["FATAL", "ERROR", "WARN", "INFO", "DEBUG"]:
            if level in message_upper:
                return level
        return "INFO"

    async def health_check_prometheus(self) -> bool:
        """Check Prometheus availability."""
        try:
            result = await self.query_prometheus("up")
            return bool(result.get("result"))
        except Exception:
            return False

    async def health_check_loki(self) -> bool:
        """Check Loki availability."""
        client = await self._get_client()
        try:
            response = await client.get(f"{self.loki_url}/ready")
            return response.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "MCPClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()
