"""Tests for MCP client."""

import re

import httpx
import pytest
from pytest_httpx import HTTPXMock

from triage_agent.mcp.client import MCPClient, MCPQueryError, MCPTimeoutError

PROM_QUERY_URL = re.compile(r"http://test-prometheus:9090/api/v1/query(\?.*)?$")
PROM_RANGE_URL = re.compile(r"http://test-prometheus:9090/api/v1/query_range(\?.*)?$")
LOKI_RANGE_URL = re.compile(r"http://test-loki:3100/loki/api/v1/query_range(\?.*)?$")
LOKI_READY_URL = re.compile(r"http://test-loki:3100/ready$")


class TestMCPClientPrometheus:
    """Tests for Prometheus queries via MCP client."""

    @pytest.mark.asyncio
    async def test_query_prometheus_success(self, httpx_mock: HTTPXMock) -> None:
        """Test successful Prometheus instant query returns data."""
        httpx_mock.add_response(
            url=PROM_QUERY_URL,
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
            url=PROM_QUERY_URL,
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
    async def test_query_prometheus_error_raises_mcpqueryerror(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test that Prometheus error status raises MCPQueryError."""
        httpx_mock.add_response(
            url=PROM_QUERY_URL,
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
    async def test_query_prometheus_timeout_raises_mcptimeouterror(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test that Prometheus timeout raises MCPTimeoutError."""
        httpx_mock.add_exception(
            httpx.ReadTimeout("Connection timed out"),
            url=PROM_QUERY_URL,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            with pytest.raises(MCPTimeoutError, match="timed out"):
                await client.query_prometheus("up")

    @pytest.mark.asyncio
    async def test_query_prometheus_http_error_raises_mcpqueryerror(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test that non-429 HTTP error raises MCPQueryError immediately."""
        httpx_mock.add_response(
            url=PROM_QUERY_URL,
            status_code=500,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            with pytest.raises(MCPQueryError, match="Prometheus HTTP error"):
                await client.query_prometheus("up")

    @pytest.mark.asyncio
    async def test_query_prometheus_429_retries_then_succeeds(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test that 429 rate limit triggers retry and eventually succeeds."""
        # First call: 429
        httpx_mock.add_response(
            url=PROM_QUERY_URL,
            status_code=429,
        )
        # Second call: success
        httpx_mock.add_response(
            url=PROM_QUERY_URL,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {"pod": "amf-1"}, "value": [1708000000, "1"]}],
                },
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.query_prometheus("up", max_retries=3)

        assert result["resultType"] == "vector"
        assert len(httpx_mock.get_requests()) == 2

    @pytest.mark.asyncio
    async def test_query_prometheus_429_exhausts_retries(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test that persistent 429 raises MCPQueryError after max retries."""
        for _ in range(3):
            httpx_mock.add_response(
                url=PROM_QUERY_URL,
                status_code=429,
            )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            with pytest.raises(MCPQueryError, match="429"):
                await client.query_prometheus("up", max_retries=3)

        assert len(httpx_mock.get_requests()) == 3

    @pytest.mark.asyncio
    async def test_query_prometheus_range(self, httpx_mock: HTTPXMock) -> None:
        """Test Prometheus range query."""
        httpx_mock.add_response(
            url=PROM_RANGE_URL,
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
    async def test_query_loki_returns_parsed_log_entries(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test successful Loki query returns parsed log entries with all fields."""
        httpx_mock.add_response(
            url=LOKI_RANGE_URL,
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
        assert result[0]["timestamp"] == 1708000000
        assert result[0]["pod"] == "amf-1"
        assert result[0]["labels"] == {"pod": "amf-1", "namespace": "5g-core"}

    @pytest.mark.asyncio
    async def test_query_loki_extracts_log_level(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test log level extraction from messages across all supported levels."""
        httpx_mock.add_response(
            url=LOKI_RANGE_URL,
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
                                ["1708000003000000000", "ERROR: connection lost"],
                                ["1708000004000000000", "WARN: high latency"],
                                ["1708000005000000000", "no level marker here"],
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
        assert result[3]["level"] == "ERROR"
        assert result[4]["level"] == "WARN"
        assert result[5]["level"] == "INFO"  # Default when no level found

    @pytest.mark.asyncio
    async def test_query_loki_timeout_raises_mcptimeouterror(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test that Loki timeout raises MCPTimeoutError."""
        httpx_mock.add_exception(
            httpx.ReadTimeout("Connection timed out"),
            url=LOKI_RANGE_URL,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            with pytest.raises(MCPTimeoutError, match="Loki query timed out"):
                await client.query_loki(
                    logql='{pod="amf-1"}',
                    start=1708000000,
                    end=1708000060,
                )

    @pytest.mark.asyncio
    async def test_query_loki_http_error_raises_mcpqueryerror(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test that Loki HTTP error raises MCPQueryError."""
        httpx_mock.add_response(
            url=LOKI_RANGE_URL,
            status_code=500,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            with pytest.raises(MCPQueryError, match="Loki HTTP error"):
                await client.query_loki(
                    logql='{pod="amf-1"}',
                    start=1708000000,
                    end=1708000060,
                )


class TestMCPClientHealthChecks:
    """Tests for health check methods."""

    @pytest.mark.asyncio
    async def test_health_check_prometheus_returns_true(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Prometheus health check returns True when healthy."""
        httpx_mock.add_response(
            url=PROM_QUERY_URL,
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
    async def test_health_check_prometheus_returns_false(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Prometheus health check returns False on connection error."""
        httpx_mock.add_exception(ConnectionError("refused"), url=PROM_QUERY_URL)

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_prometheus()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_prometheus_returns_false_on_empty_result(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Prometheus health check returns False when result is empty."""
        httpx_mock.add_response(
            url=PROM_QUERY_URL,
            json={
                "status": "success",
                "data": {"result": []},
            },
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_prometheus()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_loki_returns_true(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Loki health check returns True when ready."""
        httpx_mock.add_response(
            url=LOKI_READY_URL,
            status_code=200,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_loki()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_loki_returns_false(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Loki health check returns False when unavailable."""
        httpx_mock.add_response(
            url=LOKI_READY_URL,
            status_code=503,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_loki()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_loki_returns_false_on_connection_error(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Test Loki health check returns False on connection error."""
        httpx_mock.add_exception(
            ConnectionError("refused"),
            url=LOKI_READY_URL,
        )

        async with MCPClient(
            prometheus_url="http://test-prometheus:9090",
            loki_url="http://test-loki:3100",
        ) as client:
            result = await client.health_check_loki()

        assert result is False
