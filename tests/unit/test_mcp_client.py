"""Tests for MCP client."""

import pytest
from pytest_httpx import HTTPXMock

from triage_agent.mcp.client import MCPClient, MCPQueryError


class TestMCPClientPrometheus:
    """Tests for Prometheus queries via MCP client."""

    @pytest.mark.asyncio
    async def test_query_prometheus_success(self, httpx_mock: HTTPXMock) -> None:
        """Test successful Prometheus instant query."""
        httpx_mock.add_response(
            url="http://test-prometheus:9090/api/v1/query",
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"pod": "amf-1"},
                            "value": [1708000000, "42"],
                        }
                    ],
                },
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
            timeout=5.0,
        ) as client:
            result = await client.query_prometheus("up")

        assert result["resultType"] == "vector"
        assert len(result["result"]) == 1
        assert result["result"][0]["metric"]["pod"] == "amf-1"

    @pytest.mark.asyncio
    async def test_query_prometheus_with_time(self, httpx_mock: HTTPXMock) -> None:
        """Test Prometheus query with specific timestamp."""
        httpx_mock.add_response(
            url="http://test-prometheus:9090/api/v1/query",
            json={"status": "success", "data": {"result": []}},
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            await client.query_prometheus("up", time=1708000000)

        request = httpx_mock.get_requests()[0]
        assert "time=1708000000" in str(request.url)

    @pytest.mark.asyncio
    async def test_query_prometheus_error_response(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test handling of Prometheus error response."""
        httpx_mock.add_response(
            url="http://test-prometheus:9090/api/v1/query",
            json={
                "status": "error",
                "error": "invalid query",
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            with pytest.raises(MCPQueryError, match="Prometheus error"):
                await client.query_prometheus("bad{query}")

    @pytest.mark.asyncio
    async def test_query_prometheus_range(self, httpx_mock: HTTPXMock) -> None:
        """Test Prometheus range query."""
        httpx_mock.add_response(
            url="http://test-prometheus:9090/api/v1/query_range",
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"pod": "amf-1"},
                            "values": [[1708000000, "1"], [1708000015, "2"]],
                        }
                    ],
                },
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.query_prometheus_range(
                query="up",
                start=1708000000,
                end=1708000060,
                step="15s",
            )

        assert result["resultType"] == "matrix"


class TestMCPClientLoki:
    """Tests for Loki queries via MCP client."""

    @pytest.mark.asyncio
    async def test_query_loki_success(self, httpx_mock: HTTPXMock) -> None:
        """Test successful Loki query."""
        httpx_mock.add_response(
            url="http://test-loki:3100/loki/api/v1/query_range",
            json={
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"pod": "amf-1", "namespace": "5g-core"},
                            "values": [
                                ["1708000000000000000", "ERROR: auth failed"],
                                ["1708000001000000000", "WARN: retry attempted"],
                            ],
                        }
                    ],
                },
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.query_loki(
                logql='{namespace="5g-core"}',
                start=1708000000,
                end=1708000060,
            )

        assert len(result) == 2
        assert result[0]["message"] == "ERROR: auth failed"
        assert result[0]["level"] == "ERROR"
        assert result[1]["level"] == "WARN"

    @pytest.mark.asyncio
    async def test_query_loki_extracts_log_level(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test log level extraction from messages."""
        httpx_mock.add_response(
            url="http://test-loki:3100/loki/api/v1/query_range",
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "stream": {"pod": "amf-1"},
                            "values": [
                                ["1708000000000000000", "INFO: started"],
                                ["1708000001000000000", "DEBUG: checking"],
                                ["1708000002000000000", "FATAL: crashed"],
                            ],
                        }
                    ],
                },
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.query_loki(
                logql='{pod="amf-1"}',
                start=1708000000,
                end=1708000060,
            )

        assert result[0]["level"] == "INFO"
        assert result[1]["level"] == "DEBUG"
        assert result[2]["level"] == "FATAL"


class TestMCPClientHealthChecks:
    """Tests for health check methods."""

    @pytest.mark.asyncio
    async def test_health_check_prometheus_success(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Prometheus health check success."""
        httpx_mock.add_response(
            url="http://test-prometheus:9090/api/v1/query",
            json={
                "status": "success",
                "data": {"result": [{"metric": {}, "value": [0, "1"]}]},
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_prometheus()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_prometheus_failure(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Prometheus health check failure."""
        httpx_mock.add_exception(ConnectionError("refused"))

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_prometheus()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_loki_success(self, httpx_mock: HTTPXMock) -> None:
        """Test Loki health check success."""
        httpx_mock.add_response(
            url="http://test-loki:3100/ready",
            status_code=200,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_loki()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_loki_failure(self, httpx_mock: HTTPXMock) -> None:
        """Test Loki health check failure."""
        httpx_mock.add_response(
            url="http://test-loki:3100/ready",
            status_code=503,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_loki()

        assert result is False
